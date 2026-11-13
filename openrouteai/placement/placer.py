"""Component Placement Engine (opcional).

Recocido simulado (simulated annealing) que minimiza:
  - longitud de cableado estimada (HPWL por red; masa/alimentación con peso reducido)
  - solape de courtyards (penalización fuerte)
  - salirse del contorno de la placa (penalización fuerte)

Movimientos: desplazamiento, intercambio de dos huellas y rotación de 90°.
Las huellas bloqueadas (``locked``) y, opcionalmente, los conectores no se mueven.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ..core.model import Board, Footprint


@dataclass
class PlacerConfig:
    iterations: int = 20000
    seed: int = 1
    fix_connectors: bool = True
    grid: float = 0.5
    margin: float = 1.0               # separación extra entre courtyards (mm)
    power_weight: float = 0.2
    progress: Optional[Callable[[str, float], None]] = None


def _is_connector(fp: Footprint) -> bool:
    return fp.ref.startswith(("J", "P", "CN", "X")) or "Connector" in fp.lib_id or "Conn" in fp.value


class Placer:
    def __init__(self, board: Board, cfg: Optional[PlacerConfig] = None):
        self.b = board
        self.cfg = cfg or PlacerConfig()
        self.rnd = random.Random(self.cfg.seed)
        self.movable = [fp for fp in board.footprints if not fp.locked and fp.pads and
                        not (self.cfg.fix_connectors and _is_connector(fp))]
        self.bx0, self.by0, self.bx1, self.by1 = board.bbox()
        self.nets = [n for n in board.routable_nets()]
        self.fp_nets: Dict[int, List[int]] = {}
        for i, n in enumerate(self.nets):
            for p in n.pads:
                self.fp_nets.setdefault(id(p.footprint), []).append(i)
        self.w_net = [self.cfg.power_weight if n.kind in ("power", "ground") else 1.0 for n in self.nets]

    # ----------------------------------------------------------- coste
    def hpwl(self, i: int) -> float:
        n = self.nets[i]
        xs = [p.x for p in n.pads]
        ys = [p.y for p in n.pads]
        return ((max(xs) - min(xs)) + (max(ys) - min(ys))) * self.w_net[i]

    def overlap(self, fp: Footprint) -> float:
        m = self.cfg.margin
        a = fp.bbox()
        pen = 0.0
        for o in self.b.footprints:
            if o is fp:
                continue
            b = o.bbox()
            dx = min(a[2], b[2]) - max(a[0], b[0]) + m
            dy = min(a[3], b[3]) - max(a[1], b[1]) + m
            if dx > 0 and dy > 0:
                pen += dx * dy
        # fuera de placa
        e = self.b.rules.copper_edge_clearance + 0.5
        out = max(0, self.bx0 + e - a[0]) + max(0, a[2] - (self.bx1 - e)) + \
            max(0, self.by0 + e - a[1]) + max(0, a[3] - (self.by1 - e))
        return pen + out * 10

    def total(self) -> float:
        return sum(self.hpwl(i) for i in range(len(self.nets))) + \
            50 * sum(self.overlap(fp) for fp in self.movable) / 2

    def _local(self, fps: List[Footprint]) -> float:
        nets = set()
        for fp in fps:
            nets.update(self.fp_nets.get(id(fp), []))
        return sum(self.hpwl(i) for i in nets) + 50 * sum(self.overlap(fp) for fp in fps)

    # ----------------------------------------------------------- recocido
    def run(self) -> Dict[str, float]:
        if not self.movable:
            return {"antes": 0, "despues": 0, "movidas": 0}
        cfg = self.cfg
        start = self.total()
        span = max(self.bx1 - self.bx0, self.by1 - self.by0)
        T0 = span * 0.5
        n_it = cfg.iterations
        for k in range(n_it):
            T = T0 * (1 - k / n_it) ** 2 + 0.01
            mv = self.rnd.random()
            if mv < 0.15 and len(self.movable) > 1:
                a, b = self.rnd.sample(self.movable, 2)
                before = self._local([a, b])
                pa, pb = (a.x, a.y, a.angle), (b.x, b.y, b.angle)
                a.set_pose(pb[0], pb[1], a.angle)
                b.set_pose(pa[0], pa[1], b.angle)
                after = self._local([a, b])
                if after > before and self.rnd.random() > math.exp(-(after - before) / T):
                    a.set_pose(*pa)
                    b.set_pose(*pb)
            else:
                fp = self.rnd.choice(self.movable)
                old = (fp.x, fp.y, fp.angle)
                before = self._local([fp])
                if mv < 0.25:
                    fp.set_pose(fp.x, fp.y, fp.angle + 90)
                else:
                    r = max(0.5, T)
                    fp.set_pose(fp.x + self.rnd.uniform(-r, r), fp.y + self.rnd.uniform(-r, r), fp.angle)
                after = self._local([fp])
                if after > before and self.rnd.random() > math.exp(-(after - before) / T):
                    fp.set_pose(*old)
            if cfg.progress and k % 2000 == 0:
                cfg.progress(f"Colocación: iteración {k}/{n_it}", k / n_it)
        # ajuste a rejilla (solo si no crea solapes)
        for fp in self.movable:
            old = (fp.x, fp.y, fp.angle)
            ov = self.overlap(fp)
            g = cfg.grid
            fp.set_pose(round(fp.x / g) * g, round(fp.y / g) * g, round(fp.angle / 90) * 90)
            if self.overlap(fp) > ov + 1e-9:
                fp.set_pose(*old)
        end = self.total()
        ov = sum(self.overlap(fp) for fp in self.movable)
        return {"antes": round(start, 2), "despues": round(end, 2), "movidas": len(self.movable),
                "solape_residual": round(ov, 3)}
