"""Primitivas geométricas (mm, eje Y hacia abajo como en KiCad)."""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Point = Tuple[float, float]


def rotate(x: float, y: float, angle_deg: float) -> Point:
    """Rota un punto como KiCad: ángulo positivo = antihorario en pantalla (Y abajo)."""
    if not angle_deg:
        return x, y
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point_seg_dist(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segs_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2 = _orient(c, d, a), _orient(c, d, b)
    d3, d4 = _orient(a, b, c), _orient(a, b, d)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return False


def seg_seg_dist(a: Point, b: Point, c: Point, d: Point) -> float:
    if segs_intersect(a, b, c, d):
        return 0.0
    return min(point_seg_dist(a, c, d), point_seg_dist(b, c, d),
               point_seg_dist(c, a, b), point_seg_dist(d, a, b))


def point_in_convex(p: Point, poly: Sequence[Point]) -> bool:
    n = len(poly)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        o = _orient(poly[i], poly[(i + 1) % n], p)
        if abs(o) < 1e-12:
            continue
        s = 1 if o > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def poly_dist(P: Sequence[Point], Q: Sequence[Point]) -> float:
    """Distancia entre dos convexos dados como lista de vértices (1, 2 o n puntos)."""
    if len(P) >= 3 and any(point_in_convex(q, P) for q in Q):
        return 0.0
    if len(Q) >= 3 and any(point_in_convex(p, Q) for p in P):
        return 0.0
    ep = _edges(P)
    eq = _edges(Q)
    best = math.inf
    for a, b in ep:
        for c, d in eq:
            v = seg_seg_dist(a, b, c, d)
            if v < best:
                best = v
                if best == 0.0:
                    return 0.0
    return best


def _edges(P: Sequence[Point]) -> List[Tuple[Point, Point]]:
    if len(P) == 1:
        return [(P[0], P[0])]
    if len(P) == 2:
        return [(P[0], P[1])]
    return [(P[i], P[(i + 1) % len(P)]) for i in range(len(P))]


def point_in_polygon(px, py, poly: Sequence[Point]):
    """Par-impar vectorizado: px, py pueden ser arrays numpy."""
    import numpy as np
    inside = np.zeros(np.broadcast(px, py).shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if y1 == y2:
            continue
        cond = (py >= min(y1, y2)) & (py < max(y1, y2))
        xint = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (px < xint)
    return inside


def arc_points(start: Point, mid: Point, end: Point, seg_len: float = 0.5) -> List[Point]:
    """Discretiza un arco definido por tres puntos."""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        return [start, end]
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1 = math.atan2(y1 - uy, x1 - ux)
    a2 = math.atan2(y2 - uy, x2 - ux)
    a3 = math.atan2(y3 - uy, x3 - ux)

    def norm(a):
        return a % (2 * math.pi)
    # sentido que pasa por el punto medio
    ccw = norm(a2 - a1) < norm(a3 - a1)
    sweep = norm(a3 - a1) if ccw else -norm(a1 - a3)
    n = max(4, int(abs(sweep) * r / seg_len))
    return [(ux + r * math.cos(a1 + sweep * k / n), uy + r * math.sin(a1 + sweep * k / n)) for k in range(n + 1)]


def rect_poly(cx: float, cy: float, w: float, h: float, angle: float) -> List[Point]:
    hw, hh = w / 2, h / 2
    pts = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    out = []
    for x, y in pts:
        rx, ry = rotate(x, y, angle)
        out.append((cx + rx, cy + ry))
    return out


def polyline_length(pts: Sequence[Point]) -> float:
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
