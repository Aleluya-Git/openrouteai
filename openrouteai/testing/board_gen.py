"""Generador de placas de prueba (.kicad_pcb + .kicad_pro) sin enrutar.

Las placas se escriben en formato KiCad 8 (version 20240108), que KiCad 9/10
abren y actualizan sin problemas. Sirven para la autoprueba y como ejemplos.
"""
from __future__ import annotations

import json
import os
import random
import uuid
from typing import Dict, List, Tuple

from ..core import sexpr as S

Q = S.QStr
N = S.fmt_num

# ------------------------------------------------------------------ huellas
# pad: (número, tipo, forma, x, y, w, h, taladro)


def fp_r0805():
    return ("Resistor_SMD:R_0805_2012Metric",
            [("1", "smd", "roundrect", -0.9125, 0, 1.025, 1.4, 0), ("2", "smd", "roundrect", 0.9125, 0, 1.025, 1.4, 0)],
            (-1.68, -0.95, 1.68, 0.95))


def fp_c0603():
    return ("Capacitor_SMD:C_0603_1608Metric",
            [("1", "smd", "roundrect", -0.775, 0, 0.9, 0.95, 0), ("2", "smd", "roundrect", 0.775, 0, 0.9, 0.95, 0)],
            (-1.48, -0.73, 1.48, 0.73))


def fp_soic8():
    pads = []
    ys = [-1.905, -0.635, 0.635, 1.905]
    for i, y in enumerate(ys):
        pads.append((str(i + 1), "smd", "roundrect", -2.475, y, 1.95, 0.6, 0))
    for i, y in enumerate(reversed(ys)):
        pads.append((str(i + 5), "smd", "roundrect", 2.475, y, 1.95, 0.6, 0))
    return "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", pads, (-3.7, -2.7, 3.7, 2.7)


def fp_tqfp32():
    pads = []
    pitch = 0.8
    n = 8
    off = [(k - (n - 1) / 2) * pitch for k in range(n)]
    num = 1
    for y in off:                      # izquierda, de arriba abajo
        pads.append((str(num), "smd", "roundrect", -4.1875, y, 1.475, 0.55, 0)); num += 1
    for x in off:                      # abajo, de izquierda a derecha
        pads.append((str(num), "smd", "roundrect", x, 4.1875, 0.55, 1.475, 0)); num += 1
    for y in reversed(off):            # derecha, de abajo arriba
        pads.append((str(num), "smd", "roundrect", 4.1875, y, 1.475, 0.55, 0)); num += 1
    for x in reversed(off):            # arriba, de derecha a izquierda
        pads.append((str(num), "smd", "roundrect", x, -4.1875, 0.55, 1.475, 0)); num += 1
    return "Package_QFP:TQFP-32_7x7mm_P0.8mm", pads, (-5.2, -5.2, 5.2, 5.2)


def fp_header(n: int):
    pads = []
    for i in range(n):
        pads.append((str(i + 1), "thru_hole", "rect" if i == 0 else "oval", 0, i * 2.54, 1.7, 1.7, 1.0))
    return f"Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical", pads, \
        (-1.8, -1.8, 1.8, (n - 1) * 2.54 + 1.8)


# ------------------------------------------------------------------ escritura
def _uuid():
    return ["uuid", Q(str(uuid.uuid4()))]


def _layers(n_copper: int):
    lay = [["0", Q("F.Cu"), "signal"]]
    for i in range(1, n_copper - 1):
        lay.append([str(i), Q(f"In{i}.Cu"), "signal"])
    lay.append(["31", Q("B.Cu"), "signal"])
    for num, name, alias in [(32, "B.Adhes", "B.Adhesive"), (33, "F.Adhes", "F.Adhesive"), (34, "B.Paste", None),
                             (35, "F.Paste", None), (36, "B.SilkS", "B.Silkscreen"), (37, "F.SilkS", "F.Silkscreen"),
                             (38, "B.Mask", None), (39, "F.Mask", None), (40, "Dwgs.User", "User.Drawings"),
                             (41, "Cmts.User", "User.Comments"), (44, "Edge.Cuts", None), (45, "Margin", None),
                             (46, "B.CrtYd", "B.Courtyard"), (47, "F.CrtYd", "F.Courtyard"),
                             (48, "B.Fab", None), (49, "F.Fab", None)]:
        e = [str(num), Q(name), "user"]
        if alias:
            e.append(Q(alias))
        lay.append(e)
    return ["layers"] + lay


