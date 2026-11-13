"""Modelo de datos interno de OpenRouteAI.

La placa se representa con objetos ligeros (huellas, pads, redes, pistas, vías)
más los grafos y matrices que construye el analizador de netlist y el router:
  - grafo de componentes, grafo de conexiones (``analysis.netlist_analyzer``)
  - grafo espacial / matriz de ocupación / mapa de congestión (``routing.grid``)

Todas las coordenadas en milímetros, eje Y hacia abajo (convención KiCad).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import Point, rect_poly, rotate

COPPER_ORDER_HINT = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, 31)] + ["B.Cu"]


@dataclass
class NetClass:
    name: str = "Default"
    clearance: float = 0.2
    track_width: float = 0.25
    via_diameter: float = 0.6
    via_drill: float = 0.3
    diff_pair_width: float = 0.2
    diff_pair_gap: float = 0.25
    priority: int = 0


@dataclass
class DesignRules:
    min_clearance: float = 0.0
    min_track_width: float = 0.0
    min_via_diameter: float = 0.0
    min_via_drill: float = 0.0
    copper_edge_clearance: float = 0.5
    hole_clearance: float = 0.25
    allow_blind_buried: bool = False
    allow_microvias: bool = False
    # Restricciones de longitud: patrón (regex de red) -> (min, max) mm
    length_rules: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    diff_pair_max_skew: float = 0.5


@dataclass
class Net:
    code: int
    name: str
    netclass: str = "Default"
    kind: str = "signal"          # signal | power | ground | diff | highspeed
    priority: int = 0
    pair: Optional[str] = None    # nombre de la red compañera (pares diferenciales)
    pads: List["Pad"] = field(default_factory=list)


@dataclass
class Pad:
    """Nodo eléctrico. Contiene posición, capas, restricciones, prioridad y clase de red."""
    footprint: "Footprint"
    number: str
    kind: str                     # smd | thru_hole | np_thru_hole | connect
    shape: str                    # rect | roundrect | circle | oval | trapezoid | custom
    x: float
    y: float
    angle: float
    w: float
    h: float
    layers: List[str]
    net: str = ""
    drill: float = 0.0
    rratio: float = 0.0
    lx: float = 0.0               # posición local en la huella (sin rotar)
    ly: float = 0.0
    rel_angle: float = 0.0        # orientación relativa a la huella

    @property
    def pos(self) -> Point:
        return (self.x, self.y)

    def on_layer(self, layer: str) -> bool:
        return layer in self.layers

    def copper_shape(self) -> Tuple[List[Point], float]:
        """Forma de cobre como (polígono convexo, radio de redondeo)."""
        if self.shape == "circle":
            return [(self.x, self.y)], self.w / 2
        if self.shape == "oval":
            if abs(self.w - self.h) < 1e-9:
                return [(self.x, self.y)], self.w / 2
            if self.w > self.h:
                d = (self.w - self.h) / 2
                a, b = rotate(-d, 0, self.angle), rotate(d, 0, self.angle)
                r = self.h / 2
            else:
                d = (self.h - self.w) / 2
                a, b = rotate(0, -d, self.angle), rotate(0, d, self.angle)
                r = self.w / 2
            return [(self.x + a[0], self.y + a[1]), (self.x + b[0], self.y + b[1])], r
        if self.shape == "roundrect" and self.rratio > 0:
            r = min(self.w, self.h) * self.rratio
            return rect_poly(self.x, self.y, max(self.w - 2 * r, 1e-6), max(self.h - 2 * r, 1e-6), self.angle), r
        return rect_poly(self.x, self.y, self.w, self.h, self.angle), 0.0

    def bbox(self) -> Tuple[float, float, float, float]:
        poly, r = self.copper_shape()
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r

    @property
    def ref(self) -> str:
        return f"{self.footprint.ref}.{self.number}"


def flip_layer(name: str) -> str:
    """F.xxx ↔ B.xxx (las demás capas no cambian)."""
    if name.startswith("F."):
        return "B." + name[2:]
    if name.startswith("B."):
        return "F." + name[2:]
    return name


def _norm180(a: float) -> float:
    a = ((a + 180.0) % 360.0) - 180.0
    return 0.0 if abs(a) < 1e-9 else a


@dataclass
class FpText:
    """Texto de una huella (referencia o valor), en coordenadas locales."""
    kind: str                     # reference | value
    text: str
    lx: float
    ly: float
    rel_angle: float              # ángulo relativo a la huella
    layer: str
    size: float = 1.0             # altura del texto (mm)
    hidden: bool = False


@dataclass
class Footprint:
    ref: str
    value: str
    lib_id: str
    x: float
    y: float
    angle: float
    layer: str
    pads: List[Pad] = field(default_factory=list)
    courtyard: Optional[Tuple[float, float, float, float]] = None  # bbox absoluto
    locked: bool = False
    node: Optional[list] = None   # nodo S-expr original
    local_courtyard: Optional[List[Point]] = None
    graphics: List[Tuple[str, List[Point]]] = field(default_factory=list)   # (capa, polilínea local)
    texts: List[FpText] = field(default_factory=list)

    def _to_abs(self, x: float, y: float) -> Point:
        rx, ry = rotate(x, y, self.angle)
        return self.x + rx, self.y + ry

    def abs_graphics(self) -> List[Tuple[str, List[Point]]]:
        """Dibujo de la huella (serigrafía, fabricación, courtyard) en coordenadas de placa."""
        return [(layer, [self._to_abs(x, y) for x, y in pts]) for layer, pts in self.graphics]

    def abs_texts(self) -> List[Tuple[FpText, float, float, float]]:
        """(texto, x, y, ángulo absoluto) de referencia y valor."""
        out = []
        for t in self.texts:
            x, y = self._to_abs(t.lx, t.ly)
            out.append((t, x, y, self.angle + t.rel_angle))
        return out

    @property
    def is_back(self) -> bool:
        return self.layer == "B.Cu"

    def set_pose(self, x: float, y: float, angle: float) -> None:
        """Mueve/rota la huella y recalcula pads y courtyard."""
        self.x, self.y, self.angle = x, y, ((angle + 180) % 360) - 180
        for p in self.pads:
            rx, ry = rotate(p.lx, p.ly, self.angle)
            p.x, p.y = x + rx, y + ry
            p.angle = self.angle + p.rel_angle
        if self.local_courtyard:
            pts = [rotate(px, py, self.angle) for px, py in self.local_courtyard]
            self.courtyard = (x + min(q[0] for q in pts), y + min(q[1] for q in pts),
                              x + max(q[0] for q in pts), y + max(q[1] for q in pts))

    def flip(self) -> None:
        """Pasa la huella a la otra cara (como KiCad, en espejo izquierda-derecha sobre su origen).

        En el archivo de KiCad las coordenadas locales de una huella de la cara inferior están
        reflejadas en Y y el ángulo pasa a 180°-ángulo: el resultado en la placa es un espejo
        horizontal de la huella alrededor de su posición.
        """
        self.layer = flip_layer(self.layer)
        for p in self.pads:
            p.ly = -p.ly
            p.rel_angle = _norm180(-p.rel_angle)
            p.layers = [flip_layer(l) for l in p.layers]
        self.graphics = [(flip_layer(l), [(x, -y) for x, y in pts]) for l, pts in self.graphics]
        for t in self.texts:
            t.ly = -t.ly
            t.rel_angle = _norm180(-t.rel_angle - 180.0)
            t.layer = flip_layer(t.layer)
        if self.local_courtyard:
            self.local_courtyard = [(x, -y) for x, y in self.local_courtyard]
        self.set_pose(self.x, self.y, 180.0 - self.angle)

    def bbox(self) -> Tuple[float, float, float, float]:
        if self.courtyard:
            return self.courtyard
        boxes = [p.bbox() for p in self.pads] or [(self.x - 0.5, self.y - 0.5, self.x + 0.5, self.y + 0.5)]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))


@dataclass
class Track:
    start: Point
    end: Point
    width: float
    layer: str
    net: str
    fixed: bool = False           # pista existente en el diseño original

    @property
    def length(self) -> float:
        return math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])


@dataclass
class Via:
    x: float
    y: float
    diameter: float
    drill: float
    layers: Tuple[str, str]       # capa superior / inferior (pasante = F.Cu, B.Cu)
    net: str
    kind: str = "through"         # through | blind_buried | micro
    fixed: bool = False

    @property
    def pos(self) -> Point:
        return (self.x, self.y)


@dataclass
class Zone:
    net: str
    layers: List[str]
    polygon: List[Point]
    keepout_tracks: bool = False
    keepout_vias: bool = False
    filled: List = field(default_factory=list)      # relleno calculado: [(exterior, [agujeros])]
    stale: bool = False                              # relleno desactualizado tras editar
    refilled: bool = False                           # relleno recalculado por OpenRouteAI
    node: Optional[list] = field(default=None, repr=False, compare=False)   # nodo S-expr original


@dataclass
class Board:
    path: str = ""
    copper_layers: List[str] = field(default_factory=lambda: ["F.Cu", "B.Cu"])
    footprints: List[Footprint] = field(default_factory=list)
    nets: Dict[str, Net] = field(default_factory=dict)
    netclasses: Dict[str, NetClass] = field(default_factory=lambda: {"Default": NetClass()})
    rules: DesignRules = field(default_factory=DesignRules)
    tracks: List[Track] = field(default_factory=list)
    vias: List[Via] = field(default_factory=list)
    zones: List[Zone] = field(default_factory=list)
    outline: List[Point] = field(default_factory=list)   # polígono del borde (Edge.Cuts)
    tree: Optional[list] = None   # árbol S-expr completo del .kicad_pcb
    net_style: str = "numbered"   # numbered (KiCad <=9) | named (sin códigos de red)
    track_widths: List[float] = field(default_factory=list)              # lista predefinida del .kicad_pro
    via_sizes: List[Tuple[float, float]] = field(default_factory=list)   # (Ø, taladro)
    version: str = ""

    # ------------------------------------------------------------------ util
    @property
    def pads(self) -> List[Pad]:
        return [p for fp in self.footprints for p in fp.pads]

    def netclass_of(self, net: str) -> NetClass:
        n = self.nets.get(net)
        if n and n.netclass in self.netclasses:
            return self.netclasses[n.netclass]
        return self.netclasses.get("Default", NetClass())

    def clearance_between(self, net_a: str, net_b: str) -> float:
        c = max(self.netclass_of(net_a).clearance, self.netclass_of(net_b).clearance)
        return max(c, self.rules.min_clearance)

    def bbox(self) -> Tuple[float, float, float, float]:
        if self.outline:
            xs = [p[0] for p in self.outline]
            ys = [p[1] for p in self.outline]
            return min(xs), min(ys), max(xs), max(ys)
        boxes = [fp.bbox() for fp in self.footprints]
        if not boxes:
            return 0, 0, 100, 100
        m = 2.0
        return (min(b[0] for b in boxes) - m, min(b[1] for b in boxes) - m,
                max(b[2] for b in boxes) + m, max(b[3] for b in boxes) + m)

    def routable_nets(self) -> List[Net]:
        """Redes con dos o más pads de cobre."""
        return [n for n in self.nets.values() if n.name and len(n.pads) >= 2]

    def clear_routing(self, keep_fixed: bool = True) -> None:
        self.tracks = [t for t in self.tracks if keep_fixed and t.fixed]
        self.vias = [v for v in self.vias if keep_fixed and v.fixed]

    def layer_index(self, name: str) -> int:
        return self.copper_layers.index(name)


def sort_copper_layers(names: Sequence[str]) -> List[str]:
    def key(n):
        return COPPER_ORDER_HINT.index(n) if n in COPPER_ORDER_HINT else 999
    return sorted(set(names), key=key)
