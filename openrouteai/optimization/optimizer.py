"""Optimization Engine: búsqueda de los mejores parámetros/orden de enrutado.

Optimiza simultáneamente (suma ponderada) las métricas de calidad:
  conexiones sin enrutar, longitud total, nº de vías, codos (proxy de
  integridad de señal), coste de fabricación y tiempo.

Métodos:
  genetic : algoritmo genético sobre (orden de redes + parámetros del router)
  pso     : Particle Swarm Optimization sobre los parámetros continuos
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional, Tuple

from ..analysis.netlist_analyzer import analyze
from ..core.model import Board
from ..routing.router import Router, RouterConfig, RouteResult


@dataclass
class Weights:
    unrouted: float = 1000.0
    length: float = 1.0
    vias: float = 4.0
    bends: float = 0.2
    time: float = 0.0


def score(res: RouteResult, w: Weights) -> float:
    m = res.metrics()
    return (w.unrouted * (res.connections_total - res.connections_routed) + w.length * res.total_length +
            w.vias * res.via_count + w.bends * m["codos"] + w.time * res.elapsed)


PARAMS = {"via_cost_mm": (0.5, 4.0), "dir_penalty": (0.0, 1.0), "turn_penalty": (0.0, 1.0)}


@dataclass
class OptConfig:
    method: str = "genetic"
    population: int = 6
    generations: int = 4
    inner_strategy: str = "ripup"     # estrategia rápida para evaluar
    seed: int = 1
    weights: Weights = field(default_factory=Weights)
    progress: Optional[Callable[[str, float], None]] = None


class Optimizer:
    def __init__(self, board: Board, base: RouterConfig, cfg: Optional[OptConfig] = None):
        self.b = board
        self.base = base
        self.cfg = cfg or OptConfig()
        self.rnd = random.Random(self.cfg.seed)
        self.an = analyze(board)
        self.history: List[Tuple[float, Dict]] = []

    def _eval(self, genome: Dict) -> Tuple[float, RouteResult]:
        rc = replace(self.base, strategy=self.cfg.inner_strategy, net_order=genome["order"], progress=None,
                     **{k: genome[k] for k in PARAMS})
        res = Router(self.b, rc, self.an).run()
        s = score(res, self.cfg.weights)
        self.history.append((s, {k: v for k, v in genome.items() if k != "order"}))
        return s, res

    def _random(self) -> Dict:
        g = {k: self.rnd.uniform(*r) for k, r in PARAMS.items()}
        order = list(self.an.order)
        # perturbación suave del orden base (mantiene la prioridad aproximada)
        for _ in range(len(order) // 3):
            i = self.rnd.randrange(len(order))
            j = min(len(order) - 1, max(0, i + self.rnd.randint(-3, 3)))
            order[i], order[j] = order[j], order[i]
        g["order"] = order
        return g

    def _cross(self, a: Dict, b: Dict) -> Dict:
        c = {k: (a[k] if self.rnd.random() < 0.5 else b[k]) for k in PARAMS}
        # cruce de orden (OX)
        n = len(a["order"])
        i, j = sorted(self.rnd.sample(range(n), 2)) if n > 1 else (0, 0)
        mid = a["order"][i:j]
        rest = [x for x in b["order"] if x not in mid]
        c["order"] = rest[:i] + mid + rest[i:]
        return c

    def _mutate(self, g: Dict) -> Dict:
        g = dict(g)
        for k, (lo, hi) in PARAMS.items():
            if self.rnd.random() < 0.4:
                g[k] = min(hi, max(lo, g[k] + self.rnd.gauss(0, (hi - lo) * 0.15)))
        order = list(g["order"])
        if len(order) > 1 and self.rnd.random() < 0.6:
            i, j = self.rnd.sample(range(len(order)), 2)
            order.insert(j, order.pop(i))
        g["order"] = order
        return g

    def run(self) -> Tuple[Dict, RouteResult]:
        if self.cfg.method == "pso":
            return self._pso()
        cfg = self.cfg
        base = {k: getattr(self.base, k) for k in PARAMS}
        base["order"] = list(self.an.order)
        pop = [base] + [self._random() for _ in range(cfg.population - 1)]
        scored = []
        total = cfg.population * (cfg.generations + 1)
        n = 0
        for g in pop:
            s, r = self._eval(g)
            scored.append((s, g, r))
            n += 1
            self._prog(f"GA evaluación {n}/{total}: {s:.1f}", n / total)
        for gen in range(cfg.generations):
            scored.sort(key=lambda t: t[0])
            elite = scored[: max(2, cfg.population // 3)]
            children = []
            while len(children) < cfg.population - len(elite):
                a, b = self.rnd.sample(elite, 2) if len(elite) > 1 else (elite[0], elite[0])
                children.append(self._mutate(self._cross(a[1], b[1])))
            new = list(elite)
            for c in children:
                s, r = self._eval(c)
                new.append((s, c, r))
                n += 1
                self._prog(f"GA gen {gen + 1}: evaluación {n}/{total}: {s:.1f}", n / total)
            scored = new
        scored.sort(key=lambda t: t[0])
        best = scored[0]
        # re-ejecutar el mejor con la estrategia elegida por el usuario para dejar la placa en ese estado
        rc = replace(self.base, net_order=best[1]["order"], **{k: best[1][k] for k in PARAMS})
        res = Router(self.b, rc, self.an).run()
        return best[1], res

    def _pso(self) -> Tuple[Dict, RouteResult]:
        cfg = self.cfg
        keys = list(PARAMS)
        parts = [{k: self.rnd.uniform(*PARAMS[k]) for k in keys} for _ in range(cfg.population)]
        vel = [{k: 0.0 for k in keys} for _ in parts]
        pbest = [(float("inf"), None)] * len(parts)
        gbest = (float("inf"), None)
        total = cfg.population * (cfg.generations + 1)
        n = 0
        for it in range(cfg.generations + 1):
            for i, p in enumerate(parts):
                g = dict(p)
                g["order"] = list(self.an.order)
                s, _ = self._eval(g)
                n += 1
                self._prog(f"PSO {n}/{total}: {s:.1f}", n / total)
                if s < pbest[i][0]:
                    pbest[i] = (s, dict(p))
                if s < gbest[0]:
                    gbest = (s, dict(p))
            for i, p in enumerate(parts):
                for k in keys:
                    lo, hi = PARAMS[k]
                    vel[i][k] = 0.6 * vel[i][k] + 1.4 * self.rnd.random() * (pbest[i][1][k] - p[k]) + \
                        1.4 * self.rnd.random() * (gbest[1][k] - p[k])
                    p[k] = min(hi, max(lo, p[k] + vel[i][k]))
        best = dict(gbest[1])
        best["order"] = list(self.an.order)
        rc = replace(self.base, net_order=best["order"], **{k: best[k] for k in keys})
        return best, Router(self.b, rc, self.an).run()

    def _prog(self, msg, frac):
        if self.cfg.progress:
            self.cfg.progress(msg, frac)