def _font():
    return ["effects", ["font", ["size", "1", "1"], ["thickness", "0.15"]]]


def footprint_node(defn, ref: str, value: str, x: float, y: float, angle: float,
                   pin_nets: Dict[str, str], net_codes: Dict[str, int]):
    lib, pads, cy = defn
    at = ["at", N(x), N(y)] + ([N(angle)] if angle else [])
    node = ["footprint", Q(lib), ["layer", Q("F.Cu")], _uuid(), at,
            ["property", Q("Reference"), Q(ref), ["at", "0", N(cy[1] - 1.0), N(angle)], ["layer", Q("F.SilkS")], _uuid(), _font()],
            ["property", Q("Value"), Q(value), ["at", "0", N(cy[3] + 1.0), N(angle)], ["layer", Q("F.Fab")], _uuid(), _font()],
            ["attr", "smd" if pads[0][1] == "smd" else "through_hole"],
            ["fp_rect", ["start", N(cy[0]), N(cy[1])], ["end", N(cy[2]), N(cy[3])],
             ["stroke", ["width", "0.05"], ["type", "solid"]], ["fill", "none"], ["layer", Q("F.CrtYd")], _uuid()]]
    for num, kind, shape, px, py, w, h, drill in pads:
        pn = ["pad", Q(num), kind, shape, ["at", N(px), N(py)] + ([N(angle)] if angle else []), ["size", N(w), N(h)]]
        if drill:
            pn.append(["drill", N(drill)])
        pn.append(["layers"] + ([Q("*.Cu"), Q("*.Mask")] if kind == "thru_hole" else [Q("F.Cu"), Q("F.Paste"), Q("F.Mask")]))
        if shape == "roundrect":
            pn.append(["roundrect_rratio", "0.25"])
        net = pin_nets.get(num)
        if net:
            pn.append(["net", str(net_codes[net]), Q(net)])
        pn.append(_uuid())
        node.append(pn)
    return node


def write_board(path: str, w: float, h: float, comps: List[Tuple], nets: Dict[Tuple[str, str], str],
                n_copper: int = 2, netclasses=None, patterns=None):
    """comps: [(defn, ref, value, x, y, angle)]; nets: {(ref, pin): red}."""
    names = sorted(set(nets.values()))
    codes = {n: i + 1 for i, n in enumerate(names)}
    tree = ["kicad_pcb", ["version", "20240108"], ["generator", Q("openrouteai")],
            ["generator_version", Q("0.1")],
            ["general", ["thickness", "1.6"], ["legacy_teardrops", "no"]],
            ["paper", Q("A4")], _layers(n_copper),
            ["setup", ["pad_to_mask_clearance", "0"], ["allow_soldermask_bridges_in_footprints", "no"]],
            ["net", "0", Q("")]] + [["net", str(codes[n]), Q(n)] for n in names]
    for defn, ref, value, x, y, ang in comps:
        pin_nets = {pin: net for (r, pin), net in nets.items() if r == ref}
        tree.append(footprint_node(defn, ref, value, x, y, ang, pin_nets, codes))
    tree.append(["gr_rect", ["start", "0", "0"], ["end", N(w), N(h)],
                 ["stroke", ["width", "0.05"], ["type", "default"]], ["fill", "none"],
                 ["layer", Q("Edge.Cuts")], _uuid()])
    S.dump_file(tree, path)

    classes = [dict(name="Default", clearance=0.2, track_width=0.25, via_diameter=0.6, via_drill=0.3,
                    diff_pair_width=0.2, diff_pair_gap=0.25, diff_pair_via_gap=0.25,
                    microvia_diameter=0.3, microvia_drill=0.1, wire_width=6, bus_width=12, line_style=0,
                    pcb_color="rgba(0, 0, 0, 0.000)", schematic_color="rgba(0, 0, 0, 0.000)",
                    priority=2147483647)]
    for nc in netclasses or []:
        d = dict(classes[0])
        d.update(nc)
        d["priority"] = 0
        classes.append(d)
    pro = {
        "board": {"design_settings": {"rules": {
            "min_clearance": 0.0, "min_track_width": 0.0, "min_via_diameter": 0.5,
            "min_through_hole_diameter": 0.3, "min_copper_edge_clearance": 0.5,
            "min_hole_clearance": 0.25, "allow_blind_buried_vias": False, "allow_microvias": False}}},
        "net_settings": {"classes": classes, "meta": {"version": 3},
                         "netclass_patterns": patterns or []},
        "meta": {"filename": os.path.basename(os.path.splitext(path)[0]) + ".kicad_pro", "version": 1},
    }
    with open(os.path.splitext(path)[0] + ".kicad_pro", "w", encoding="utf-8") as fh:
        json.dump(pro, fh, indent=2)


