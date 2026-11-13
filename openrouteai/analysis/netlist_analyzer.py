"""Netlist Analyzer: clasificación de redes y construcción de grafos.

Produce:
  - grafo de componentes (peso = nº de redes compartidas)
  - grafo de conexiones (árbol de expansión mínima por red, define qué pares de pads unir)
  - clasificación de redes: ground, power, diff, highspeed, signal
  - orden/prioridad de enrutado
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..core.model import Board, Net, Pad

GND_RE = re.compile(r"^(/)?(A|D|P|S|CHASSIS|EARTH)?(GND|VSS|0V)\w*$", re.I)
PWR_RE = re.compile(r"^(/)?([+-]?\d+V\d*|V(CC|DD|BAT|BUS|IN|SYS|MOT|REF)\w*|\+\w+|PWR\w*|VIN|3V3|5V)$", re.I)
DIFF_RE = re.compile(r"^(.*?)(_?)(P|N|\+|-|_P|_N|DP|DM)$", re.I)
HS_HINTS = ("USB", "HDMI", "LVDS", "DDR", "PCIE", "ETH", "MIPI", "SATA", "CLK", "RF", "HS")


@dataclass
class Connection:
    """Una arista del grafo de conexiones: dos pads de la misma red a unir."""
    net: str
    a: Pad
    b: Pad

    @property
    def length(self) -> float:
        return math.hypot(self.a.x - self.b.x, self.a.y - self.b.y)


@dataclass
class Analysis:
    connections: Dict[str, List[Connection]] = field(default_factory=dict)
    component_graph: Dict[Tuple[str, str], int] = field(default_factory=dict)
    diff_pairs: List[Tuple[str, str]] = field(default_factory=list)
    order: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


def classify_net(net: Net, board: Board) -> str:
    name = net.name.split("/")[-1]
    cls = net.netclass.upper()
    if GND_RE.match(name):
        return "ground"
    if PWR_RE.match(name) or cls in ("POWER", "PWR"):
        return "power"
    if any(h in name.upper() or h in cls for h in HS_HINTS):
        return "highspeed"
    return "signal"


def _pair_key(name: str):
    """Para 'USB_D+' devuelve ('USB_D', '+'); None si no parece miembro de un par."""
    for pos, neg in (("_P", "_N"), ("+", "-"), ("DP", "DM")):
        if name.endswith(pos):
            return name[: -len(pos)], (pos, neg), 0
        if name.endswith(neg):
            return name[: -len(neg)], (pos, neg), 1
    return None


def find_diff_pairs(board: Board) -> List[Tuple[str, str]]:
    names = set(board.nets)
    pairs = []
    seen = set()
    for n in sorted(names):
        k = _pair_key(n)
        if not k:
            continue
        base, (pos, neg), idx = k
        if idx != 0:
            continue
        mate = base + neg
        if mate in names and n not in seen and base and not base.endswith(("+", "-")):
            pairs.append((n, mate))
            seen.update((n, mate))
    return pairs


def mst_connections(net: Net) -> List[Connection]:
    """Árbol de expansión mínima (Prim) sobre los pads de la red."""
    pads = net.pads
    if len(pads) < 2:
        return []
    # pads ya unidos por solape (p. ej. pads repetidos del mismo pin) se tratan igual
    in_tree = [False] * len(pads)
    best = [math.inf] * len(pads)
    parent = [-1] * len(pads)
    best[0] = 0
    out = []
    for _ in range(len(pads)):
        i = min((k for k in range(len(pads)) if not in_tree[k]), key=lambda k: best[k])
        in_tree[i] = True
        if parent[i] >= 0:
            out.append(Connection(net.name, pads[parent[i]], pads[i]))
        for j in range(len(pads)):
            if not in_tree[j]:
                d = abs(pads[i].x - pads[j].x) + abs(pads[i].y - pads[j].y)
                if d < best[j]:
                    best[j], parent[j] = d, i
    return out


def analyze(board: Board) -> Analysis:
    an = Analysis()
    for net in board.nets.values():
        net.kind = classify_net(net, board)
    an.diff_pairs = find_diff_pairs(board)
    for a, b in an.diff_pairs:
        board.nets[a].kind = board.nets[b].kind = "diff"
        board.nets[a].pair, board.nets[b].pair = b, a

    for net in board.routable_nets():
        an.connections[net.name] = mst_connections(net)
        refs = sorted({p.footprint.ref for p in net.pads})
        if net.kind in ("signal", "highspeed", "diff"):
            for i in range(len(refs)):
                for j in range(i + 1, len(refs)):
                    key = (refs[i], refs[j])
                    an.component_graph[key] = an.component_graph.get(key, 0) + 1

    # Prioridad: pares diferenciales y alta velocidad primero, luego redes cortas;
    # las de alimentación/masa (anchas) antes que señal larga para reservar sitio.
    kind_rank = {"diff": 0, "highspeed": 1, "power": 2, "ground": 3, "signal": 4}
    for net in board.routable_nets():
        cls = board.netclass_of(net.name)
        net.priority = kind_rank.get(net.kind, 4) * 10 - cls.priority

    def span(n: Net):
        xs = [p.x for p in n.pads]
        ys = [p.y for p in n.pads]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    an.order = [n.name for n in sorted(board.routable_nets(), key=lambda n: (n.priority, span(n)))]
    kinds: Dict[str, int] = {}
    for n in board.routable_nets():
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    an.stats = {
        "componentes": len(board.footprints),
        "pads": len(board.pads),
        "redes": len([n for n in board.nets.values() if n.name]),
        "redes_enrutables": len(board.routable_nets()),
        "conexiones": sum(len(v) for v in an.connections.values()),
        "pares_diferenciales": len(an.diff_pairs),
        **{f"redes_{k}": v for k, v in kinds.items()},
    }
    return an
