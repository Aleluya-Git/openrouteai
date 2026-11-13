"""Edición interactiva sin dependencias de Qt (la usa la vista 2D y se prueba aparte).

1) Mover/girar grupos de huellas arrastrando su cobre conectado:
   - una "isla" de cobre (pistas + vías + pads conectados de una red) cuyos pads
     pertenecen todos a huellas que se mueven, se mueve entera (rígida);
   - si la isla también llega a huellas que no se mueven, solo se estiran los
     extremos de pista que están dentro de un pad que se mueve.
2) Arrastrar segmentos de pista y vías manteniendo la conectividad:
   - los segmentos vecinos que comparten extremo lo siguen (se estiran);
   - una vía en un extremo arrastrado se mueve con él;
   - un extremo que está sobre un pad no puede despegarse: se añade un tramo
     de unión desde el pad hasta la nueva posición.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .core.geometry import Point, point_seg_dist, poly_dist, rotate
from .core.model import Board, Footprint, Pad, Track, Via

EPS = 1e-4
Transform = Callable[[Point], Point]


def translation(dx: float, dy: float) -> Transform:
    return lambda p: (p[0] + dx, p[1] + dy)


def rotation(cx: float, cy: float, angle: float) -> Transform:
    def f(p):
        rx, ry = rotate(p[0] - cx, p[1] - cy, angle)
        return (cx + rx, cy + ry)
    return f


def is_generated(o) -> bool:
    """Pista/vía creada por OpenRouteAI (editable). Las del archivo original no se tocan."""
    return (not o.fixed) or getattr(o, "_was_routed", False)


def point_in_pad(pt: Point, pad: Pad, tol: float = EPS) -> bool:
    poly, r = pad.copper_shape()
    return poly_dist([pt], poly) - r <= tol


def pad_on_layer(pad: Pad, layer: str) -> bool:
    return layer in pad.layers or "*.Cu" in pad.layers or pad.kind == "thru_hole"


def via_layers(board: Board, v: Via) -> List[str]:
    cu = board.copper_layers
    i0 = cu.index(v.layers[0]) if v.layers[0] in cu else 0
    i1 = cu.index(v.layers[1]) if v.layers[1] in cu else len(cu) - 1
    return cu[min(i0, i1):max(i0, i1) + 1]


def _same(a: Point, b: Point, tol: float = EPS) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def split_t_junctions(board: Board, nets: Optional[Set[str]] = None, exclude: Optional[Set[int]] = None) -> int:
    """Parte los segmentos generados por los puntos interiores donde se les une
    otra pista o una vía de la misma red. Devuelve cuántos cortes se hicieron."""
    by_net: Dict[str, List[Track]] = {}
    for t in board.tracks:
        if nets is None or t.net in nets:
            by_net.setdefault(t.net, []).append(t)
    vias_by_net: Dict[str, List[Via]] = {}
    for v in board.vias:
        vias_by_net.setdefault(v.net, []).append(v)
    cuts = 0
    new_tracks: List[Track] = []
    replaced: Set[int] = set()
    for net, tracks in by_net.items():
        for t in tracks:
            if not is_generated(t) or t.length < 2 * EPS or (exclude and id(t) in exclude):
                continue
            pts: List[Point] = []
            for o in tracks:
                if o is t or o.layer != t.layer:
                    continue
                for q in (o.start, o.end):
                    if not _same(q, t.start) and not _same(q, t.end) and point_seg_dist(q, t.start, t.end) <= EPS:
                        pts.append(q)
            for v in vias_by_net.get(net, []):
                q = (v.x, v.y)
                if t.layer in via_layers(board, v) and not _same(q, t.start) and not _same(q, t.end) and \
                        point_seg_dist(q, t.start, t.end) <= EPS:
                    pts.append(q)
            if not pts:
                continue
            L = t.length
            ux, uy = (t.end[0] - t.start[0]) / L, (t.end[1] - t.start[1]) / L
            pts = sorted({(round(q[0], 6), round(q[1], 6)) for q in pts},
                         key=lambda q: (q[0] - t.start[0]) * ux + (q[1] - t.start[1]) * uy)
            chain = [t.start] + list(pts) + [t.end]
            for a, b in zip(chain, chain[1:]):
                nt = Track(a, b, t.width, t.layer, t.net, fixed=t.fixed)
                if getattr(t, "_was_routed", False):
                    nt._was_routed = True
                new_tracks.append(nt)
            replaced.add(id(t))
            cuts += len(pts)
    if replaced:
        board.tracks[:] = [t for t in board.tracks if id(t) not in replaced] + new_tracks
    return cuts


# ====================================================================== huellas
@dataclass
class FootprintPlan:
    footprints: List[Footprint]
    rigid_tracks: List[Track] = field(default_factory=list)
    rigid_vias: List[Via] = field(default_factory=list)
    stretch: List[Tuple[Track, int]] = field(default_factory=list)    # (pista, 0=start / 1=end)
    moved_vias: List[Via] = field(default_factory=list)                # vías dentro de un pad que se mueve
    rats: List[Tuple[Pad, Pad]] = field(default_factory=list)          # conexiones pendientes afectadas


def plan_footprint_move(board: Board, fps: Sequence[Footprint], only_generated: bool = True) -> FootprintPlan:
    plan = FootprintPlan(footprints=list(fps))
    moved = {id(fp) for fp in fps}
    moved_pads = [p for fp in fps for p in fp.pads]
    nets = {p.net for p in moved_pads if p.net}
    # las uniones en T (una rama o una vía en mitad de un segmento) deben quedar
    # en un extremo para que al estirar no se despegue la rama
    split_t_junctions(board, nets)
    for net in nets:
        pads = [p for p in board.nets[net].pads] if net in board.nets else []
        tracks = [t for t in board.tracks if t.net == net]
        vias = [v for v in board.vias if v.net == net]
        # unión-búsqueda sobre pads, pistas y vías
        nodes: List[object] = list(pads) + list(tracks) + list(vias)
        idx = {id(o): i for i, o in enumerate(nodes)}
        parent = list(range(len(nodes)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(a, b):
            ra, rb = find(idx[id(a)]), find(idx[id(b)])
            if ra != rb:
                parent[ra] = rb

        ends: Dict[int, List[Tuple[int, Optional[Pad]]]] = {}
        for t in tracks:
            for k, pt in enumerate((t.start, t.end)):
                for p in pads:
                    if pad_on_layer(p, t.layer) and point_in_pad(pt, p):
                        union(t, p)
                        ends.setdefault(id(t), []).append((k, p))
            for v in vias:
                if t.layer in via_layers(board, v) and \
                        point_seg_dist((v.x, v.y), t.start, t.end) <= v.diameter / 2 + EPS:
                    union(t, v)
        for i, a in enumerate(tracks):
            for b in tracks[i + 1:]:
                if a.layer != b.layer:
                    continue
                if any(_same(p, q) for p in (a.start, a.end) for q in (b.start, b.end)) or \
                        point_seg_dist(a.start, b.start, b.end) <= EPS or point_seg_dist(a.end, b.start, b.end) <= EPS or \
                        point_seg_dist(b.start, a.start, a.end) <= EPS or point_seg_dist(b.end, a.start, a.end) <= EPS:
                    union(a, b)
        for v in vias:
            for p in pads:
                if point_in_pad((v.x, v.y), p):
                    union(v, p)
        groups: Dict[int, List[object]] = {}
        for o in nodes:
            groups.setdefault(find(idx[id(o)]), []).append(o)
        for members in groups.values():
            gpads = [o for o in members if isinstance(o, Pad)]
            gtracks = [o for o in members if isinstance(o, Track)]
            gvias = [o for o in members if isinstance(o, Via)]
            if not gtracks and not gvias:
                continue
            rigid = bool(gpads) and all(id(p.footprint) in moved for p in gpads)
            # vías dentro de un pad que se mueve: se mueven con él
            mv = []
            if not rigid:
                for v in gvias:
                    if (not only_generated or is_generated(v)) and \
                            any(id(p.footprint) in moved and point_in_pad((v.x, v.y), p) for p in gpads):
                        mv.append(v)
                plan.moved_vias += mv
            for t in gtracks:
                if only_generated and not is_generated(t):
                    continue
                if rigid:
                    plan.rigid_tracks.append(t)
                else:
                    for k, p in ends.get(id(t), []):
                        if id(p.footprint) in moved:
                            plan.stretch.append((t, k))
                    for k, pt in enumerate((t.start, t.end)):
                        if any(t.layer in via_layers(board, v) and _same(pt, (v.x, v.y), v.diameter / 2) for v in mv):
                            plan.stretch.append((t, k))
            if rigid:
                plan.rigid_vias += [v for v in gvias if not only_generated or is_generated(v)]
    # conexiones pendientes (ratsnest) que tocan pads movidos
    try:
        from .analysis.netlist_analyzer import mst_connections
        from .drc.drc import unconnected
        pending = {n for n, _, _ in unconnected(board)}
        for name in pending & nets:
            for c in mst_connections(board.nets[name]):
                if id(c.a.footprint) in moved or id(c.b.footprint) in moved:
                    plan.rats.append((c.a, c.b))
    except Exception:
        pass
    # quitar duplicados de estiramiento
    seen = set()
    plan.stretch = [(t, k) for t, k in plan.stretch if not ((id(t), k) in seen or seen.add((id(t), k)))]
    return plan


def preview_track(t: Track, plan_ends: Dict[int, Set[int]], rigid: Set[int], G: Transform) -> Tuple[Point, Point]:
    """Posición de una pista bajo la transformación G (sin modificarla)."""
    if id(t) in rigid:
        return G(t.start), G(t.end)
    e = plan_ends.get(id(t), set())
    return (G(t.start) if 0 in e else t.start), (G(t.end) if 1 in e else t.end)


def apply_footprint_plan(plan: FootprintPlan, G: Transform, dangle: float = 0.0) -> None:
    """Aplica la transformación rígida G (y el giro ``dangle``) a huellas y cobre conectado."""
    for fp in plan.footprints:
        nx, ny = G((fp.x, fp.y))
        fp.set_pose(nx, ny, fp.angle + dangle)
    for t in plan.rigid_tracks:
        t.start, t.end = G(t.start), G(t.end)
    for v in plan.rigid_vias + plan.moved_vias:
        v.x, v.y = G((v.x, v.y))
    for t, k in plan.stretch:
        if k == 0:
            t.start = G(t.start)
        else:
            t.end = G(t.end)


# ====================================================================== pistas
@dataclass
class TrackPlan:
    tracks: List[Track]                                          # segmentos seleccionados
    vias: List[Via]                                              # vías arrastradas
    follow: List[Tuple[Track, int]] = field(default_factory=list)   # extremos de vecinos que siguen
    bridges: List[Tuple[Point, str, float, str]] = field(default_factory=list)  # (punto fijo, capa, ancho, red)
    bridge_ends: List[Tuple[Track, int]] = field(default_factory=list)          # extremos que se separan del pad


def plan_track_move(board: Board, tracks: Sequence[Track], vias: Sequence[Via] = ()) -> TrackPlan:
    plan = TrackPlan(tracks=list(tracks), vias=list(vias))
    sel = {id(t) for t in tracks}
    # las pistas no seleccionadas que reciben un extremo arrastrado (o pasan por una
    # vía arrastrada) en su interior se parten ahí, para que ese punto pueda seguir
    split_t_junctions(board, {t.net for t in tracks} | {v.net for v in vias}, exclude=sel)
    moved_vias = {id(v) for v in vias}
    # puntos que se mueven: extremos de los segmentos seleccionados y centros de las vías
    moving_pts: List[Tuple[Point, str, str, Optional[Track], int]] = []   # (punto, capa, red, pista, extremo)
    for t in tracks:
        for k, pt in enumerate((t.start, t.end)):
            moving_pts.append((pt, t.layer, t.net, t, k))
    changed = True
    # una vía en un extremo arrastrado se mueve también
    while changed:
        changed = False
        for pt, layer, net, t, k in list(moving_pts):
            for v in board.vias:
                if id(v) in moved_vias or v.net != net or not is_generated(v):
                    continue
                if layer in via_layers(board, v) and math.hypot(pt[0] - v.x, pt[1] - v.y) <= v.diameter / 2 + EPS:
                    moved_vias.add(id(v))
                    plan.vias.append(v)
                    changed = True
    for v in plan.vias:
        for layer in via_layers(board, v):
            moving_pts.append(((v.x, v.y), layer, v.net, None, -1))
    seen_mid: Set[Tuple[int, int]] = set()
    pads_by_net: Dict[str, List[Pad]] = {}
    for p in board.pads:
        if p.net:
            pads_by_net.setdefault(p.net, []).append(p)
    seen = set()
    # lo que se une por el medio de un segmento seleccionado lo acompaña
    for t in tracks:
        for o in board.tracks:
            if id(o) in sel or o.net != t.net or o.layer != t.layer:
                continue
            for kk, q in enumerate((o.start, o.end)):
                if (id(o), kk) not in seen_mid and not _same(q, t.start) and not _same(q, t.end) and \
                        point_seg_dist(q, t.start, t.end) <= EPS:
                    seen_mid.add((id(o), kk))
                    plan.follow.append((o, kk))
        for v in board.vias:
            if id(v) not in moved_vias and v.net == t.net and is_generated(v) and t.layer in via_layers(board, v) \
                    and point_seg_dist((v.x, v.y), t.start, t.end) <= EPS:
                moved_vias.add(id(v))
                plan.vias.append(v)
                for layer in via_layers(board, v):
                    moving_pts.append(((v.x, v.y), layer, v.net, None, -1))
    for pt, layer, net, t, k in moving_pts:
        # vecinos no seleccionados con un extremo en el punto
        for o in board.tracks:
            if id(o) in sel or o.net != net or o.layer != layer:
                continue
            for kk, q in enumerate((o.start, o.end)):
                if _same(q, pt) and (id(o), kk) not in seen and (id(o), kk) not in seen_mid:
                    seen.add((id(o), kk))
                    plan.follow.append((o, kk))
        # extremo sobre un pad (solo para segmentos, no para vías): tramo de unión
        if t is not None:
            for p in pads_by_net.get(net, []):
                if pad_on_layer(p, layer) and point_in_pad(pt, p):
                    plan.bridges.append((pt, layer, t.width, net))
                    plan.bridge_ends.append((t, k))
                    break
    return plan


def apply_track_plan(board: Board, plan: TrackPlan, dx: float, dy: float) -> List[Track]:
    """Desplaza segmentos y vías (dx, dy) y crea los tramos de unión necesarios."""
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return []
    G = translation(dx, dy)
    for t in plan.tracks:
        t.start, t.end = G(t.start), G(t.end)
    for v in plan.vias:
        v.x, v.y = G((v.x, v.y))
    for o, k in plan.follow:
        if k == 0:
            o.start = G(o.start)
        else:
            o.end = G(o.end)
    new = []
    for (pt, layer, w, net) in plan.bridges:
        nt = Track(pt, G(pt), w, layer, net)
        board.tracks.append(nt)
        new.append(nt)
    # segmentos degenerados (longitud 0) que hayan quedado
    board.tracks[:] = [t for t in board.tracks if t.length > 1e-6 or not is_generated(t)]
    return new


# ====================================================================== selección
def track_at(board: Board, x: float, y: float, visible_layers: Optional[Set[str]] = None,
             tol: float = 0.0) -> Optional[Track]:
    best = None
    for t in board.tracks:
        if visible_layers is not None and t.layer not in visible_layers:
            continue
        d = point_seg_dist((x, y), t.start, t.end) - t.width / 2
        if d <= tol and (best is None or d < best[0]):
            best = (d, t)
    return best[1] if best else None


def via_at(board: Board, x: float, y: float, tol: float = 0.0) -> Optional[Via]:
    for v in board.vias:
        if math.hypot(x - v.x, y - v.y) <= v.diameter / 2 + tol:
            return v
    return None


def footprints_in_rect(board: Board, x0: float, y0: float, x1: float, y1: float, touching: bool) -> List[Footprint]:
    """Huellas dentro del rectángulo (``touching``: basta con tocarlo, como KiCad de derecha a izquierda)."""
    ax0, ax1 = min(x0, x1), max(x0, x1)
    ay0, ay1 = min(y0, y1), max(y0, y1)
    out = []
    for fp in board.footprints:
        bx0, by0, bx1, by1 = fp.bbox()
        if touching:
            ok = bx0 <= ax1 and bx1 >= ax0 and by0 <= ay1 and by1 >= ay0
        else:
            ok = bx0 >= ax0 and bx1 <= ax1 and by0 >= ay0 and by1 <= ay1
        if ok:
            out.append(fp)
    return out


def selection_center(fps: Iterable[Footprint]) -> Point:
    fps = list(fps)
    if len(fps) == 1:
        return fps[0].x, fps[0].y
    boxes = [fp.bbox() for fp in fps]
    return ((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2,
            (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2)
