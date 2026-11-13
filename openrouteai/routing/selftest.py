"""Comprobación del núcleo nativo frente a la implementación en Python.

Con penalización de giro 0, Dijkstra y A* deben dar exactamente el mismo
coste óptimo en ambas implementaciones (salvo redondeo float32).
"""
from __future__ import annotations

import random
from typing import Tuple

import numpy as np

from . import native
from .search import search as py_search


def _path_cost(path, cost, vcost, W, H, lm):
    WH = W * H
    tot = 0.0
    moves = {(1, 0): 0, (-1, 0): 1, (0, 1): 2, (0, -1): 3, (1, 1): 4, (-1, -1): 5, (1, -1): 6, (-1, 1): 7}
    for a, b in zip(path, path[1:]):
        la, ra = divmod(a, WH)
        lb, rb = divmod(b, WH)
        if la != lb:
            tot += float(vcost[ra]) + float(cost[b])
            continue
        ya, xa = divmod(ra, W)
        yb, xb = divmod(rb, W)
        d = moves[(xb - xa, yb - ya)]
        tot += (1.0 if d < 4 else 2 ** 0.5) * lm[la][d] * float(cost[b])
    return tot


def compare(trials: int = 30, seed: int = 3) -> Tuple[int, int, str]:
    """Devuelve (coinciden, total, detalle)."""
    if not native.available():
        return 0, 0, native.status()
    rnd = random.Random(seed)
    ns = native.NativeSearcher()
    ok = 0
    worst = 0.0
    for t in range(trials):
        W, H, L = rnd.randint(8, 60), rnd.randint(8, 60), rnd.choice([1, 2, 4])
        rs = np.random.RandomState(seed + t)
        cost = (1 + rs.rand(L, H, W) * 3).astype(np.float32)
        cost[rs.rand(L, H, W) < 0.25] = 1e30
        vcost = (2 + rs.rand(H, W) * 4).astype(np.float32)
        vcost[rs.rand(H, W) < 0.3] = 1e30
        lm = [[1.0, 1.0, 1.3, 1.3, 1.15, 1.15, 1.15, 1.15] for _ in range(L)]
        src = [int(rs.randint(L * W * H)) for _ in range(3)]
        tgt = {int(rs.randint(L * W * H)) for _ in range(3)}
        cf = np.where(cost >= 1e29, np.inf, cost).ravel()
        vf = np.where(vcost >= 1e29, np.inf, vcost).ravel()
        mode = rnd.choice(["astar", "maze"])
        pp = py_search(W, H, L, cf.tolist(), vf.tolist(), src, tgt, mode=mode, layer_mult=lm, turn_penalty=0.0)
        tm = np.zeros(L * W * H, np.uint8)
        tl = np.array(sorted(tgt))
        tm[tl] = 1
        rem = tl % (W * H)
        tbox = (int((rem % W).min()), int((rem % W).max()), int((rem // W).min()), int((rem // W).max()))
        pn = ns.search(W, H, L, cost, vcost, src, tm, mode, np.array(lm, np.float32), 0.0, tbox)
        if pp is None and pn is None:
            ok += 1
            continue
        if (pp is None) != (pn is None):
            continue
        cp_ = _path_cost(pp, cf, vf, W, H, lm)
        cn_ = _path_cost(pn, cf, vf, W, H, lm)
        rel = abs(cp_ - cn_) / max(1.0, cp_)
        worst = max(worst, rel)
        if rel < 1e-4:
            ok += 1
    return ok, trials, f"desviación máx. de coste {worst:.2e}"
