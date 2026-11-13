"""Análisis de esquemas .kicad_sch.

Extrae símbolos (referencia, valor, huella) y reconstruye la conectividad de
una hoja: pines (a partir de las ``lib_symbols`` incrustadas), cables, uniones,
etiquetas locales/globales y símbolos de alimentación.

Limitaciones (fase 1): las hojas jerárquicas se listan pero no se siguen; para
diseños jerárquicos la fuente fiable es la netlist exportada con
``kicad-cli sch export netlist`` (ver ``kicad.netlist``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..core import sexpr as S
from ..core.geometry import point_seg_dist, rotate
from .netlist import Netlist, NetlistComponent


@dataclass
class SchSymbol:
    ref: str
    value: str
    footprint: str
    lib_id: str
    x: float
    y: float
    angle: float
    unit: int = 1
    is_power: bool = False
    pins: List[Tuple[str, float, float]] = field(default_factory=list)  # (número, x, y)


@dataclass
class Schematic:
    path: str
    symbols: List[SchSymbol] = field(default_factory=list)
    sheets: List[str] = field(default_factory=list)
    netlist: Netlist = field(default_factory=Netlist)
    warnings: List[str] = field(default_factory=list)


def _xy(n):
    return S.fnum(n[1]), S.fnum(n[2])


class _UF:
    def __init__(self):
        self.p: Dict = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _key(x, y):
    return (round(x * 1000), round(y * 1000))


def load_schematic(path: str) -> Schematic:
    tree = S.parse_file(path)
    if S.head(tree) != "kicad_sch":
        raise ValueError("No es un archivo .kicad_sch")
    sch = Schematic(path=path)

    libs: Dict[str, list] = {}
    ls = S.find(tree, "lib_symbols")
    if ls:
        for sym in S.find_all(ls, "symbol"):
            libs[str(sym[1])] = sym

    for sh in S.find_all(tree, "sheet"):
        for pr in S.find_all(sh, "property"):
            if str(pr[1]) in ("Sheetfile", "Sheet file"):
                sch.sheets.append(os.path.join(os.path.dirname(path), str(pr[2])))
    if sch.sheets:
        sch.warnings.append("El esquema tiene hojas jerárquicas: use la netlist exportada para la conectividad completa.")

    # ---- símbolos
    for sym in S.find_all(tree, "symbol"):
        lib_id = str(S.value(sym, "lib_id", ""))
        at = S.find(sym, "at")
        x, y = _xy(at)
        ang = S.fnum(at[3]) if len(at) > 3 else 0.0
        mirror = S.value(sym, "mirror")
        unit = int(S.fnum(S.value(sym, "unit", 1), 1))
        props = {str(p[1]): str(p[2]) for p in S.find_all(sym, "property") if len(p) > 2}
        lib = libs.get(lib_id)
        is_power = bool(lib and S.find(lib, "power") is not None)
        s = SchSymbol(ref=props.get("Reference", "?"), value=props.get("Value", ""),
                      footprint=props.get("Footprint", ""), lib_id=lib_id,
                      x=x, y=y, angle=ang, unit=unit, is_power=is_power)
        if lib is not None:
            for sub in [lib] + S.find_all(lib, "symbol"):
                name = str(sub[1])
                parts = name.rsplit("_", 2)
                if sub is not lib and len(parts) == 3 and parts[1].isdigit():
                    u = int(parts[1])
                    if u not in (0, unit):
                        continue
                for pin in S.find_all(sub, "pin"):
                    pat = S.find(pin, "at")
                    px, py = S.fnum(pat[1]), -S.fnum(pat[2])   # librería: Y hacia arriba
                    rx, ry = rotate(px, py, ang)
                    if mirror == "x":
                        ry = -ry
                    elif mirror == "y":
                        rx = -rx
                    num = str(S.value(pin, "number", "?"))
                    s.pins.append((num, x + rx, y + ry))
        sch.symbols.append(s)

    # ---- conectividad
    uf = _UF()
    wires: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for w in S.find_all(tree, "wire"):
        pts = [_xy(xy) for xy in S.find_all(S.find(w, "pts"), "xy")]
        for i in range(len(pts) - 1):
            wires.append((pts[i], pts[i + 1]))
            uf.union(_key(*pts[i]), _key(*pts[i + 1]))

    def attach(pt):
        k = _key(*pt)
        uf.find(k)
        for a, b in wires:
            if point_seg_dist(pt, a, b) < 1e-3:
                uf.union(k, _key(*a))
        return k

    # extremos de cable que caen sobre el tramo de otro cable (unión en T)
    for a, b in list(wires):
        attach(a)
        attach(b)

    labels: Dict[str, List] = {}
    for tag in ("label", "global_label", "hierarchical_label"):
        for lb in S.find_all(tree, tag):
            k = attach(_xy(S.find(lb, "at")))
            labels.setdefault(str(lb[1]), []).append(k)
    for j in S.find_all(tree, "junction"):
        attach(_xy(S.find(j, "at")))
    for name, ks in labels.items():
        for k in ks[1:]:
            uf.union(ks[0], k)

    pin_keys: List[Tuple[SchSymbol, str, tuple]] = []
    power_nets: Dict[str, List] = {}
    for s in sch.symbols:
        for num, px, py in s.pins:
            k = attach((px, py))
            pin_keys.append((s, num, k))
            if s.is_power:
                power_nets.setdefault(s.value, []).append(k)
    for name, ks in power_nets.items():
        for k in ks[1:]:
            uf.union(ks[0], k)

    # nombres
    root_name: Dict = {}
    for name, ks in list(labels.items()) + list(power_nets.items()):
        root_name.setdefault(uf.find(ks[0]), name)

    groups: Dict = {}
    for s, num, k in pin_keys:
        if s.is_power:
            continue
        groups.setdefault(uf.find(k), []).append((s.ref, num))
    nl = sch.netlist
    for s in sch.symbols:
        if not s.is_power and not s.ref.startswith("#"):
            nl.components[s.ref] = NetlistComponent(s.ref, s.value, s.footprint, s.lib_id)
    for root, nodes in groups.items():
        name = root_name.get(root)
        if not name:
            if len(nodes) < 2:
                r, p = nodes[0]
                name = f"unconnected-({r}-Pad{p})"
            else:
                r, p = sorted(nodes)[0]
                name = f"Net-({r}-Pad{p})"
        nl.nets.setdefault(name, []).extend(nodes)
    return sch


def compare_connectivity(sch_netlist: Netlist, board) -> Tuple[int, int, List[str]]:
    """Compara particiones de pines (independiente de nombres de red).

    Devuelve (redes coincidentes, redes totales del esquema con >=2 pines, diferencias).
    """
    pcb_groups: Dict[str, Set] = {}
    for p in board.pads:
        if p.net:
            pcb_groups.setdefault(p.net, set()).add((p.footprint.ref, p.number))
    pcb_sets = {frozenset(v) for v in pcb_groups.values()}
    ok, total, diffs = 0, 0, []
    for name, nodes in sch_netlist.nets.items():
        if len(nodes) < 2:
            continue
        total += 1
        if frozenset(nodes) in pcb_sets:
            ok += 1
        else:
            diffs.append(f"{name}: {sorted(nodes)[:6]}")
    return ok, total, diffs
