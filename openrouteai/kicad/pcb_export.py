"""KiCad Export Engine: reescribe el .kicad_pcb original con el enrutado nuevo.

Se conserva el árbol S-expr original completo; solo se:
  - actualizan posiciones de huellas movidas por el colocador (si procede)
  - eliminan pistas/vías originales (si ``keep_existing`` es False)
  - añaden ``segment``, ``via`` y ``zone`` nuevos con el mismo estilo de red
    (códigos numéricos o nombres) que el archivo de entrada.
"""
from __future__ import annotations

import copy
import uuid
from typing import List, Optional

from ..core import sexpr as S
from ..core.model import Board, Track, Via, Zone

Q = S.QStr
N = S.fmt_num


def _net_ref(board: Board, net: str) -> list:
    if board.net_style == "named":
        return ["net", Q(net)]
    n = board.nets.get(net)
    return ["net", str(n.code if n else 0)]


def _uuid() -> list:
    return ["uuid", Q(str(uuid.uuid4()))]


def track_node(board: Board, t: Track) -> list:
    return ["segment", ["start", N(t.start[0]), N(t.start[1])], ["end", N(t.end[0]), N(t.end[1])],
            ["width", N(t.width)], ["layer", Q(t.layer)], _net_ref(board, t.net), _uuid()]


def via_node(board: Board, v: Via) -> list:
    node = ["via"]
    if v.kind == "blind_buried":
        node.append("blind")
    elif v.kind == "micro":
        node.append("micro")
    node += [["at", N(v.x), N(v.y)], ["size", N(v.diameter)], ["drill", N(v.drill)],
             ["layers", Q(v.layers[0]), Q(v.layers[1])], _net_ref(board, v.net), _uuid()]
    return node


def zone_node(board: Board, z: Zone) -> list:
    node = ["zone", _net_ref(board, z.net)]
    node.append(["net_name", Q(z.net)])
    from ..zones_fill import fracture, zone_clearance
    node += [["layer", Q(z.layers[0])], _uuid(), ["hatch", "edge", "0.5"],
             # conexión sólida de pads: es como calcula el relleno OpenRouteAI
             ["connect_pads", "yes", ["clearance", N(zone_clearance(board, z.net))]],
             ["min_thickness", "0.25"], ["filled_areas_thickness", "no"],
             ["fill", "yes", ["thermal_gap", "0.5"], ["thermal_bridge_width", "0.5"],
              ["island_removal_mode", "0"]],
             ["polygon", ["pts"] + [["xy", N(x), N(y)] for x, y in z.polygon]]]
    # relleno ya calculado: KiCad lo muestra sin tener que pulsar B
    for outer, holes in getattr(z, "filled", None) or []:
        ring = fracture(outer, holes)
        node.append(["filled_polygon", ["layer", Q(z.layers[0])],
                     ["pts"] + [["xy", N(x), N(y)] for x, y in ring]])
    return node


def update_footprint_positions(board: Board) -> None:
    for fp in board.footprints:
        if fp.node is None:
            continue
        at = S.find(fp.node, "at")
        if at is None:
            continue
        old = (S.fnum(at[1]), S.fnum(at[2]), S.fnum(at[3]) if len(at) > 3 and not isinstance(at[3], list) else 0.0)
        if abs(old[0] - fp.x) > 1e-6 or abs(old[1] - fp.y) > 1e-6 or abs(old[2] - fp.angle) > 1e-6:
            rest = [x for x in at[3:] if isinstance(x, list) or x == "unlocked"] if len(at) > 3 else []
            at[:] = ["at", N(fp.x), N(fp.y)] + ([N(fp.angle)] if fp.angle else []) + rest
            # los ángulos absolutos de los pads deben seguir a la huella
            d = fp.angle - old[2]
            if abs(d) > 1e-9:
                for pn in S.find_all(fp.node, "pad"):
                    pat = S.find(pn, "at")
                    a = (S.fnum(pat[3]) if len(pat) > 3 else 0.0) + d
                    a = ((a + 180) % 360) - 180
                    pat[:] = pat[:3] + ([N(a)] if abs(a) > 1e-9 else [])
                for pr in S.find_all(fp.node, "property") + S.find_all(fp.node, "fp_text"):
                    pat = S.find(pr, "at")
                    if pat is not None and len(pat) >= 3:
                        a = (S.fnum(pat[3]) if len(pat) > 3 and not isinstance(pat[3], list) else 0.0) + d
                        tail = [x for x in pat[3:] if not (isinstance(x, str) and _isnum(x))]
                        pat[:] = pat[:3] + [N(a)] + tail


_FLIP_XY = ("start", "end", "center", "mid", "xy", "offset")


