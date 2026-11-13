"""Relleno de zonas de cobre (planos de masa/alimentación), como «Rellenar zonas» de KiCad.

Método:
  1. Campo de distancia exacta (rejilla fina) desde cada punto al cobre de OTRAS
     redes en la capa de la zona (pads, pistas, vías, agujeros) y al borde de placa.
  2. Región permitida = distancia >= clearance (+ margen de rejilla).
  3. Apertura morfológica con el grosor mínimo de la zona (quita cuellos estrechos).
  4. Se eliminan las islas que no tocan cobre de la propia red (como «quitar islas»).
  5. El contorno se extrae con contourpy sobre el campo continuo (bordes suaves) y
     se convierte a polígonos con agujeros.

El cobre de la propia red queda unido de forma sólida (conexión directa de pads).
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .core.geometry import Point, point_in_polygon
from .core.model import Board, Zone

Ring = List[Point]
Polygon = Tuple[Ring, List[Ring]]          # (contorno exterior, agujeros)


def _seg_dist(xs, ys, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return np.hypot(xs - ax, ys - ay)
    t = np.clip(((xs - ax) * dx + (ys - ay) * dy) / L2, 0.0, 1.0)
    return np.hypot(xs - (ax + t * dx), ys - (ay + t * dy))


def _shape_dist(xs, ys, poly, r):
    if len(poly) == 1:
        d = np.hypot(xs - poly[0][0], ys - poly[0][1])
    elif len(poly) == 2:
        d = _seg_dist(xs, ys, poly[0], poly[1])
    else:
        d = np.full(np.broadcast(xs, ys).shape, np.inf)
        pos = np.ones_like(d, dtype=bool)
        neg = np.ones_like(d, dtype=bool)
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            d = np.minimum(d, _seg_dist(xs, ys, a, b))
            cr = (b[0] - a[0]) * (ys - a[1]) - (b[1] - a[1]) * (xs - a[0])
            pos &= cr >= -1e-12
            neg &= cr <= 1e-12
        d = np.where(pos | neg, 0.0, d)
    return d - r


def _copper_on_layer(board: Board, layer: str):
    """(polígono, radio, red) del cobre presente en la capa."""
    cu = board.copper_layers
    out = []
    for p in board.pads:
        if p.kind == "np_thru_hole":
            out.append(([(p.x, p.y)], max(p.drill, min(p.w, p.h)) / 2, None))
            continue
        if not (layer in p.layers or "*.Cu" in p.layers or p.kind == "thru_hole"):
            continue
        poly, r = p.copper_shape()
        out.append((poly, r, p.net))
    for t in board.tracks:
        if t.layer == layer:
            out.append(([t.start, t.end], t.width / 2, t.net))
    for v in board.vias:
        i0 = cu.index(v.layers[0]) if v.layers[0] in cu else 0
        i1 = cu.index(v.layers[1]) if v.layers[1] in cu else len(cu) - 1
        if layer in cu[min(i0, i1):max(i0, i1) + 1]:
            out.append(([(v.x, v.y)], v.diameter / 2, v.net))
    return out


def zone_clearance(board: Board, net: str) -> float:
    """Separación de la zona al cobre ajeno (la misma que se escribe en el .kicad_pcb)."""
    return max(board.netclass_of(net).clearance, board.rules.min_clearance, 0.2)


def fill_zone(board: Board, zone: Zone, clearance: Optional[float] = None, min_thickness: float = 0.25,
              pitch: float = 0.05, remove_islands: bool = True) -> List[Polygon]:
    """Calcula el relleno de ``zone`` en su primera capa. Devuelve polígonos (exterior, agujeros)."""
    from scipy.ndimage import distance_transform_edt, label
    import contourpy

    if not zone.layers or len(zone.polygon) < 3:
        return []
    layer = zone.layers[0]
    net = zone.net
    clr = clearance if clearance is not None else zone_clearance(board, net)
    ec = board.rules.copper_edge_clearance
    margin = pitch * 1.0                       # error del contorno interpolado + simplificación
    zx = [p[0] for p in zone.polygon]
    zy = [p[1] for p in zone.polygon]
    x0, y0, x1, y1 = min(zx) - 2 * pitch, min(zy) - 2 * pitch, max(zx) + 2 * pitch, max(zy) + 2 * pitch
    xs = np.arange(x0, x1 + pitch, pitch)
    ys = np.arange(y0, y1 + pitch, pitch)
    X, Y = np.meshgrid(xs, ys)
    f = np.full(X.shape, 1e3, dtype=np.float64)      # holgura respecto a lo que hay que esquivar
    own = np.zeros(X.shape, dtype=bool)               # cobre de la propia red

    # contorno de la zona y borde de placa
    inside = point_in_polygon(X, Y, zone.polygon)
    n = len(zone.polygon)
    dz = np.full(X.shape, np.inf)
    for i in range(n):
        dz = np.minimum(dz, _seg_dist(X, Y, zone.polygon[i], zone.polygon[(i + 1) % n]))
    f = np.minimum(f, np.where(inside, dz, -dz))
    if board.outline:
        inb = point_in_polygon(X, Y, board.outline)
        db = np.full(X.shape, np.inf)
        m = len(board.outline)
        for i in range(m):
            db = np.minimum(db, _seg_dist(X, Y, board.outline[i], board.outline[(i + 1) % m]))
        f = np.minimum(f, np.where(inb, db - ec - margin, -db))

    reach = clr + margin + 0.5
    for poly, r, onet in _copper_on_layer(board, layer):
        px = [q[0] for q in poly]
        py = [q[1] for q in poly]
        bx0, bx1 = min(px) - r - reach, max(px) + r + reach
        by0, by1 = min(py) - r - reach, max(py) + r + reach
        i0, i1 = max(0, int((bx0 - x0) / pitch)), min(len(xs), int((bx1 - x0) / pitch) + 2)
        j0, j1 = max(0, int((by0 - y0) / pitch)), min(len(ys), int((by1 - y0) / pitch) + 2)
        if i1 <= i0 or j1 <= j0:
            continue
        d = _shape_dist(X[j0:j1, i0:i1], Y[j0:j1, i0:i1], poly, r)
        if onet and onet == net:
            own[j0:j1, i0:i1] |= d <= 0
        else:
            sub = f[j0:j1, i0:i1]
            np.minimum(sub, d - clr - margin, out=sub)

    allowed = (f >= 0) | (own & inside)
    # grosor mínimo: apertura morfológica (erosión + dilatación con radio t/2)
    rad = min_thickness / 2 / pitch
    if rad > 0.5:
        core = distance_transform_edt(allowed) > rad
        opened = distance_transform_edt(~core) <= rad + 0.5
        allowed &= opened | (own & inside)
    # islas sin cobre propio
    if remove_islands:
        lab, nlab = label(allowed)
        keep = np.unique(lab[own & allowed])
        keep = keep[keep > 0]
        allowed = np.isin(lab, keep)
    if not allowed.any():
        return []
    # campo continuo para un contorno suave: positivo dentro de la región permitida
    g = np.where(allowed, np.maximum(np.minimum(f, 0.5), 0.02), -0.02)
    g[0, :] = g[-1, :] = g[:, 0] = g[:, -1] = -0.02
    gen = contourpy.contour_generator(xs, ys, g, fill_type=contourpy.FillType.OuterOffset)
    points, offsets = gen.filled(0.0, 1e9)
    polys: List[Polygon] = []
    for pts, offs in zip(points, offsets):
        rings = [[(float(x), float(y)) for x, y in pts[offs[k]:offs[k + 1]]] for k in range(len(offs) - 1)]
        rings = [_simplify_ring(r_, pitch * 0.4) for r_ in rings]
        rings = [r_ for r_ in rings if len(r_) >= 3 and abs(_area(r_)) > (pitch * 2) ** 2]
        if rings:
            polys.append((rings[0], rings[1:]))
    return polys


def _area(ring: Sequence[Point]) -> float:
    return 0.5 * sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
                     for i in range(len(ring)))


def _simplify_ring(ring: Ring, tol: float) -> Ring:
    """Douglas-Peucker sobre un anillo cerrado (quita puntos colineales de la rejilla)."""
    if len(ring) > 2 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 4:
        return ring

    def dp(pts):
        if len(pts) < 3:
            return pts
        a, b = pts[0], pts[-1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1e-12
        idx, dmax = 0, -1.0
        for i in range(1, len(pts) - 1):
            d = abs(dy * (pts[i][0] - a[0]) - dx * (pts[i][1] - a[1])) / L
            if d > dmax:
                idx, dmax = i, d
        if dmax <= tol:
            return [a, b]
        return dp(pts[:idx + 1])[:-1] + dp(pts[idx:])
    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    # anillo cerrado: se parte en dos tramos entre el punto 0 y el más alejado de él
    a = ring[0]
    far = max(range(len(ring)), key=lambda i: (ring[i][0] - a[0]) ** 2 + (ring[i][1] - a[1]) ** 2)
    if far == 0:
        return ring
    first = dp(ring[:far + 1])
    second = dp(ring[far:] + [ring[0]])
    out = first[:-1] + second[:-1]
    return out if len(out) >= 3 else ring


_EDGE_CACHE: dict = {}


def _island_edges(polys: Sequence[Polygon]):
    """Aristas de cada isla (exterior + agujeros) como arrays NumPy, en caché por lista de polígonos."""
    key = id(polys)
    hit = _EDGE_CACHE.get(key)
    if hit is not None and hit[0] is polys:
        return hit[1]
    out = []
    for outer, holes in polys:
        x1, y1, x2, y2 = [], [], [], []
        for ring in [outer] + list(holes):
            r = np.asarray(ring, dtype=float)
            if len(r) < 3:
                continue
            q = np.roll(r, -1, axis=0)
            x1.append(r[:, 0]); y1.append(r[:, 1]); x2.append(q[:, 0]); y2.append(q[:, 1])
        if x1:
            e = tuple(np.concatenate(v) for v in (x1, y1, x2, y2))
            bb = (min(e[0].min(), e[2].min()), min(e[1].min(), e[3].min()),
                  max(e[0].max(), e[2].max()), max(e[1].max(), e[3].max()))
            out.append((e, bb))
        else:
            out.append(None)
    if len(_EDGE_CACHE) > 64:
        _EDGE_CACHE.clear()
    _EDGE_CACHE[key] = (polys, out)
    return out


def island_of(x: float, y: float, polys: Sequence[Polygon]) -> int:
    """Índice del polígono (isla) que contiene el punto, o -1 (regla par-impar: los agujeros restan)."""
    for i, item in enumerate(_island_edges(polys)):
        if item is None:
            continue
        (x1, y1, x2, y2), bb = item
        if not (bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]):
            continue
        c = (y1 > y) != (y2 > y)
        if not c.any():
            continue
        xi = x1[c] + (y - y1[c]) * (x2[c] - x1[c]) / (y2[c] - y1[c])
        if int(np.count_nonzero(xi > x)) % 2 == 1:
            return i
    return -1


def point_in_filled(x: float, y: float, polys: Sequence[Polygon]) -> bool:
    return island_of(x, y, polys) >= 0


# ------------------------------------------------------------------ exportación
def fracture(outer: Ring, holes: Sequence[Ring]) -> Ring:
    """Une los agujeros al contorno con puentes de anchura nula (formato «filled_polygon» de KiCad)."""
    def orient(r, ccw):
        return list(r) if (_area(r) > 0) == ccw else list(reversed(r))
    poly = orient(outer, True)
    hs = [orient(h, False) for h in holes if len(h) >= 3]
    # de derecha a izquierda: cada agujero se une hacia la derecha con el polígono ya construido
    hs.sort(key=lambda h: -max(p[0] for p in h))
    for h in hs:
        mi = max(range(len(h)), key=lambda i: (h[i][0], -h[i][1]))
        mx, my = h[mi]
        best = None
        n = len(poly)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            if (a[1] - my) * (b[1] - my) > 0 or a[1] == b[1]:
                continue
            t = (my - a[1]) / (b[1] - a[1])
            if not (0 <= t <= 1):
                continue
            x = a[0] + t * (b[0] - a[0])
            if x >= mx - 1e-12 and (best is None or x < best[0]):
                best = (x, i, t)
        if best is None:
            continue
        x, i, t = best
        q = (x, my)
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ring_h = h[mi:] + h[:mi] + [h[mi]]
        insert = ([q] if (q != a and q != b) else []) + ring_h + [q]
        if q == b:
            poly = poly[:i + 2] + ring_h + [b] + poly[i + 2:]
        else:
            poly = poly[:i + 1] + insert + poly[i + 1:]
    return poly


def fill_violations(board: Board, zone: Zone, clearance: Optional[float] = None, step: float = 0.05):
    """Cobre de otras redes demasiado cerca del relleno: [(distancia, x, y, red)] (una por elemento)."""
    if not zone.filled or not zone.layers:
        return []
    clr = zone_clearance(board, zone.net) if clearance is None else clearance
    pts = []
    for outer, holes in zone.filled:
        for ring in [outer] + list(holes):
            n = len(ring)
            for i in range(n):
                a, b = ring[i], ring[(i + 1) % n]
                L = math.hypot(b[0] - a[0], b[1] - a[1])
                k = max(1, int(L / step))
                for j in range(k):
                    t = j / k
                    pts.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    if not pts:
        return []
    P = np.asarray(pts)
    from scipy.spatial import cKDTree
    tree = cKDTree(P)
    out = []
    for poly, r, net in _copper_on_layer(board, zone.layers[0]):
        if net == zone.net and net:
            continue
        px = [q[0] for q in poly]
        py = [q[1] for q in poly]
        cx, cy = (min(px) + max(px)) / 2, (min(py) + max(py)) / 2
        reach = math.hypot(max(px) - min(px), max(py) - min(py)) / 2 + r + clr + step
        idx = tree.query_ball_point((cx, cy), reach)
        inside = point_in_filled(sum(px) / len(px), sum(py) / len(py), zone.filled)
        if not idx and not inside:
            continue
        d = float(_shape_dist(P[idx, 0], P[idx, 1], poly, r).min()) if idx else clr
        if inside:
            d = min(d, 0.0)
        if d < clr - 1e-3:
            out.append((d, px[0], py[0], net or ""))
    return out


def fill_board_zones(board: Board, zones: Sequence[Zone], pitch: float = 0.05) -> None:
    for z in zones:
        if z.keepout_tracks or z.keepout_vias:
            continue
        z.filled = fill_zone(board, z, pitch=pitch)
        z.stale = False
        z.refilled = True
