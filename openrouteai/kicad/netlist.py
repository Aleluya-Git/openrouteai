"""Lectura de netlists de KiCad (formato S-expr, ``kicad-cli sch export netlist``)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..core import sexpr as S


@dataclass
class NetlistComponent:
    ref: str
    value: str = ""
    footprint: str = ""
    lib: str = ""


@dataclass
class Netlist:
    components: Dict[str, NetlistComponent] = field(default_factory=dict)
    nets: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)  # red -> [(ref, pin)]

    def pin_map(self) -> Dict[Tuple[str, str], str]:
        return {node: net for net, nodes in self.nets.items() for node in nodes}


def load_netlist(path: str) -> Netlist:
    tree = S.parse_file(path)
    if S.head(tree) != "export":
        raise ValueError("No es una netlist KiCad (se esperaba '(export ...')")
    nl = Netlist()
    comps = S.find(tree, "components")
    if comps:
        for c in S.find_all(comps, "comp"):
            ref = str(S.value(c, "ref", "?"))
            ls = S.find(c, "libsource")
            nl.components[ref] = NetlistComponent(
                ref=ref,
                value=str(S.value(c, "value", "")),
                footprint=str(S.value(c, "footprint", "")),
                lib=f"{S.value(ls, 'lib', '')}:{S.value(ls, 'part', '')}" if ls else "",
            )
    nets = S.find(tree, "nets")
    if nets:
        for n in S.find_all(nets, "net"):
            name = str(S.value(n, "name", ""))
            nl.nets[name] = [(str(S.value(nd, "ref", "")), str(S.value(nd, "pin", "")))
                             for nd in S.find_all(n, "node")]
    return nl


def compare_with_board(netlist: Netlist, board) -> List[str]:
    """Diferencias entre la netlist del esquema y las redes de la PCB."""
    issues = []
    pcb_map = {(p.footprint.ref, p.number): p.net for p in board.pads}
    refs = {fp.ref for fp in board.footprints}
    for ref in netlist.components:
        if ref not in refs:
            issues.append(f"Componente {ref} está en el esquema pero no en la PCB")
    for ref in refs:
        if ref not in netlist.components and not ref.startswith(("H", "MH", "#", "REF**")):
            issues.append(f"Huella {ref} está en la PCB pero no en el esquema")
    for (ref, pin), net in netlist.pin_map().items():
        pnet = pcb_map.get((ref, pin))
        if pnet is None:
            continue
        if pnet != net and not (net.startswith("unconnected-") and not pnet):
            issues.append(f"Pin {ref}.{pin}: esquema='{net}' PCB='{pnet}'")
    return issues