def _flip_children(node: list) -> None:
    """Refleja (y → −y) las coordenadas locales de todo lo que cuelga de una huella."""
    from ..core.model import flip_layer
    for c in node[1:]:
        if not isinstance(c, list) or not c:
            continue
        tag = c[0]
        if tag in ("model", "zone", "dimension"):
            continue
        if tag in _FLIP_XY and len(c) >= 3 and _isnum(str(c[2])):
            c[2] = N(-S.fnum(c[2]))
            continue
        if tag == "at" and len(c) >= 3:
            c[2] = N(-S.fnum(c[2]))
            continue
        if tag in ("layer", "layers"):
            c[1:] = [Q(flip_layer(str(x))) if isinstance(x, str) and not isinstance(x, list) else x for x in c[1:]]
            continue
        if tag == "pad":
            at = S.find(c, "at")
            if at is not None and len(at) >= 3:
                a = S.fnum(at[3]) if len(at) > 3 and _isnum(str(at[3])) else 0.0
                a = 180.0 - a
                a = ((a + 180) % 360) - 180
                at[:] = ["at", N(S.fnum(at[1])), N(-S.fnum(at[2]))] + ([N(a)] if abs(a) > 1e-9 else [])
            _flip_children([None] + [x for x in c[1:] if not (isinstance(x, list) and x and x[0] == "at")])
            continue
        if tag in ("property", "fp_text"):
            at = S.find(c, "at")
            if at is not None and len(at) >= 3:
                a = S.fnum(at[3]) if len(at) > 3 and _isnum(str(at[3])) else 0.0
                a = -a
                a = ((a + 180) % 360) - 180
                tail = [x for x in at[3:] if not (isinstance(x, str) and _isnum(x))]
                at[:] = ["at", N(S.fnum(at[1])), N(-S.fnum(at[2]))] + ([N(a)] if abs(a) > 1e-9 or tail else []) + tail
            eff = S.find(c, "effects")
            if eff is not None:
                j = S.find(eff, "justify")
                if j is None:
                    eff.append(["justify", "mirror"])
                elif "mirror" in j[1:]:
                    j[:] = [x for x in j if x != "mirror"]
                    if len(j) == 1:
                        eff.remove(j)
                else:
                    j.append("mirror")
            _flip_children([None] + [x for x in c[1:] if not (isinstance(x, list) and x and x[0] in ("at", "effects"))])
            continue
        _flip_children(c)          # fp_line, fp_poly (pts → xy), fp_arc, fp_circle, fp_rect, zonas...


def sync_footprint_sides(board: Board) -> int:
    """Aplica al árbol S-expr los cambios de cara (F.Cu ↔ B.Cu) hechos en el modelo. Idempotente."""
    from ..core.model import flip_layer
    n = 0
    for fp in board.footprints:
        if fp.node is None:
            continue
        ln = S.find(fp.node, "layer")
        if ln is None or str(ln[1]) == fp.layer or flip_layer(str(ln[1])) != fp.layer:
            continue
        ln[1] = Q(fp.layer)
        _flip_children([None] + [c for c in fp.node[1:] if not (isinstance(c, list) and c and c[0] in ("at", "layer"))])
        at = S.find(fp.node, "at")
        if at is not None:
            a = S.fnum(at[3]) if len(at) > 3 and _isnum(str(at[3])) else 0.0
            a = 180.0 - a
            a = ((a + 180) % 360) - 180
            rest = [x for x in at[3:] if isinstance(x, list) or x == "unlocked"]
            at[:] = ["at", at[1], at[2]] + ([N(a)] if abs(a) > 1e-9 else []) + rest
        # en KiCad, la propiedad «attr» y los textos de la otra cara se leen en espejo: hecho arriba
        n += 1
    return n


def update_zone_fills(board: Board) -> None:
    """Escribe en las zonas del archivo original el relleno recalculado por OpenRouteAI."""
    from ..zones_fill import fracture
    for z in board.zones:
        if z.node is None or not z.refilled:
            continue
        z.node[:] = [c for c in z.node if not (isinstance(c, list) and c and c[0] in ("filled_polygon", "fill_segments"))]
        lay = z.layers[0] if z.layers else "F.Cu"
        for outer, holes in z.filled:
            ring = fracture(outer, holes)
            z.node.append(["filled_polygon", ["layer", Q(lay)], ["pts"] + [["xy", N(x), N(y)] for x, y in ring]])


def _isnum(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def export_board(board: Board, path: str, new_tracks: List[Track], new_vias: List[Via],
                 new_zones: Optional[List[Zone]] = None, keep_existing: bool = True,
                 placement_changed: bool = False) -> None:
    if board.tree is None:
        raise ValueError("La placa no procede de un archivo .kicad_pcb")
    sync_footprint_sides(board)          # huellas pasadas a la otra cara
    if placement_changed:
        update_footprint_positions(board)
    update_zone_fills(board)
    tree = copy.deepcopy(board.tree)   # se puede exportar varias veces sin duplicar
    if not keep_existing:
        tree[:] = [tree[0]] + [c for c in tree[1:] if not (isinstance(c, list) and c and c[0] in ("segment", "arc", "via"))]
    # los nuevos elementos se insertan antes de los textos finales / grupos
    insert_at = len(tree)
    for i, c in enumerate(tree):
        if isinstance(c, list) and c and c[0] in ("embedded_fonts", "group", "generated"):
            insert_at = i
            break
    items = [track_node(board, t) for t in new_tracks] + [via_node(board, v) for v in new_vias]
    items += [zone_node(board, z) for z in (new_zones or [])]
    tree[insert_at:insert_at] = items
    S.dump_file(tree, path)
