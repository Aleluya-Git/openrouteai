"""Algoritmos de búsqueda de camino sobre la rejilla (ventana local).

Estrategias:
  astar   - A* con heurística octil y penalización de giros
  maze    - Maze routing ponderado (Dijkstra, sin heurística)
  lee     - Algoritmo de Lee: expansión de onda BFS de coste unitario
  hadlock - Algoritmo de Hadlock: 0-1 BFS minimizando el "detour number"

Representación de la ventana: estado ``s = (l*H + y)*W + x``.
``cost[s]``: coste de entrar en la celda (``inf`` = prohibida).
``via_cost[y*W+x]``: coste de una vía en esa posición (``inf`` = no se permite).
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from typing import Iterable, List, Optional, Sequence, Set

INF = float("inf")
SQ2 = math.sqrt(2.0)
# (dx, dy, longitud, índice de dirección)
MOVES = [(1, 0, 1.0, 0), (-1, 0, 1.0, 1), (0, 1, 1.0, 2), (0, -1, 1.0, 3),
         (1, 1, SQ2, 4), (-1, -1, SQ2, 5), (1, -1, SQ2, 6), (-1, 1, SQ2, 7)]


def search(W: int, H: int, L: int, cost: Sequence[float], via_cost: Sequence[float],
           sources: Iterable[int], targets: Set[int], mode: str = "astar",
           layer_mult: Optional[List[List[float]]] = None, turn_penalty: float = 0.1,
           via_span_ok=None, max_expansions: int = 4_000_000) -> Optional[List[int]]:
    """Devuelve la lista de estados desde un origen hasta un destino, o None."""
    if not targets:
        return None
    sources = [s for s in sources if cost[s] < INF or s in targets]
    if not sources:
        return None
    for s in sources:
        if s in targets:
            return [s]

    WH = W * H
    # caja de destinos para la heurística
    txs, tys = [], []
    tlayers = set()
    for t in targets:
        l, r = divmod(t, WH)
        y, x = divmod(r, W)
        txs.append(x)
        tys.append(y)
        tlayers.add(l)
    tx0, tx1, ty0, ty1 = min(txs), max(txs), min(tys), max(tys)

    if layer_mult is None:
        layer_mult = [[1.0] * 8 for _ in range(L)]
    step = [[MOVES[d][2] * layer_mult[l][d] for d in range(8)] for l in range(L)]

    if mode == "lee":
        return _lee(W, H, L, cost, via_cost, sources, targets, via_span_ok)
    if mode == "hadlock":
        return _hadlock(W, H, L, cost, via_cost, sources, targets, (tx0, tx1, ty0, ty1), via_span_ok)

    use_h = mode == "astar"
    N = L * WH
    g = [INF] * N
    parent = [-1] * N
    pdir = bytearray(b"\xff") * N
    heap = []
    for s in sources:
        g[s] = 0.0
        heapq.heappush(heap, (0.0, s))
    expansions = 0
    while heap:
        f, s = heapq.heappop(heap)
        gs = g[s]
        if s in targets:
            return _backtrack(parent, s)
        if use_h:
            l, r = divmod(s, WH)
            y, x = divmod(r, W)
            ddx = tx0 - x if x < tx0 else (x - tx1 if x > tx1 else 0)
            ddy = ty0 - y if y < ty0 else (y - ty1 if y > ty1 else 0)
            hmin = abs(ddx - ddy) + SQ2 * (ddx if ddx < ddy else ddy)
            if f > gs + hmin + 1e-9:
                continue  # entrada obsoleta
        else:
            if f > gs + 1e-9:
                continue
            l, r = divmod(s, WH)
            y, x = divmod(r, W)
        expansions += 1
        if expansions > max_expansions:
            return None
        pd = pdir[s]
        stl = step[l]
        base = l * WH
        for dx, dy, _, d in MOVES:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= W or ny >= H:
                continue
            n = base + ny * W + nx
            c = cost[n]
            if c == INF:
                continue
            if dx and dy:
                # no cortar esquinas entre dos celdas prohibidas
                if cost[base + y * W + nx] == INF and cost[base + ny * W + x] == INF:
                    continue
            ng = gs + stl[d] * c
            if pd != 255 and pd != d:
                ng += turn_penalty
            if ng < g[n]:
                g[n] = ng
                parent[n] = s
                pdir[n] = d
                if use_h:
                    ddx = tx0 - nx if nx < tx0 else (nx - tx1 if nx > tx1 else 0)
                    ddy = ty0 - ny if ny < ty0 else (ny - ty1 if ny > ty1 else 0)
                    h = abs(ddx - ddy) + SQ2 * (ddx if ddx < ddy else ddy)
                    heapq.heappush(heap, (ng + h, n))
                else:
                    heapq.heappush(heap, (ng, n))
        vc = via_cost[r]
        if vc < INF and L > 1:
            for l2 in range(L):
                if l2 == l:
                    continue
                if via_span_ok is not None and not via_span_ok(r, l, l2):
                    continue
                n = l2 * WH + r
                c = cost[n]
                if c == INF:
                    continue
                ng = gs + vc + c
                if ng < g[n]:
                    g[n] = ng
                    parent[n] = s
                    pdir[n] = 255
                    if use_h:
                        ddx = tx0 - x if x < tx0 else (x - tx1 if x > tx1 else 0)
                        ddy = ty0 - y if y < ty0 else (y - ty1 if y > ty1 else 0)
                        h = abs(ddx - ddy) + SQ2 * (ddx if ddx < ddy else ddy)
                        heapq.heappush(heap, (ng + h, n))
                    else:
                        heapq.heappush(heap, (ng, n))
    return None


def _backtrack(parent: List[int], s: int) -> List[int]:
    path = [s]
    while parent[s] >= 0:
        s = parent[s]
        path.append(s)
    path.reverse()
    return path


def _neighbors(W, H, L, s, cost, via_cost, via_span_ok):
    WH = W * H
    l, r = divmod(s, WH)
    y, x = divmod(r, W)
    base = l * WH
    for dx, dy, _, d in MOVES:
        nx, ny = x + dx, y + dy
        if 0 <= nx < W and 0 <= ny < H:
            n = base + ny * W + nx
            if cost[n] == INF:
                continue
            if dx and dy and cost[base + y * W + nx] == INF and cost[base + ny * W + x] == INF:
                continue
            yield n, nx, ny, False
    if via_cost[r] < INF:
        for l2 in range(L):
            if l2 != l and (via_span_ok is None or via_span_ok(r, l, l2)):
                n = l2 * WH + r
                if cost[n] < INF:
                    yield n, x, y, True


def _lee(W, H, L, cost, via_cost, sources, targets, via_span_ok):
    N = L * W * H
    parent = [-2] * N
    q = deque()
    for s in sources:
        parent[s] = -1
        q.append(s)
    while q:
        s = q.popleft()
        if s in targets:
            return _backtrack(parent, s)
        for n, _, _, _ in _neighbors(W, H, L, s, cost, via_cost, via_span_ok):
            if parent[n] == -2:
                parent[n] = s
                q.append(n)
    return None


def _hadlock(W, H, L, cost, via_cost, sources, targets, tbox, via_span_ok):
    tx0, tx1, ty0, ty1 = tbox
    WH = W * H

    def md(x, y):
        dx = tx0 - x if x < tx0 else (x - tx1 if x > tx1 else 0)
        dy = ty0 - y if y < ty0 else (y - ty1 if y > ty1 else 0)
        return max(dx, dy)  # distancia de Chebyshev (movimientos en 8 direcciones)

    N = L * WH
    det = [INF] * N
    parent = [-1] * N
    dq = deque()
    for s in sources:
        det[s] = 0
        dq.append(s)
    while dq:
        s = dq.popleft()
        if s in targets:
            return _backtrack(parent, s)
        l, r = divmod(s, WH)
        y, x = divmod(r, W)
        here = md(x, y)
        for n, nx, ny, is_via in _neighbors(W, H, L, s, cost, via_cost, via_span_ok):
            w = 1 if (is_via or md(nx, ny) >= here) else 0
            nd = det[s] + w
            if nd < det[n]:
                det[n] = nd
                parent[n] = s
                if w == 0:
                    dq.appendleft(n)
                else:
                    dq.append(n)
    return None