# ------------------------------------------------------------------ placas
def board_simple(path: str):
    """Placa sencilla: SOIC-8, resistencias, condensadores y un conector."""
    comps = [
        (fp_soic8(), "U1", "NE555", 20, 15, 0),
        (fp_header(4), "J1", "Conn_01x04", 4, 10, 0),
        (fp_r0805(), "R1", "10k", 12, 8, 0),
        (fp_r0805(), "R2", "47k", 12, 22, 0),
        (fp_c0603(), "C1", "100n", 30, 10, 90),
        (fp_c0603(), "C2", "10n", 30, 20, 90),
        (fp_r0805(), "R3", "1k", 32, 15, 0),
    ]
    nets = {
        ("J1", "1"): "GND", ("U1", "1"): "GND", ("C1", "2"): "GND", ("C2", "2"): "GND",
        ("J1", "2"): "+5V", ("U1", "8"): "+5V", ("U1", "4"): "+5V", ("R1", "1"): "+5V", ("C1", "1"): "+5V",
        ("R1", "2"): "DIS", ("U1", "7"): "DIS", ("R2", "1"): "DIS",
        ("R2", "2"): "THR", ("U1", "6"): "THR", ("U1", "2"): "THR", ("C2", "1"): "THR",
        ("U1", "3"): "OUT", ("R3", "1"): "OUT", ("R3", "2"): "LED", ("J1", "3"): "LED",
        ("U1", "5"): "CTRL", ("J1", "4"): "CTRL",
    }
    write_board(path, 40, 30, comps, nets,
                netclasses=[dict(name="Power", track_width=0.5, clearance=0.2)],
                patterns=[{"netclass": "Power", "pattern": "GND"}, {"netclass": "Power", "pattern": "+5V"}])


