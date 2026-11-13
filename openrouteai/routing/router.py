"""Routing Engine.

Esquemas de enrutado:
  negotiated : PathFinder (negociación de congestión) con A* interno  [por defecto]
  ripup      : secuencial A* + rip-up and retry
  astar      : secuencial A* (sin rip-up)
  maze       : secuencial maze routing ponderado (Dijkstra)
  lee        : secuencial algoritmo de Lee (BFS)
  hadlock    : secuencial algoritmo de Hadlock (detour number)

Cada red se enruta como árbol de Steiner incremental: se parte de un pad y se
conecta cada pad restante desde todo el árbol ya construido (búsqueda
multi-origen).
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from ..analysis.netlist_analyzer import Analysis, analyze
from ..core.geometry import point_in_convex
from ..core.model import Board, Net, Pad, Track, Via, Zone
from ..i18n import tr
from .costfield import window_costs
from .grid import INF as GINF, RoutingGrid, disk
from .search import search

BLOCKED = 1e29   # cost >= BLOCKED => celda prohibida (costfield usa 1e30)

STRATEGIES = ["negotiated", "ripup", "astar", "maze", "lee", "hadlock"]
SLACK = 0.02  # mm de margen por discretización


@dataclass
class RouterConfig:
    strategy: str = "negotiated"
    pitch: Optional[float] = None          # None = automático
    max_iterations: int = 25
    via_cost_mm: float = 1.5
    turn_penalty: float = 0.25
    dir_penalty: float = 0.3
    pres_fac: float = 0.6
    pres_mult: float = 1.7
    hist_fac: float = 0.8
    nets: Optional[Set[str]] = None        # enrutado parcial: solo estas redes
    keep_existing: bool = True             # conservar pistas/vías del archivo
    planes: Dict[str, str] = field(default_factory=dict)   # capa -> red (plano de masa/alimentación)
    stitch_pitch: float = 0.0              # 0 = sin cosido de vías
    cleanup: bool = True                   # depurar pistas tras enrutar (quitar quiebros innecesarios)
    net_widths: Dict[str, float] = field(default_factory=dict)   # ancho de pista por red (mm)
    net_vias: Dict[str, Tuple[float, float]] = field(default_factory=dict)  # vía por red: (Ø, taladro) mm
    via_min: float = 0.0                   # diámetro de vía mínimo (mm), 0 = sin límite
    via_max: float = 0.0                   # diámetro de vía máximo (mm), 0 = sin límite
    time_limit: float = 600.0
    net_order: Optional[List[str]] = None  # orden forzado (lo usa el optimizador)
    engine: str = "auto"                   # auto | native | python  (núcleo de búsqueda)
    heur_weight: float = 1.0               # A* ponderado (>1 = más rápido, caminos algo más largos)
    device: str = "cpu"                    # cpu | gpu | auto  (cálculo de rejilla y costes)
    progress: Optional[Callable[[str, float], None]] = None


@dataclass
class NetRoute:
    net: str
    paths: List[List[int]] = field(default_factory=list)       # estados globales
    cells: Dict[int, np.ndarray] = field(default_factory=dict)  # clase -> índices planos
    complete: bool = False
    unconnected: int = 0
    stamps: Optional[Dict[float, np.ndarray]] = None             # huella dilatada por alcance


@dataclass
class RouteResult:
    strategy: str
    tracks: List[Track]
    vias: List[Via]
    zones: List[Zone]
    routed_nets: List[str]
    failed_nets: List[str]
    connections_total: int
    connections_routed: int
    iterations: int
    elapsed: float
    total_length: float
    via_count: int
    congestion: Optional[np.ndarray] = None
    history: Optional[np.ndarray] = None
    grid_info: Dict = field(default_factory=dict)
    log: List[str] = field(default_factory=list)

    @property
    def completion(self) -> float:
        return 100.0 * self.connections_routed / max(1, self.connections_total)

    def metrics(self) -> Dict[str, float]:
        bends = 0
        for t1, t2 in zip(self.tracks, self.tracks[1:]):
            if t1.net == t2.net and t1.end == t2.start:
                a1 = math.atan2(t1.end[1] - t1.start[1], t1.end[0] - t1.start[0])
                a2 = math.atan2(t2.end[1] - t2.start[1], t2.end[0] - t2.start[0])
                if abs(a1 - a2) > 1e-6:
                    bends += 1
        min_w = min((t.width for t in self.tracks), default=0)
        cost = self.via_count * 0.02 + self.total_length * 0.001 + (0.5 if min_w and min_w < 0.15 else 0)
        return {
            "completado_%": round(self.completion, 2),
            "conexiones": f"{self.connections_routed}/{self.connections_total}",
            "longitud_total_mm": round(self.total_length, 2),
            "vias": self.via_count,
            "codos": bends,
            "tiempo_s": round(self.elapsed, 2),
            "iteraciones": self.iterations,
            "congestion_max": float(self.congestion.max()) if self.congestion is not None and self.congestion.size else 0.0,
            "coste_fabricacion_rel": round(cost, 3),
            "motor": f"{self.grid_info.get('engine', '?')} / {self.grid_info.get('device', 'cpu')}",
        }


def auto_pitch(board: Board) -> float:
    d = board.netclasses.get("Default")
    w = d.track_width if d else 0.25
    c = max(d.clearance if d else 0.2, board.rules.min_clearance)
    p = (w + c) / 3.0
    return float(min(0.25, max(0.08, round(p / 0.01) * 0.01)))


class Router:
    def __init__(self, board: Board, cfg: Optional[RouterConfig] = None, analysis: Optional[Analysis] = None):
        self.board = board
        self.cfg = cfg or RouterConfig()
        if self.cfg.strategy not in STRATEGIES:
            raise ValueError(f"Estrategia desconocida: {self.cfg.strategy}")
        self.an = analysis or analyze(board)
        self.log: List[str] = []

    # ---------------------------------------------------------------- utils
    def _p(self, msg: str, frac: float):
        self.log.append(msg)
        if self.cfg.progress:
            self.cfg.progress(msg, frac)

    def _net_params(self, name: str):
        b = self.board
        nc = b.netclass_of(name)
        cfg = self.cfg
        w = max(cfg.net_widths.get(name, nc.track_width), b.rules.min_track_width)
        vd = max(nc.via_diameter, b.rules.min_via_diameter)
        if cfg.via_min > 0:
            vd = max(vd, cfg.via_min)
        if cfg.via_max > 0:
            vd = min(vd, max(cfg.via_max, b.rules.min_via_diameter))
        vdr = max(nc.via_drill, b.rules.min_via_drill)
        # anillo mínimo de 0,1 mm por lado si la vía se ha reducido
        vdr = max(b.rules.min_via_drill, min(vdr, vd - 0.2)) if vd - vdr < 0.2 else vdr
        if name in cfg.net_vias:              # elección explícita para la red: manda sobre clase y límites
            vd, vdr = cfg.net_vias[name]
        c = max(nc.clearance, b.rules.min_clearance, b.netclasses["Default"].clearance if "Default" in b.netclasses else 0)
        return w, vd, vdr, c

    # ---------------------------------------------------------------- main
    def run(self) -> RouteResult:
        t0 = time.time()
        cfg, b = self.cfg, self.board
        sel = cfg.nets

        # pistas previas: fijas (archivo) o resultados anteriores
        if sel:
            new_tracks = [t for t in b.tracks if t.fixed or t.net not in sel]
            new_vias = [v for v in b.vias if v.fixed or v.net not in sel]
            for t in new_tracks:
                if not t.fixed:
                    t.fixed = True
                    t._was_routed = True
            for v in new_vias:
                if not v.fixed:
                    v.fixed = True
                    v._was_routed = True
            b.tracks, b.vias = new_tracks, new_vias
        else:
            b.tracks = [t for t in b.tracks if t.fixed and cfg.keep_existing]
            b.vias = [v for v in b.vias if v.fixed and cfg.keep_existing]

        pitch = cfg.pitch or auto_pitch(b)
        self._p(tr("Construyendo rejilla (paso {p} mm)...", p=f"{pitch:.3f}"), 0.02)
        extra = max([w / 2 for w in cfg.net_widths.values()] + [d / 2 for d, _ in cfg.net_vias.values()] +
                    [cfg.via_min / 2, cfg.via_max / 2, 0.0])
        g = self.grid = RoutingGrid(b, pitch, min_radius=extra)
        self._p(tr("Rejilla {d} = {n} celdas", d=f"{g.L}x{g.ny}x{g.nx}", n=f"{g.L * g.ny * g.nx:,}"), 0.05)

        # planos
        self.plane_layer_of: Dict[str, List[int]] = {}
        for lname, net in cfg.planes.items():
            if lname in g.layers and net in b.nets:
                li = g.layers.index(lname)
                self.plane_layer_of.setdefault(net, []).append(li)
                if g.L >= 4:
                    g.reserved[li] = True

        order = list(cfg.net_order or self.an.order)
        order = [n for n in order if n in b.nets and len(b.nets[n].pads) >= 2]
        if sel:
            order = [n for n in order if n in sel]
        self.routes: Dict[str, NetRoute] = {}
        self.via_cost_cells = cfg.via_cost_mm / pitch
        # alcances (semiancho + clearance) que consultarán las redes: la huella de
        # cada ruta se dilata una vez por alcance al confirmarla (ver _stamps)
        for n in order:
            w, vd, vdr, c = self._net_params(n)
            g.ensure_stamp(self._reach(w / 2 + c + SLACK))
            g.ensure_stamp(self._reach(vd / 2 + c + SLACK))
            g.class_index(w / 2)          # se crean aquí: los hilos solo leen
            g.class_index(vd / 2)
        self._scratch = np.zeros(g.L * g.ny * g.nx, dtype=np.uint8)
        self.gpu = None
        from . import gpu as gpumod
        dev, xp, why = gpumod.resolve(cfg.device, g.L * g.ny * g.nx)
        if xp is not None:
            try:
                self.gpu = gpumod.GpuGrid(g, xp)
                self._p(tr("Dispositivo: GPU {w} — {m} MB en la GPU", w=why, m=f"{self.gpu.nbytes() / 2 ** 20:.0f}"), 0.05)
            except Exception as exc:   # memoria de GPU insuficiente, etc.
                self.gpu = None
                self._p(tr("GPU no utilizable ({e}); se usa la CPU", e=f"{type(exc).__name__}: {exc}"), 0.05)
        else:
            self._p(tr("Dispositivo: CPU ({w})", w=why), 0.05)
        self.device_used = "gpu" if self.gpu is not None else "cpu"
        self.native = None
        self._tls = threading.local()
        if cfg.engine in ("auto", "native"):
            from . import native
            if native.available():
                self.native = native.NativeSearcher()
                self._tls.native = self.native
            elif cfg.engine == "native":
                raise RuntimeError("Núcleo nativo no disponible: " + native.status())
        self._p(tr("Motor de búsqueda: {m}", m=tr("nativo (C)") if self.native else "Python"), 0.05)

        # capa preferida por dirección: capas pares horizontales, impares verticales
        dp = cfg.dir_penalty
        self.layer_mult = []
        for l in range(g.L):
            if l % 2 == 0:
                self.layer_mult.append([1, 1, 1 + dp, 1 + dp] + [1 + dp / 2] * 4)
            else:
                self.layer_mult.append([1 + dp, 1 + dp, 1, 1] + [1 + dp / 2] * 4)
        self.layer_mult_np = np.array(self.layer_mult, dtype=np.float32)

        iterations = 1
        if cfg.strategy == "negotiated":
            iterations = self._pathfinder(order, t0)
        else:
            # Lee, Hadlock y maze son núcleos de búsqueda; el marco de rip-up and retry
            # se aplica también a ellos. "astar" se deja sin rip-up como referencia.
            self._sequential(order, t0, ripup=(cfg.strategy != "astar"))

        if cfg.stitch_pitch > 0:
            self._stitch()
        if cfg.cleanup:
            self._cleanup(order)

        # ---- geometría de salida
        tracks, vias = self._all_geometry()
        zones = []
        for lname, net in cfg.planes.items():
            if net in b.nets and lname in g.layers:
                poly = b.outline or [(b.bbox()[0], b.bbox()[1]), (b.bbox()[2], b.bbox()[1]),
                                     (b.bbox()[2], b.bbox()[3]), (b.bbox()[0], b.bbox()[3])]
                zones.append(Zone(net=net, layers=[lname], polygon=list(poly)))
        if zones:
            tracks, vias = self._fill_and_check_planes(zones, tracks, vias, order)

        b.tracks += tracks
        b.vias += vias
        conn_total = conn_routed = 0
        routed, failed = [], []
        for name in order:
            n_conn = len(b.nets[name].pads) - 1
            conn_total += n_conn
            rt = self.routes.get(name)
            if rt and rt.complete:
                routed.append(name)
                conn_routed += n_conn
            else:
                failed.append(name)
                conn_routed += max(0, n_conn - (rt.unconnected if rt else n_conn))
        res = RouteResult(
            strategy=cfg.strategy, tracks=tracks, vias=vias, zones=zones,
            routed_nets=routed, failed_nets=failed,
            connections_total=conn_total, connections_routed=conn_routed,
            iterations=iterations, elapsed=time.time() - t0,
            total_length=sum(t.length for t in tracks), via_count=len(vias),
            congestion=g.congestion_map(), history=g.hist.copy(),
            grid_info={"pitch": pitch, "x0": g.x0, "y0": g.y0, "nx": g.nx, "ny": g.ny, "layers": g.layers,
                       "engine": "nativo" if self.native else "python", "device": self.device_used},
            log=self.log,
        )
        self._p(tr("Terminado: {c}% en {t} s", c=f"{res.completion:.1f}", t=f"{res.elapsed:.1f}"), 1.0)
        return res

    # ------------------------------------------------------------ esquemas
    def _sequential(self, order: List[str], t0: float, ripup: bool):
        cfg = self.cfg
        mode = {"astar": "astar", "ripup": "astar", "maze": "maze", "lee": "lee", "hadlock": "hadlock"}[cfg.strategy]
        queue = list(order)
        rips: Dict[str, int] = {}
        done = 0
        total = len(queue)
        while queue:
            if time.time() - t0 > cfg.time_limit:
                self._p(tr("Límite de tiempo alcanzado"), 0.95)
                break
            name = queue.pop(0)
            rt = self._route_net(name, mode=mode, pres=None)
            if rt.complete or not ripup:
                self._commit(rt)
                done += 1
                self._p(f"[{done}/{total}] {name}: " + ("OK" if rt.complete else tr("incompleta")), 0.05 + 0.9 * done / max(1, total + len(queue)))
                continue
            # rip-up and retry: ruta permitiendo cruces muy penalizados
            trial = self._route_net(name, mode="astar", pres=40.0)
            blockers = self._blocking_nets(trial) if trial.complete else []
            if trial.complete and blockers and rips.get(name, 0) < 3:
                rips[name] = rips.get(name, 0) + 1
                for bn in blockers:
                    self._uncommit(bn)
                    if bn not in queue:
                        queue.append(bn)
                self._commit(trial)
                done += 1
                self._p(tr("{name}: rip-up de {b}", name=name, b=", ".join(blockers[:4])), 0.05 + 0.9 * done / max(1, total + len(queue)))
            elif trial.complete and not blockers:
                self._commit(trial)        # la ruta de prueba ya es legal
                done += 1
            else:
                self._commit(rt)
                done += 1

    def _pathfinder(self, order: List[str], t0: float) -> int:
        cfg, g = self.cfg, self.grid
        pres = cfg.pres_fac
        to_route = list(order)
        it = 0
        conflicts: Dict[str, np.ndarray] = {}
        for it in range(1, cfg.max_iterations + 1):
            for k, name in enumerate(to_route):
                if name in self.routes:
                    self._uncommit(name)
                rt = self._route_net(name, mode="astar", pres=pres)
                self._commit(rt)
                if time.time() - t0 > cfg.time_limit:
                    break
            conflicts = self._find_conflicts()
            n_conf = len(conflicts)
            self._p(tr("Iteración {it}: {n} redes en conflicto (factor presente {p})", it=it, n=n_conf, p=f"{pres:.2f}"),
                    0.05 + 0.85 * it / cfg.max_iterations)
            if n_conf == 0 or time.time() - t0 > cfg.time_limit:
                break
            for name, cells in conflicts.items():
                np.add.at(g.hist.reshape(-1), cells, cfg.hist_fac)
            if self.gpu is not None:
                self.gpu.sync_hist()
            pres *= cfg.pres_mult
            to_route = [n for n in order if n in conflicts]

        # limpieza: retirar redes en conflicto hasta que no quede ninguno
        guard = 0
        while conflicts and guard < 200:
            guard += 1
            worst = max(conflicts, key=lambda n: len(conflicts[n]))
            self._uncommit(worst)
            self.routes[worst] = NetRoute(worst, unconnected=len(self.board.nets[worst].pads) - 1)
            conflicts = self._find_conflicts()
        # reintento estricto de las fallidas
        failed = [n for n in order if not (self.routes.get(n) and self.routes[n].complete)]
        for name in failed:
            if name in self.routes and self.routes[name].paths:
                self._uncommit(name)
            rt = self._route_net(name, mode="astar", pres=None)
            self._commit(rt)
        if failed:
            self._p(tr("Reintento estricto de {n} redes", n=len(failed)), 0.93)
        return it

    # ------------------------------------------------------------ una red
    def _window_arrays(self, name: str, bb_cells, margin_cells: int, pres: Optional[float], extra_target_layers=()):
        """Prepara los costes de una ventana para la red ``name`` (en CPU o GPU)."""
        g, b = self.grid, self.board
        w, vd, vdr, c = self._net_params(name)
        hw, rv = w / 2, vd / 2
        nid = g.net_ids.get(name, -999)
        iy0 = max(0, bb_cells[0] - margin_cells)
        iy1 = min(g.ny, bb_cells[1] + margin_cells + 1)
        ix0 = max(0, bb_cells[2] - margin_cells)
        ix1 = min(g.nx, bb_cells[3] + margin_cells + 1)
        sl = (slice(None), slice(iy0, iy1), slice(ix0, ix1))
        kw = dict(nid=nid, hw=hw, rv=rv, c=c, ec=b.rules.copper_edge_clearance, slack=SLACK,
                  pres=pres, via_cost_cells=self.via_cost_cells)
        rt_, rv_ = self._reach(hw + c + SLACK), self._reach(rv + c + SLACK)
        if self.gpu is not None:
            cost, vcost = self.gpu.window(sl, extra_layers=extra_target_layers, reach_t=rt_, reach_v=rv_, **kw)
        else:
            ys, xs = sl[1], sl[2]
            cost, vcost = window_costs(
                np, g.d1[sl], g.d2[sl], g.o1[sl], g.dedge[ys, xs], g.keepout_track[sl], g.keepout_via[ys, xs],
                g.stamp[rt_][sl], g.stamp[rv_][sl], g.hist[sl],
                blocked_layers=[int(i) for i in np.flatnonzero(g.reserved)] + list(extra_target_layers), **kw)
        return (iy0, iy1, ix0, ix1), cost, vcost, None

    def _route_net(self, name: str, mode: str, pres: Optional[float]) -> NetRoute:
        g, b = self.grid, self.board
        net = b.nets[name]
        rt = NetRoute(name)
        pads = [p for p in net.pads if id(p) in g.pad_cells]
        if len(pads) < 2:
            rt.complete = True
            return rt
        plane_layers = self.plane_layer_of.get(name, [])
        if plane_layers:
            return self._route_plane_net(name, pads, plane_layers, mode, pres)

        tree: Set[int] = set()
        connected = [False] * len(pads)
        # semilla: primer pad + pistas fijas existentes de la red
        connected[0] = True
        tree.update(self._pad_targets(pads[0]))
        tree.update(self._fixed_cells(name))
        self._mark_touching(pads, connected, tree)

        while not all(connected):
            # pad pendiente más cercano a uno ya conectado
            best = None
            for i, p in enumerate(pads):
                if connected[i]:
                    continue
                for j, q in enumerate(pads):
                    if connected[j]:
                        d = abs(p.x - q.x) + abs(p.y - q.y)
                        if best is None or d < best[0]:
                            best = (d, i, j)
            _, ti, si = best
            path = None
            tcells = g.pad_cells[id(pads[ti])]
            span_cells = int(best[0] / g.pitch)
            margins = (max(20, span_cells // 3), max(60, span_cells), 10 ** 6)
            for margin in margins:
                bb = self._cells_bbox(np.concatenate([tcells, g.pad_cells[id(pads[si])]]))
                path = self._search_window(name, bb, margin, tree, set(self._pad_targets(pads[ti])), mode, pres, pads)
                if path is not None or margin >= max(g.nx, g.ny):
                    break
            if path is None:
                # no se pudo; marcar como conectado para seguir con el resto
                connected[ti] = True
                rt.unconnected += 1
                continue
            rt.paths.append(path)
            tree.update(path)
            connected[ti] = True
            self._mark_touching(pads, connected, tree)
        rt.complete = rt.unconnected == 0
        rt.cells = self._route_cells(name, rt.paths)
        return rt

    def _route_plane_net(self, name, pads, plane_layers, mode, pres) -> NetRoute:
        """Red conectada por un plano: cada pad baja al plano con una vía corta."""
        g = self.grid
        rt = NetRoute(name)
        for p in pads:
            lays = g.pad_layers(p)
            if any(l in lays for l in plane_layers):
                continue
            pc = np.array(self._pad_targets(p), dtype=np.int64)
            bb = self._cells_bbox(pc)
            found = None
            for margin in (int(2.0 / g.pitch), int(6.0 / g.pitch)):
                (iy0, iy1, ix0, ix1), cost, vcost, vlo = self._window_arrays(name, bb, margin, pres, extra_target_layers=plane_layers)
                H, W = iy1 - iy0, ix1 - ix0
                # destinos: celdas del plano donde cabe una vía
                ok = vcost < BLOCKED
                targets = set()
                for li in plane_layers:
                    ys, xs = np.nonzero(ok)
                    base = li * W * H
                    targets.update((base + ys * W + xs).tolist())
                    cost[li][ok] = 1.0
                own = self._own_pad_overrides([p], cost, vcost, (iy0, iy1, ix0, ix1))
                src = self._to_win_arr(pc, (iy0, iy1, ix0, ix1))
                path = self._run_search(W, H, cost, vcost, src, targets,
                                        "astar" if mode in ("lee", "hadlock") else mode)
                if path:
                    found = self._to_glob_list(path, (iy0, iy1, ix0, ix1))
                    break
            if found:
                rt.paths.append(found)
            else:
                rt.unconnected += 1
        rt.complete = rt.unconnected == 0
        rt.cells = self._route_cells(name, rt.paths)
        return rt

    def _search_window(self, name, bb, margin, tree, targets_glob, mode, pres, pads):
        g = self.grid
        win, cost, vcost, vlo = self._window_arrays(name, bb, margin, pres)
        iy0, iy1, ix0, ix1 = win
        H, W = iy1 - iy0, ix1 - ix0
        self._own_pad_overrides(pads, cost, vcost, win)
        src = self._to_win_arr(tree, win)
        cf = cost.reshape(-1)
        tgt = self._to_win_arr(targets_glob, win)
        tgt = tgt[cf[tgt] < BLOCKED]
        if not len(src) or not len(tgt):
            return None
        path = self._run_search(W, H, cost, vcost, src, tgt, mode)
        if not path:
            return None
        return self._to_glob_list(path, win)

    def _run_search(self, W, H, cost, vcost, src, targets, mode):
        L = self.grid.L
        if len(targets) == 0 or len(src) == 0:
            return None
        if self.native is not None:
            ns = getattr(self._tls, "native", None)
            if ns is None:
                from . import native as _nat
                ns = self._tls.native = _nat.NativeSearcher()
            tl = np.asarray(targets, dtype=np.int64) if not isinstance(targets, set) else \
                np.fromiter(targets, dtype=np.int64, count=len(targets))
            tm = np.zeros(L * W * H, dtype=np.uint8)
            tm[tl] = 1
            rem = tl % (W * H)
            ys, xs = rem // W, rem % W
            tbox = (int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()))
            return ns.search(W, H, L, cost, vcost, src, tm, mode, self.layer_mult_np,
                                      self.cfg.turn_penalty, tbox, heur_weight=self.cfg.heur_weight)
        inf = float("inf")
        cl = np.where(cost >= BLOCKED, inf, cost).ravel().tolist()
        vl = np.where(vcost >= BLOCKED, inf, vcost).ravel().tolist()
        return search(W, H, L, cl, vl,
                      [int(v) for v in src], {int(v) for v in targets},
                      mode=mode, layer_mult=self.layer_mult, turn_penalty=self.cfg.turn_penalty)

    @staticmethod
    def _reach(v: float) -> float:
        return round(v, 4)

    def _stamps(self, rt: NetRoute) -> Dict[float, np.ndarray]:
        """Celdas (índices planos) a menos de alcance+radio del cobre de la ruta, por alcance."""
        g = self.grid
        out = {}
        if not rt.cells:
            return out
        WH = g.ny * g.nx
        scr = self._scratch
        for Q in g.stamp:
            lo, hi = None, None
            for k, cells in rt.cells.items():
                if not len(cells):
                    continue
                dy, dx = g.disk_offsets((Q + g.radii[k]) / g.pitch)
                l, rem = np.divmod(cells.astype(np.int64), WH)
                y, x = np.divmod(rem, g.nx)
                for a in range(0, len(cells), 20000):
                    Y = y[a:a + 20000, None] + dy[None, :]
                    X = x[a:a + 20000, None] + dx[None, :]
                    ok = (Y >= 0) & (Y < g.ny) & (X >= 0) & (X < g.nx)
                    flat = (l[a:a + 20000, None] * WH + Y * g.nx + X)[ok]
                    if len(flat):
                        scr[flat] = 1
                        fmn, fmx = int(flat.min()), int(flat.max())
                        lo = fmn if lo is None else min(lo, fmn)
                        hi = fmx if hi is None else max(hi, fmx)
            if lo is None:
                out[Q] = np.zeros(0, dtype=np.int32)
                continue
            # solo se recorre el tramo del búfer que ha tocado la ruta
            idx = (np.flatnonzero(scr[lo:hi + 1]) + lo).astype(np.int32)
            scr[idx] = 0
            out[Q] = idx
        return out

    def _own_pad_overrides(self, pads, cost, vcost, win):
        """La celda central de cada pad propio siempre es transitable (salida del pad).

        El resto de celdas del pad solo se usan si respetan el clearance con el
        cobre ajeno; así las pistas salen por el centro en pads de paso fino.
        """
        g = self.grid
        iy0, iy1, ix0, ix1 = win
        WHw = (iy1 - iy0) * (ix1 - ix0)
        cf = cost.reshape(-1)
        vf = vcost.reshape(-1)
        F = cost.dtype.type
        centers, th_cells = [], []
        for p in pads:
            cells = g.pad_cells.get(id(p))
            if cells is None:
                continue
            iy, ix = g.to_cell(p.x, p.y)
            cs = [g.idx(l, iy, ix) for l in g.pad_layers(p)]
            centers += cs
            if p.kind == "thru_hole":
                th_cells.append(np.asarray(cs, dtype=np.int64))
                th_cells.append(cells)
        cw = self._to_win_arr(np.asarray(centers, dtype=np.int64), win)
        cf[cw[cf[cw] >= BLOCKED]] = F(1.0)
        if th_cells:
            tw = self._to_win_arr(np.concatenate(th_cells), win)
            tw = tw[cf[tw] < BLOCKED]
            vf[tw % WHw] = F(0.05)   # cambio de capa dentro del pad pasante: gratis

    def _pad_targets(self, pad) -> List[int]:
        g = self.grid
        iy, ix = g.to_cell(pad.x, pad.y)
        return list(set(int(c) for c in g.pad_cells[id(pad)]) | {g.idx(l, iy, ix) for l in g.pad_layers(pad)})

    # ----------------------------------------------------------- auxiliares
    def _to_win(self, s: int, win):
        g = self.grid
        iy0, iy1, ix0, ix1 = win
        l, iy, ix = g.unravel(s)
        if iy0 <= iy < iy1 and ix0 <= ix < ix1:
            return (l * (iy1 - iy0) + (iy - iy0)) * (ix1 - ix0) + (ix - ix0)
        return None

    def _to_win_arr(self, cells, win) -> np.ndarray:
        """Versión vectorizada de _to_win: devuelve solo las celdas dentro de la ventana."""
        g = self.grid
        iy0, iy1, ix0, ix1 = win
        a = np.fromiter(cells, dtype=np.int64) if not isinstance(cells, np.ndarray) else cells.astype(np.int64)
        if not len(a):
            return a
        l, rem = np.divmod(a, g.ny * g.nx)
        iy, ix = np.divmod(rem, g.nx)
        ok = (iy >= iy0) & (iy < iy1) & (ix >= ix0) & (ix < ix1)
        return (l[ok] * (iy1 - iy0) + (iy[ok] - iy0)) * (ix1 - ix0) + (ix[ok] - ix0)

    def _to_glob_list(self, path, win) -> List[int]:
        iy0, iy1, ix0, ix1 = win
        H, W = iy1 - iy0, ix1 - ix0
        a = np.asarray(path, dtype=np.int64)
        l, r = np.divmod(a, W * H)
        y, x = np.divmod(r, W)
        g = self.grid
        return ((l * g.ny + iy0 + y) * g.nx + ix0 + x).tolist()

    def _to_glob(self, s: int, win) -> int:
        iy0, iy1, ix0, ix1 = win
        H, W = iy1 - iy0, ix1 - ix0
        l, r = divmod(s, W * H)
        y, x = divmod(r, W)
        return self.grid.idx(l, iy0 + y, ix0 + x)

    def _cells_bbox(self, cells) -> Tuple[int, int, int, int]:
        g = self.grid
        rem = np.asarray(cells) % (g.ny * g.nx)
        ys, xs = rem // g.nx, rem % g.nx
        return int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())

    def _mark_touching(self, pads, connected, tree):
        g = self.grid
        for i, p in enumerate(pads):
            if not connected[i] and any(c in tree for c in self._pad_targets(p)):
                connected[i] = True

    def _fixed_cells(self, name: str) -> Set[int]:
        g, b = self.grid, self.board
        out = set()
        for t in b.tracks:
            if t.net != name or t.layer not in g.layers:
                continue
            l = g.layers.index(t.layer)
            n = max(1, int(t.length / (g.pitch / 2)))
            for k in range(n + 1):
                x = t.start[0] + (t.end[0] - t.start[0]) * k / n
                y = t.start[1] + (t.end[1] - t.start[1]) * k / n
                iy, ix = g.to_cell(x, y)
                out.add(g.idx(l, iy, ix))
        for v in b.vias:
            if v.net == name:
                iy, ix = g.to_cell(v.x, v.y)
                for l in range(g.L):
                    out.add(g.idx(l, iy, ix))
        return out

    def _route_cells(self, name: str, paths: List[List[int]]) -> Dict[int, np.ndarray]:
        g = self.grid
        w, vd, vdr, c = self._net_params(name)
        kt = g.class_index(w / 2)
        kv = g.class_index(vd / 2)
        tcells, vcells = set(), set()
        WH = g.ny * g.nx
        for path in paths:
            for i, s in enumerate(path):
                tcells.add(s)
                if i and path[i - 1] // WH != s // WH:
                    if not self._in_own_th_pad(name, s):
                        r = s % WH
                        for l in range(g.L):
                            vcells.add(l * WH + r)
        out = {kt: np.array(sorted(tcells), dtype=np.int64)}
        if vcells:
            out[kv] = np.array(sorted(vcells), dtype=np.int64) if kv != kt else \
                np.array(sorted(tcells | vcells), dtype=np.int64)
        return out

    def _in_own_th_pad(self, name: str, s: int) -> bool:
        g = self.grid
        if not hasattr(self, "_th_cells"):
            self._th_cells = {}
        cache = self._th_cells.get(name)
        if cache is None:
            cache = set()
            for p in self.board.nets[name].pads:
                if p.kind == "thru_hole" and id(p) in g.pad_cells:
                    cache.update(int(c) for c in g.pad_cells[id(p)])
            self._th_cells[name] = cache
        return s in cache

    def _commit(self, rt: NetRoute):
        self.routes[rt.net] = rt
        self.grid.add_route(rt.cells, +1)
        if rt.stamps is None:
            rt.stamps = self._stamps(rt)
        self.grid.add_stamps(rt.stamps, +1)
        if self.gpu is not None:
            self.gpu.add_stamps(rt.stamps, +1)

    def _uncommit(self, name: str):
        rt = self.routes.pop(name, None)
        if rt:
            self.grid.add_route(rt.cells, -1)
            if rt.stamps:
                self.grid.add_stamps(rt.stamps, -1)
                if self.gpu is not None:
                    self.gpu.add_stamps(rt.stamps, -1)

    def _conflict_cells(self, name: str) -> np.ndarray:
        """Celdas del eje de ``name`` demasiado cerca de cobre enrutado de otras redes.

        Cada ruta confirmada suma 1 a su huella dilatada; una celda propia con un
        valor > 1 significa que otra ruta está demasiado cerca.
        """
        g = self.grid
        rt = self.routes[name]
        if not rt.paths:
            return np.zeros(0, dtype=np.int64)
        w, vd, vdr, c = self._net_params(name)
        kt = g.class_index(w / 2)
        bad = []
        for k, cells in rt.cells.items():
            r = w / 2 if k == kt else vd / 2
            st = g.stamp[self._reach(r + c + SLACK)].reshape(-1)
            hit = st[cells] > 1
            if hit.any():
                bad.append(cells[hit])
        return np.concatenate(bad) if bad else np.zeros(0, dtype=np.int64)

    def _find_conflicts(self) -> Dict[str, np.ndarray]:
        out = {}
        for name in list(self.routes):
            bad = self._conflict_cells(name)
            if len(bad):
                out[name] = bad
        return out

    def _blocking_nets(self, trial: NetRoute) -> List[str]:
        g = self.grid
        self._commit(trial)
        try:
            bad = set(self._conflict_cells(trial.net).tolist())
        finally:
            self._uncommit(trial.net)
        if not bad:
            return []
        # redes cuyo cobre está cerca de las celdas conflictivas
        w, vd, vdr, c = self._net_params(trial.net)
        R = int(math.ceil((max(g.radii) * 2 + c + SLACK) / g.pitch)) + 1
        WH = g.ny * g.nx
        near = set()
        for s in bad:
            l, iy, ix = g.unravel(s)
            for dy in range(-R, R + 1):
                for dx in range(-R, R + 1):
                    yy, xx = iy + dy, ix + dx
                    if 0 <= yy < g.ny and 0 <= xx < g.nx:
                        near.add(l * WH + yy * g.nx + xx)
        out = []
        for name, rt in self.routes.items():
            for cells in rt.cells.values():
                if any(int(c) in near for c in cells):
                    out.append(name)
                    break
        return out

    # ---------------------------------------------------------- planos
    def _all_geometry(self):
        tracks, vias = [], []
        for name, rt in self.routes.items():
            t, v = self._to_geometry(rt)
            tracks += t
            vias += v
        return tracks, vias

    def _fill_and_check_planes(self, zones, tracks, vias, order):
        """Rellena los planos y comprueba que conectan de verdad sus pads. Si el plano
        queda partido (p. ej. pistas de otras redes en la misma capa), la red se completa
        con pistas normales y se vuelve a rellenar."""
        from ..drc.drc import unconnected
        from ..zones_fill import fill_zone
        b = self.board
        base_t, base_v, base_z = list(b.tracks), list(b.vias), list(b.zones)

        def fill_and_test(tr, vi):
            b.tracks, b.vias = base_t + tr, base_v + vi
            b.zones = base_z + zones
            for z in zones:
                z.filled = fill_zone(b, z)
            broken = {n for n, _, _ in unconnected(b)} & {z.net for z in zones}
            b.tracks, b.vias, b.zones = base_t, base_v, base_z
            return broken

        self._p(tr("Rellenando planos..."), 0.985)
        broken = fill_and_test(tracks, vias)
        if broken:
            for net in sorted(broken):
                self._p(tr("El plano no conecta todos los pads de {net}: se completa con pistas", net=net), 0.99)
                self.plane_layer_of.pop(net, None)
                self._uncommit(net)
                rt = self._route_net(net, mode="astar", pres=None)
                self._commit(rt)
            tracks, vias = self._all_geometry()
            broken = fill_and_test(tracks, vias)
            if broken:
                self._p(tr("Sin completar tras el plano: {n}", n=", ".join(sorted(broken))), 0.99)
        return tracks, vias

    # ---------------------------------------------------------- depuración
    def _cleanup(self, order: List[str]):
        """Depura las pistas: sustituye cadenas de quiebros por la conexión octilineal
        más corta (recta + 45°) cuando es legal frente al cobre fijo y al de las demás
        redes. Las vías y los extremos de cada tramo no se mueven, así que la conectividad
        se conserva y el resultado nunca es más largo."""
        g = self.grid
        WH = g.ny * g.nx
        bends_before = bends_after = 0
        for name in order:
            rt = self.routes.get(name)
            if rt is None or not rt.paths:
                continue
            allc = np.concatenate([np.asarray(p, dtype=np.int64) for p in rt.paths])
            bb = self._cells_bbox(allc)
            self._uncommit(name)
            win, cost, vcost, _ = self._window_arrays(name, bb, 1, None)
            pads = [p for p in self.board.nets[name].pads if id(p) in g.pad_cells]
            self._own_pad_overrides(pads, cost, vcost, win)
            iy0, iy1, ix0, ix1 = win
            # puntos de unión del árbol: extremos de cada camino (las ramas nacen en
            # puntos intermedios de otros caminos), que nunca se pueden saltar
            anchors = set()
            for path in rt.paths:
                anchors.add(path[0])
                anchors.add(path[-1])
            new_paths = []
            for path in rt.paths:
                # tramos en una misma capa (las vías separan tramos)
                runs, cur = [], [path[0]]
                for s_ in path[1:]:
                    if s_ // WH != cur[-1] // WH:
                        runs.append(cur)
                        cur = [s_]
                    else:
                        cur.append(s_)
                runs.append(cur)
                out = []
                for run in runs:
                    l = run[0] // WH
                    pts = [divmod(s_ % WH, g.nx) for s_ in run]          # (y, x) globales
                    bends_before += _count_bends(pts)
                    # se trocea el tramo en los puntos de unión y se depura cada trozo
                    cuts = [0] + [k for k in range(1, len(run) - 1) if run[k] in anchors] + [len(run) - 1]
                    pts2 = [pts[0]]
                    for a_, b_ in zip(cuts, cuts[1:]):
                        pts2.extend(_pull_tight(pts[a_:b_ + 1], cost[l], iy0, ix0)[1:])
                    bends_after += _count_bends(pts2)
                    out.extend(l * WH + y * g.nx + x for y, x in pts2)
                new_paths.append(out)
            rt.paths = new_paths
            rt.cells = self._route_cells(name, rt.paths)
            rt.stamps = None
            self._commit(rt)
        self._p(tr("Depuración: codos {a} → {b}", a=bends_before, b=bends_after), 0.98)

    # ---------------------------------------------------------- cosido de vías
    def _stitch(self):
        g, b, cfg = self.grid, self.board, self.cfg
        by_net: Dict[str, List[int]] = {}
        for lname, net in cfg.planes.items():
            if lname in g.layers:
                by_net.setdefault(net, []).append(g.layers.index(lname))
        for net, lays in by_net.items():
            if len(lays) < 2:
                continue
            w, vd, vdr, c = self._net_params(net)
            step = max(1, int(cfg.stitch_pitch / g.pitch))
            win, cost, vcost, vlo = self._window_arrays(net, (0, g.ny - 1, 0, g.nx - 1), 0, None)
            extra = int(math.ceil(1.0 / g.pitch))  # alejar de pads/pistas 1 mm extra
            placed = []
            for iy in range(step // 2, g.ny, step):
                for ix in range(step // 2, g.nx, step):
                    y0, y1 = max(0, iy - extra), min(g.ny, iy + extra + 1)
                    x0, x1 = max(0, ix - extra), min(g.nx, ix + extra + 1)
                    if np.all(vcost[y0:y1, x0:x1] < BLOCKED):
                        placed.append([g.idx(0, iy, ix), g.idx(g.L - 1, iy, ix)])
            rt = self.routes.get(net) or NetRoute(net, complete=True)
            rt.paths += placed
            self._uncommit(net)
            rt.cells = self._route_cells(net, rt.paths)
            rt.stamps = None
            self._commit(rt)
            self._p(tr("Cosido: {n} vías de {net}", n=len(placed), net=net), 0.97)

    # ---------------------------------------------------------- geometría
    def _to_geometry(self, rt: NetRoute) -> Tuple[List[Track], List[Via]]:
        g, b = self.grid, self.board
        w, vd, vdr, c = self._net_params(rt.net)
        WH = g.ny * g.nx
        pads = b.nets[rt.net].pads
        cell_pad: Dict[int, Pad] = {}
        for p in pads:
            if id(p) in g.pad_cells:
                for cc in self._pad_targets(p):
                    cell_pad[cc] = p
        tracks: List[Track] = []
        vias: List[Via] = []
        for path in rt.paths:
            runs: List[Tuple[int, List[Tuple[float, float]]]] = []
            for i, s in enumerate(path):
                l, iy, ix = g.unravel(s)
                pt = g.cell_xy(iy, ix)
                if i == 0 or l != path[i - 1] // WH:
                    if i > 0 and not self._in_own_th_pad(rt.net, s):
                        # el router reserva espacio en todas las capas: vía pasante
                        vias.append(Via(pt[0], pt[1], vd, vdr, (g.layers[0], g.layers[-1]), rt.net, "through"))
                    runs.append((l, [pt]))
                else:
                    runs[-1][1].append(pt)
            # extender los extremos al centro del pad
            if runs:
                for end in (0, -1):
                    s = path[end]
                    p = cell_pad.get(s)
                    if p is not None:
                        lay, pts = runs[end]
                        if end == 0:
                            pts.insert(0, p.pos)
                        else:
                            pts.append(p.pos)
            for l, pts in runs:
                pts = _simplify(pts)
                if len(pts) < 2:
                    continue
                for a, bb in zip(pts, pts[1:]):
                    if a != bb:
                        tracks.append(Track(a, bb, w, g.layers[l], rt.net))
        seen = set()
        uniq = []
        for v in vias:
            k = (round(v.x, 4), round(v.y, 4))
            if k not in seen:
                seen.add(k)
                uniq.append(v)
        return tracks, uniq


def _count_bends(pts) -> int:
    n = 0
    for a, b, c in zip(pts, pts[1:], pts[2:]):
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            n += 1
    return n


def _octi_cells(a, b, diag_first: bool):
    """Celdas (sin la inicial) de la conexión octilineal a→b: un tramo recto y uno a 45°."""
    (y0, x0), (y1, x1) = a, b
    dy, dx = y1 - y0, x1 - x0
    sy, sx = (dy > 0) - (dy < 0), (dx > 0) - (dx < 0)
    nd = min(abs(dy), abs(dx))
    ns = max(abs(dy), abs(dx)) - nd
    st = (0, sx) if abs(dx) > abs(dy) else (sy, 0)
    steps = ([(sy, sx)] * nd + [st] * ns) if diag_first else ([st] * ns + [(sy, sx)] * nd)
    out = []
    y, x = y0, x0
    for ddy, ddx in steps:
        y, x = y + ddy, x + ddx
        out.append((y, x, ddy, ddx))
    return out


def _octi_legal(a, b, cost2d, iy0, ix0, diag_first: bool):
    H, W = cost2d.shape
    cells = _octi_cells(a, b, diag_first)
    py, px = a
    for y, x, ddy, ddx in cells:
        ly, lx = y - iy0, x - ix0
        if not (0 <= ly < H and 0 <= lx < W) or cost2d[ly, lx] >= BLOCKED:
            return None
        if ddy and ddx:   # misma regla que la búsqueda: no cortar esquinas entre dos celdas prohibidas
            if cost2d[py - iy0, lx] >= BLOCKED and cost2d[ly, px - ix0] >= BLOCKED:
                return None
        py, px = y, x
    return [(y, x) for y, x, _, _ in cells]


def _pull_tight(pts, cost2d, iy0, ix0):
    """Recorre los vértices (codos) del tramo y une cada uno con el vértice más lejano
    alcanzable por una conexión octilineal legal."""
    if len(pts) < 3:
        return pts
    # vértices: extremos y codos
    vid = [0] + [i for i in range(1, len(pts) - 1)
                 if (pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) !=
                 (pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])] + [len(pts) - 1]
    if len(vid) <= 2:
        return pts
    out = [pts[0]]
    i = 0
    while i < len(vid) - 1:
        a = pts[vid[i]]
        best = None
        for j in range(len(vid) - 1, i + 1, -1):       # del más lejano al más cercano
            b = pts[vid[j]]
            for diag_first in (False, True):
                cells = _octi_legal(a, b, cost2d, iy0, ix0, diag_first)
                if cells is not None:
                    best = (j, cells)
                    break
            if best:
                break
        if best is None:                                # no hay atajo: se conserva el tramo original
            j = i + 1
            out.extend(pts[vid[i] + 1: vid[j] + 1])
        else:
            j, cells = best
            out.extend(cells)
        i = j
    return out


def _simplify(pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 1e-9 and abs(out[-1][1] - p[1]) < 1e-9:
            continue
        while len(out) >= 2:
            a, b = out[-2], out[-1]
            cr = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
            dot = (b[0] - a[0]) * (p[0] - b[0]) + (b[1] - a[1]) * (p[1] - b[1])
            if abs(cr) < 1e-9 and dot >= 0:
                out.pop()
            else:
                break
        out.append(p)
    return out
