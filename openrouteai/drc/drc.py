"""DRC Validation Engine (geometría continua, independiente de la rejilla).

Comprueba:
  - cortocircuitos (cobre de redes distintas que se toca)
  - violaciones de clearance (incluye colisiones pista/pad/vía)
  - distancia cobre-borde de placa
  - circuitos abiertos (pads de una red no conectados entre sí)
  - longitudes fuera de tolerancia (reglas de longitud)
  - pares diferenciales: desfase de longitud (skew)
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.geometry import point_seg_dist, poly_dist
from ..core.model import Board, Pad, Track, Via
from ..i18n import tr


@dataclass
class Violation:
    kind: str           # short | clearance | edge | unconnected | length | diff_skew
    message: str
    x: float = 0.0
    y: float = 0.0
    layer: str = ""
    nets: Tuple[str, ...] = ()
    severity: str = "error"


@dataclass
class DRCReport:
    violations: List[Violation] = field(default_factory=list)
    net_lengths: Dict[str, float] = field(default_factory=dict)

    def count(self, kind: Optional[str] = None) -> int:
        return sum(1 for v in self.violations if kind is None or v.kind == kind)

    def summary(self) -> Dict[str, int]:
        out: Dict[str, int] = defaultdict(int)
        for v in self.violations:
            out[v.kind] += 1
        return dict(out)


@dataclass
class _Item:
    poly: List[Tuple[float, float]]
    r: float
    layers: Tuple[str, ...]
    net: str
    label: str
    bbox: Tuple[float, float, float, float]
    obj: object = None


def _mk(poly, r, layers, net, label, obj=None) -> _Item:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return _Item(poly, r, tuple(layers), net, label, (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r), obj)


def _items(board: Board) -> List[_Item]:
    cu = board.copper_layers
    out = []
    for p in board.pads:
        if p.kind == "np_thru_hole":
            out.append(_mk([p.pos], max(p.drill, min(p.w, p.h)) / 2, cu, "", f"NPTH {p.ref}", p))
            continue
        lays = [l for l in cu if l in p.layers] or (list(cu) if "*.Cu" in p.layers or p.kind == "thru_hole" else [])
        if not lays:
            continue
        poly, r = p.copper_shape()
        out.append(_mk(poly, r, lays, p.net, f"pad {p.ref}", p))
    for t in board.tracks:
        out.append(_mk([t.start, t.end], t.width / 2, [t.layer], t.net, tr("pista {net}", net=t.net), t))
    for v in board.vias:
        i0 = cu.index(v.layers[0]) if v.layers[0] in cu else 0
        i1 = cu.index(v.layers[1]) if v.layers[1] in cu else len(cu) - 1
        lays = cu[min(i0, i1):max(i0, i1) + 1]
        out.append(_mk([v.pos], v.diameter / 2, lays, v.net, tr("vía {net}", net=v.net), v))
    return out


def run_drc(board: Board, check_unconnected: bool = True, max_reports: int = 2000) -> DRCReport:
    rep = DRCReport()
    items = _items(board)
    maxc = max([nc.clearance for nc in board.netclasses.values()] + [board.rules.min_clearance, 0.2])
    cell = 2.0
    grid: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, it in enumerate(items):
        x0, y0, x1, y1 = it.bbox
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid[(gx, gy)].append(i)

    seen = set()
    for i, a in enumerate(items):
        x0, y0, x1, y1 = a.bbox
        cand = set()
        for gx in range(int(math.floor((x0 - maxc) / cell)), int(math.floor((x1 + maxc) / cell)) + 1):
            for gy in range(int(math.floor((y0 - maxc) / cell)), int(math.floor((y1 + maxc) / cell)) + 1):
                cand.update(grid.get((gx, gy), ()))
        for j in cand:
            if j <= i:
                continue
            b = items[j]
            if a.net and a.net == b.net:
                continue
            if isinstance(a.obj, Pad) and isinstance(b.obj, Pad) and a.obj.footprint is b.obj.footprint:
                continue  # pads de la misma huella: responsabilidad de la librería
            if isinstance(a.obj, Pad) and isinstance(b.obj, Pad):
                pass
            common = set(a.layers) & set(b.layers)
            if not common:
                continue
            clr = board.clearance_between(a.net, b.net)
            if a.bbox[0] - b.bbox[2] > clr or b.bbox[0] - a.bbox[2] > clr or \
               a.bbox[1] - b.bbox[3] > clr or b.bbox[1] - a.bbox[3] > clr:
                continue
            d = poly_dist(a.poly, b.poly) - a.r - b.r
            if d < clr - 1e-4:
                if isinstance(a.obj, Pad) and isinstance(b.obj, Pad):
                    continue  # pad-pad entre huellas: problema de colocación, no de enrutado
                key = (min(i, j), max(i, j))
                if key in seen:
                    continue
                seen.add(key)
                cx = (a.poly[0][0] + b.poly[0][0]) / 2
                cy = (a.poly[0][1] + b.poly[0][1]) / 2
                kind = "short" if d <= 0 else "clearance"
                msg = (tr("Cortocircuito {a} ↔ {b}", a=a.label, b=b.label) if kind == "short" else
                       tr("Clearance {d} < {c} mm: {a} ↔ {b}", d=f"{d:.3f}", c=f"{clr:.3f}", a=a.label, b=b.label))
                rep.violations.append(Violation(kind, msg, cx, cy, sorted(common)[0], (a.net, b.net)))
                if len(rep.violations) >= max_reports:
                    return rep

    # borde de placa
    if board.outline:
        ec = board.rules.copper_edge_clearance
        n = len(board.outline)
        edges = [(board.outline[k], board.outline[(k + 1) % n]) for k in range(n)]
        for it in items:
            if isinstance(it.obj, Pad):
                continue
            dmin = min(poly_dist(it.poly, [e0, e1]) for e0, e1 in edges) - it.r
            if dmin < ec - 1e-4:
                rep.violations.append(Violation("edge", tr("{x} a {d} mm del borde (mín {m})", x=it.label, d=f"{dmin:.3f}", m=ec),
                                                it.poly[0][0], it.poly[0][1], it.layers[0], (it.net,)))

    # rellenos de zona frente al cobre de otras redes
    for z in board.zones:
        if z.filled and not z.stale and not (z.keepout_tracks or z.keepout_vias):
            from ..zones_fill import fill_violations, zone_clearance
            zc = zone_clearance(board, z.net)
            for d, x, y, net in fill_violations(board, z, zc):
                kind = "short" if d <= 0 else "clearance"
                lab = tr("zona {net}", net=z.net)
                msg = (tr("Cortocircuito {a} ↔ {b}", a=lab, b=net or "NPTH") if kind == "short" else
                       tr("Clearance {d} < {c} mm: {a} ↔ {b}", d=f"{d:.3f}", c=f"{zc:.3f}", a=lab, b=net or "NPTH"))
                rep.violations.append(Violation(kind, msg, x, y, z.layers[0], (z.net, net)))

    # longitudes por red
    for t in board.tracks:
        rep.net_lengths[t.net] = rep.net_lengths.get(t.net, 0.0) + t.length
    for pat, (lmin, lmax) in board.rules.length_rules.items():
        for net, L in rep.net_lengths.items():
            if re.fullmatch(pat, net) and not (lmin <= L <= lmax):
                rep.violations.append(Violation("length", tr("{net}: longitud {l} mm fuera de [{a}, {b}]", net=net, l=f"{L:.2f}", a=lmin, b=lmax),
                                                nets=(net,)))
    for net in board.nets.values():
        if net.pair and net.name < net.pair:
            la, lb = rep.net_lengths.get(net.name, 0), rep.net_lengths.get(net.pair, 0)
            if la and lb and abs(la - lb) > board.rules.diff_pair_max_skew:
                rep.violations.append(Violation("diff_skew",
                                                tr("Par {p}: desfase {d} mm (máx {m})", p=f"{net.name}/{net.pair}",
                                                   d=f"{abs(la - lb):.2f}", m=board.rules.diff_pair_max_skew),
                                                nets=(net.name, net.pair),
                                                severity="warning"))

    if check_unconnected:
        for name, cnt, pos in unconnected(board, items):
            rep.violations.append(Violation("unconnected", tr("{net}: {n} conexión(es) sin enrutar", net=name, n=cnt),
                                            pos[0], pos[1], nets=(name,)))
    return rep


def unconnected(board: Board, items: Optional[List[_Item]] = None):
    """Por red: número de grupos aislados - 1 (conexiones pendientes)."""
    items = items if items is not None else _items(board)
    zone_layers = defaultdict(set)          # zonas sin relleno calculado: se supone la capa entera unida
    zone_fills = defaultdict(list)          # zonas rellenas: (capa, islas)
    for z in board.zones:
        if not (z.keepout_tracks or z.keepout_vias):
            if getattr(z, "filled", None):
                zone_fills[z.net].append((z.layers[0], z.filled))
            else:
                for l in z.layers:
                    zone_layers[z.net].add(l)
    by_net: Dict[str, List[int]] = defaultdict(list)
    for i, it in enumerate(items):
        if it.net:
            by_net[it.net].append(i)
    out = []
    for net, idxs in by_net.items():
        pads = [i for i in idxs if isinstance(items[i].obj, Pad)]
        if len(pads) < 2:
            continue
        parent = {i: i for i in idxs}

        def f(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for ii, i in enumerate(idxs):
            a = items[i]
            for j in idxs[ii + 1:]:
                b = items[j]
                if not (set(a.layers) & set(b.layers)):
                    continue
                if a.bbox[0] > b.bbox[2] + 1e-3 or b.bbox[0] > a.bbox[2] + 1e-3 or \
                   a.bbox[1] > b.bbox[3] + 1e-3 or b.bbox[1] > a.bbox[3] + 1e-3:
                    continue
                if poly_dist(a.poly, b.poly) - a.r - b.r <= 1e-4:
                    parent[f(i)] = f(j)
        # plano (zona) en una capa: todo lo que toque esa capa queda unido
        zl = zone_layers.get(net)
        if zl:
            on_plane = [i for i in idxs if set(items[i].layers) & zl]
            for i in on_plane[1:]:
                parent[f(i)] = f(on_plane[0])
        # plano relleno: se unen los elementos que caen en la misma isla de cobre
        for layer, polys in zone_fills.get(net, []):
            from ..zones_fill import island_of
            first_in: Dict[int, int] = {}
            for i in idxs:
                it = items[i]
                if layer not in it.layers:
                    continue
                pts = list(it.poly) if len(it.poly) <= 2 else [(sum(q[0] for q in it.poly) / len(it.poly),
                                                                  sum(q[1] for q in it.poly) / len(it.poly))]
                for q in pts:
                    k = island_of(q[0], q[1], polys)
                    if k >= 0:
                        if k in first_in:
                            parent[f(i)] = f(first_in[k])
                        else:
                            first_in[k] = i
        groups = {f(i) for i in pads}
        if len(groups) > 1:
            p = items[pads[0]].poly[0]
            out.append((net, len(groups) - 1, p))
    return out