def board_mcu(path: str, n_copper: int = 2):
    """Microcontrolador TQFP-32 con conectores, desacoplos y un par diferencial USB."""
    comps = [(fp_tqfp32(), "U1", "MCU", 30, 25, 0),
             (fp_header(8), "J1", "Conn_01x08", 5, 12, 0),
             (fp_header(8), "J2", "Conn_01x08", 55, 12, 0),
             (fp_header(4), "J3", "USB", 30, 41, 90)]
    nets: Dict[Tuple[str, str], str] = {}
    # desacoplos alrededor del chip
    for i, (x, y, a) in enumerate([(22, 17, 0), (38, 17, 0), (22, 33, 0), (38, 33, 0)]):
        comps.append((fp_c0603(), f"C{i + 1}", "100n", x, y, a))
        nets[(f"C{i + 1}", "1")] = "+3V3"
        nets[(f"C{i + 1}", "2")] = "GND"
    for p in ("4", "12", "20", "28"):
        nets[("U1", p)] = "+3V3"
    for p in ("5", "13", "21", "29"):
        nets[("U1", p)] = "GND"
    # señales a J1 (pines 1-3, 6-8 del MCU) y J2 (17-19, 22-24)
    sig_l = ["1", "2", "3", "6", "7", "8"]
    for k, p in enumerate(sig_l):
        nets[("U1", p)] = f"PA{k}"
        nets[("J1", str(k + 2))] = f"PA{k}"
    sig_r = ["17", "18", "19", "22", "23", "24"]
    for k, p in enumerate(sig_r):
        nets[("U1", p)] = f"PB{k}"
        nets[("J2", str(k + 2))] = f"PB{k}"
    nets[("J1", "1")] = "GND"
    nets[("J2", "1")] = "GND"
    nets[("J1", "8")] = "+3V3"
    nets[("J2", "8")] = "+3V3"
    # USB: par diferencial
    nets[("U1", "10")] = "USB_D+"
    nets[("U1", "11")] = "USB_D-"
    nets[("J3", "2")] = "USB_D+"
    nets[("J3", "3")] = "USB_D-"
    nets[("J3", "1")] = "VBUS"
    nets[("J3", "4")] = "GND"
    # resistencias pull-up en el lateral superior
    for i, p in enumerate(["25", "26", "27"]):
        comps.append((fp_r0805(), f"R{i + 1}", "4k7", 24 + i * 6, 8, 90))
        nets[("U1", p)] = f"I2C{i}"
        nets[(f"R{i + 1}", "1")] = "+3V3"
        nets[(f"R{i + 1}", "2")] = f"I2C{i}"
    write_board(path, 60, 46, comps, nets, n_copper=n_copper,
                netclasses=[dict(name="Power", track_width=0.4, clearance=0.2),
                            dict(name="USB", track_width=0.25, clearance=0.2, diff_pair_width=0.25,
                                 diff_pair_gap=0.2)],
                patterns=[{"netclass": "Power", "pattern": "GND"}, {"netclass": "Power", "pattern": "+3V3"},
                          {"netclass": "Power", "pattern": "VBUS"}, {"netclass": "USB", "pattern": "USB_*"}])


def board_dense(path: str, seed: int = 7, n_comp: int = 24, n_nets: int = 30, cols: int = 6, n_copper: int = 2):
    """Placa densa con conexiones aleatorias (reproducible por semilla)."""
    rnd = random.Random(seed)
    comps = []
    pins = []
    for i in range(n_comp):
        defn = rnd.choice([fp_r0805(), fp_c0603(), fp_r0805(), fp_soic8()])
        x = 8 + (i % cols) * 11
        y = 8 + (i // cols) * 11
        ang = rnd.choice([0, 90])
        ref = ("U" if len(defn[1]) > 2 else ("R" if "R_" in defn[0] else "C")) + str(i + 1)
        comps.append((defn, ref, "X", x, y, ang))
        pins += [(ref, p[0]) for p in defn[1]]
    rnd.shuffle(pins)
    nets = {}
    k = 0
    for n in range(n_nets):
        size = rnd.choice([2, 2, 2, 3, 3, 4])
        for _ in range(size):
            if k < len(pins):
                nets[pins[k]] = f"N{n:02d}"
                k += 1
    write_board(path, 8 + cols * 11, 8 + ((n_comp + cols - 1) // cols) * 11, comps, nets, n_copper=n_copper)


def generate_all(folder: str) -> List[str]:
    os.makedirs(folder, exist_ok=True)
    out = []
    p = os.path.join(folder, "prueba_simple.kicad_pcb"); board_simple(p); out.append(p)
    p = os.path.join(folder, "prueba_mcu.kicad_pcb"); board_mcu(p); out.append(p)
    p = os.path.join(folder, "prueba_mcu_4capas.kicad_pcb"); board_mcu(p, n_copper=4); out.append(p)
    p = os.path.join(folder, "prueba_densa.kicad_pcb"); board_dense(p); out.append(p)
    return out
