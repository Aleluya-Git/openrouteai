"""Cálculo de los campos de coste de una ventana de búsqueda.

La misma función sirve para CPU (``xp = numpy``) y GPU (``xp = cupy``): todas
las operaciones son elementales sobre arrays densos, que es justo lo que una
GPU acelera bien en placas grandes.
"""
from __future__ import annotations

from typing import Optional, Sequence

INF = 1e30


def window_costs(xp, d1, d2, o1, dedge, keep_t, keep_v, stamp_t, stamp_v, hist, *,
                 nid: int, hw: float, rv: float, c: float, ec: float, slack: float,
                 blocked_layers: Sequence[int], pres: Optional[float], via_cost_cells: float):
    """Devuelve (cost[L,H,W], via_cost[H,W]) en float32 en el dispositivo de ``xp``.

    d1/d2/o1: campos de distancia al cobre fijo; dedge: distancia al borde;
    keep_*: zonas de exclusión; stamp_*: huella dilatada del cobre enrutado
    (alcance de pista y de vía de esta red); hist: coste histórico (PathFinder).
    """
    F = xp.float32
    dfor = xp.where(o1 == nid, d2, d1)
    hard = (dfor >= F(hw + c + slack)) & (dedge >= F(hw + ec))[None] & ~keep_t
    for li in blocked_layers:
        hard[li] = False
    via_ok = ((dfor >= F(rv + c + slack)) & (dedge >= F(rv + ec))[None]).all(axis=0) & ~keep_v
    occv = stamp_v.max(axis=0)
    hmax = hist.max(axis=0)
    one, inf = F(1.0), F(INF)
    if pres is None:
        cost = xp.where(hard & (stamp_t == 0), one + hist, inf)
        vcost = xp.where(via_ok & (occv == 0), F(via_cost_cells) + hmax, inf)
    else:
        p = F(pres)
        cost = xp.where(hard, (one + hist) * (one + p * stamp_t.astype(F)), inf)
        vcost = xp.where(via_ok, F(via_cost_cells) * (one + p * occv.astype(F)) + hmax, inf)
    return cost.astype(F, copy=False), vcost.astype(F, copy=False)
