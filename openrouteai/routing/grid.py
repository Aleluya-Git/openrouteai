"""Grafo espacial / matriz de ocupación / mapa de congestión.

La placa se discretiza en una rejilla 3D (capa, fila, columna) de paso ``pitch``.
Para cada celda se guardan campos de distancia *exactos* (no rasterizados) al
cobre fijo (pads, pistas existentes, zonas de exclusión) y al borde de placa:

  d1/o1 : distancia al cobre más cercano y su propietario (id de red, -1 = nadie)
  d2    : distancia al cobre más cercano de un propietario distinto de o1

Así, para la red ``n`` la distancia al cobre *ajeno* es ``d1`` si ``o1 != n``
y ``d2`` en caso contrario; una celda es válida como eje de pista si esa
distancia >= semiancho + clearance.

El cobre enrutado (variable) se lleva en matrices de ocupación por "clase de
anchura": ``cl[k][capa, y, x]`` = nº de redes cuyo eje pasa por la celda con
semiancho ``radii[k]``. Las vías se registran en todas las capas que atraviesan.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..core.geometry import point_in_polygon
from ..core.model import Board, Pad

INF = np.float32(1e9)


def shape_distance(xs: np.ndarray, ys: np.ndarray, poly: Sequence[Tuple[float, float]], r: float) -> np.ndarray:
    """Distancia (vectorizada) de los puntos a la forma convexa ``poly`` engordada ``r``."""
    if len(poly) == 1:
        d = np.hypot(xs - poly[0][0], ys - poly[0][1])
    elif len(poly) == 2:
        d = _seg_dist(xs, ys, poly[0], poly[1])
    else:
        n = len(poly)
        d = np.full(np.broadcast(xs, ys).shape, np.inf)
        sign_pos = np.ones_like(d, dtype=bool)
        sign_neg = np.ones_like(d, dtype=bool)
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            d = np.minimum(d, _seg_dist(xs, ys, a, b))
            cr = (b[0] - a[0]) * (ys - a[1]) - (b[1] - a[1]) * (xs - a[0])
            sign_pos &= cr >= -1e-12
            sign_neg &= cr <= 1e-12
        d = np.where(sign_pos | sign_neg, 0.0, d)
    return np.maximum(d - r, 0.0)


def _seg_dist(xs, ys, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-18:
        return np.hypot(xs - ax, ys - ay)
    t = np.clip(((xs - ax) * dx + (ys - ay) * dy) / L2, 0.0, 1.0)
    return np.hypot(xs - (ax + t * dx), ys - (ay + t * dy))


def disk(radius_cells: float) -> np.ndarray:
    r = max(0, int(math.floor(radius_cells)))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return (xx * xx + yy * yy) < radius_cells * radius_cells


class RoutingGrid:
    def __init__(self, board: Board, pitch: float, layers: Optional[List[str]] = None, min_radius: float = 0.0):
        self.board = board
        self.pitch = pitch
        self.layers = layers or list(board.copper_layers)
        self.L = len(self.layers)
        bx0, by0, bx1, by1 = board.bbox()
        self.x0 = math.floor(bx0 / pitch) * pitch
        self.y0 = math.floor(by0 / pitch) * pitch
        self.nx = int(math.ceil((bx1 - self.x0) / pitch)) + 1
        self.ny = int(math.ceil((by1 - self.y0) / pitch)) + 1
        self.xs = self.x0 + np.arange(self.nx) * pitch
        self.ys = self.y0 + np.arange(self.ny) * pitch

        self.net_ids: Dict[str, int] = {}
        for i, name in enumerate(sorted(n for n in board.nets if n)):
            self.net_ids[name] = i + 1

        shp = (self.L, self.ny, self.nx)
        self.d1 = np.full(shp, INF, dtype=np.float32)
        self.d2 = np.full(shp, INF, dtype=np.float32)
        self.o1 = np.zeros(shp, dtype=np.int32)
        self.keepout_track = np.zeros(shp, dtype=bool)
        self.keepout_via = np.zeros((self.ny, self.nx), dtype=bool)
        self.dedge = np.full((self.ny, self.nx), INF, dtype=np.float32)
        self.hist = np.zeros(shp, dtype=np.float32)
        self.reserved = np.zeros(self.L, dtype=bool)   # capas reservadas a planos

        self.radii: List[float] = []
        self.cl: List[np.ndarray] = []
        # huellas dilatadas del cobre enrutado, una por alcance consultado (mm):
        # stamp[Q][l, y, x] = nº de rutas cuyo cobre está a < Q de la celda
        self.stamp: Dict[float, np.ndarray] = {}
        self._disk_cache: Dict[float, Tuple[np.ndarray, np.ndarray]] = {}
        self.pad_cells: Dict[int, np.ndarray] = {}    # id(pad) -> índices planos (l, y, x)
        self.min_radius = min_radius
        self.rmax = self._max_reach()
        self._build_edge()
        self._build_fixed()

    # ------------------------------------------------------------------ util
    def idx(self, l: int, iy: int, ix: int) -> int:
        return (l * self.ny + iy) * self.nx + ix

    def unravel(self, s: int) -> Tuple[int, int, int]:
        l, rem = divmod(s, self.ny * self.nx)
        iy, ix = divmod(rem, self.nx)
        return l, iy, ix

    def cell_xy(self, iy: int, ix: int) -> Tuple[float, float]:
        return float(self.x0 + ix * self.pitch), float(self.y0 + iy * self.pitch)

    def to_cell(self, x: float, y: float) -> Tuple[int, int]:
        ix = int(round((x - self.x0) / self.pitch))
        iy = int(round((y - self.y0) / self.pitch))
        return min(max(iy, 0), self.ny - 1), min(max(ix, 0), self.nx - 1)

    def class_index(self, radius: float) -> int:
        for k, r in enumerate(self.radii):
            if abs(r - radius) < 1e-6:
                return k
        self.radii.append(radius)
        self.cl.append(np.zeros((self.L, self.ny, self.nx), dtype=np.int16))
        return len(self.radii) - 1

    def _max_reach(self) -> float:
        b = self.board
        rad = [nc.track_width / 2 for nc in b.netclasses.values()] + \
              [nc.via_diameter / 2 for nc in b.netclasses.values()]
        cl = [nc.clearance for nc in b.netclasses.values()] + [b.rules.min_clearance, b.rules.copper_edge_clearance]
        return max(rad + [0.3, self.min_radius]) + max(cl + [0.2]) + 3 * self.pitch + 0.1

    def _window(self, bb, margin):
        x0, y0, x1, y1 = bb
        ix0 = max(0, int(math.floor((x0 - margin - self.x0) / self.pitch)))
        ix1 = min(self.nx, int(math.ceil((x1 + margin - self.x0) / self.pitch)) + 1)
        iy0 = max(0, int(math.floor((y0 - margin - self.y0) / self.pitch)))
        iy1 = min(self.ny, int(math.ceil((y1 + margin - self.y0) / self.pitch)) + 1)
        return iy0, iy1, ix0, ix1

    # ------------------------------------------------------------- construcción
    def _build_edge(self):
        b = self.board
        X, Y = np.meshgrid(self.xs, self.ys)
        if b.outline:
            inside = point_in_polygon(X, Y, b.outline)
            d = np.full(X.shape, np.inf)
            n = len(b.outline)
            for i in range(n):
                d = np.minimum(d, _seg_dist(X, Y, b.outline[i], b.outline[(i + 1) % n]))
            self.dedge = np.where(inside, d, 0.0).astype(np.float32)
        else:
            x0, y0, x1, y1 = b.bbox()
            d = np.minimum.reduce([X - x0, x1 - X, Y - y0, y1 - Y])
            self.dedge = np.maximum(d, 0).astype(np.float32)

    def add_fixed_shape(self, layer_mask: Sequence[int], poly, r: float, owner: int):
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        bb = (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)
        iy0, iy1, ix0, ix1 = self._window(bb, self.rmax)
        if iy1 <= iy0 or ix1 <= ix0:
            return
        X, Y = np.meshgrid(self.xs[ix0:ix1], self.ys[iy0:iy1])
        d = shape_distance(X, Y, poly, r).astype(np.float32)
        for l in layer_mask:
            d1 = self.d1[l, iy0:iy1, ix0:ix1]
            d2 = self.d2[l, iy0:iy1, ix0:ix1]
            o1 = self.o1[l, iy0:iy1, ix0:ix1]
            same = o1 == owner
            better = (~same) & (d < d1)
            worse_other = (~same) & (~better) & (d < d2)
            new_d2 = np.where(better, d1, np.where(worse_other, d, d2))
            new_d1 = np.where(same, np.minimum(d1, d), np.where(better, d, d1))
            new_o1 = np.where(better, owner, o1)
            d1[...] = new_d1
            d2[...] = new_d2
            o1[...] = new_o1

    def pad_layers(self, pad: Pad) -> List[int]:
        out = []
        for i, name in enumerate(self.layers):
            if name in pad.layers or "*.Cu" in pad.layers or \
                    (pad.kind in ("thru_hole", "np_thru_hole") and "F&B.Cu" in pad.layers):
                out.append(i)
        if pad.kind == "thru_hole" and not out:
            out = list(range(self.L))
        return out

    def _build_fixed(self):
        b = self.board
        for pad in b.pads:
            lays = self.pad_layers(pad)
            if pad.kind == "np_thru_hole":
                # agujero sin metalizar: obstáculo para todos (el agujero)
                r = max(pad.drill, min(pad.w, pad.h)) / 2
                self.add_fixed_shape(range(self.L), [(pad.x, pad.y)], r, -1)
                continue
            if not lays:
                continue
            poly, r = pad.copper_shape()
            owner = self.net_ids.get(pad.net, -1) if pad.net else -1
            self.add_fixed_shape(lays, poly, r, owner)
            # celdas interiores del pad (orígenes/destinos del enrutado)
            x0, y0, x1, y1 = pad.bbox()
            iy0, iy1, ix0, ix1 = self._window((x0, y0, x1, y1), 0)
            X, Y = np.meshgrid(self.xs[ix0:ix1], self.ys[iy0:iy1])
            inside = shape_distance(X, Y, poly, r) <= 1e-9
            ii = np.argwhere(inside)
            if len(ii) == 0:
                iy, ix = self.to_cell(pad.x, pad.y)
                ii = np.array([[iy - iy0, ix - ix0]])
            cells = []
            for l in lays:
                for (yy, xx) in ii:
                    cells.append(self.idx(l, iy0 + int(yy), ix0 + int(xx)))
            self.pad_cells[id(pad)] = np.array(cells, dtype=np.int64)
            if pad.kind == "thru_hole" and pad.drill > 0:
                pass  # el cobre anular ya cubre el agujero

        for t in b.tracks:
            if t.layer in self.layers:
                owner = self.net_ids.get(t.net, -1) if t.net else -1
                self.add_fixed_shape([self.layers.index(t.layer)], [t.start, t.end], t.width / 2, owner)
        for v in b.vias:
            owner = self.net_ids.get(v.net, -1) if v.net else -1
            l0 = self.layers.index(v.layers[0]) if v.layers[0] in self.layers else 0
            l1 = self.layers.index(v.layers[1]) if v.layers[1] in self.layers else self.L - 1
            self.add_fixed_shape(range(min(l0, l1), max(l0, l1) + 1), [v.pos], v.diameter / 2, owner)
        for z in b.zones:
            if not (z.keepout_tracks or z.keepout_vias) or len(z.polygon) < 3:
                continue
            X, Y = np.meshgrid(self.xs, self.ys)
            inside = point_in_polygon(X, Y, z.polygon)
            for name in z.layers:
                lays = range(self.L) if name in ("*.Cu", "F&B.Cu") else \
                    ([self.layers.index(name)] if name in self.layers else [])
                for l in lays:
                    if z.keepout_tracks:
                        self.keepout_track[l] |= inside
            if z.keepout_vias:
                self.keepout_via |= inside

    # ------------------------------------------------------------- ocupación
    def foreign_distance(self, net_id: int, sl=(slice(None),) * 3) -> np.ndarray:
        d1, d2, o1 = self.d1[sl], self.d2[sl], self.o1[sl]
        return np.where(o1 == net_id, d2, d1)

    def add_route(self, cells_by_class: Dict[int, np.ndarray], sign: int = 1):
        for k, cells in cells_by_class.items():
            if len(cells):
                flat = self.cl[k].reshape(-1)
                np.add.at(flat, cells, sign)

    def ensure_stamp(self, reach: float):
        if reach not in self.stamp:
            self.stamp[reach] = np.zeros((self.L, self.ny, self.nx), dtype=np.int16)

    def add_stamps(self, stamps: Dict[float, np.ndarray], sign: int):
        for Q, idx in stamps.items():
            if len(idx):
                flat = self.stamp[Q].reshape(-1)
                flat[idx] += np.int16(sign)   # índices únicos: no hace falta add.at

    def disk_offsets(self, radius_cells: float) -> Tuple[np.ndarray, np.ndarray]:
        key = round(radius_cells, 4)
        if key not in self._disk_cache:
            dy, dx = np.nonzero(disk(radius_cells))
            r = max(0, int(math.floor(radius_cells)))
            self._disk_cache[key] = (dy.astype(np.int64) - r, dx.astype(np.int64) - r)
        return self._disk_cache[key]

    def congestion_map(self) -> np.ndarray:
        """Suma de ocupación por capa (para visualización)."""
        if not self.cl:
            return np.zeros((self.L, self.ny, self.nx), dtype=np.float32)
        tot = np.zeros((self.L, self.ny, self.nx), dtype=np.float32)
        for c in self.cl:
            tot += c
        return tot
