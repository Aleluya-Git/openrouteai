"""KiCad Import Engine: lectura de .kicad_pcb (formatos KiCad 6 a 10+).

Admite tanto el estilo clásico con códigos de red ``(net 3 "GND")`` como el
estilo sin códigos ``(net "GND")``; el estilo detectado se guarda en
``Board.net_style`` para que la exportación escriba en el mismo formato.
"""
from __future__ import annotations

import math
import os
import re
from typing import Dict, List, Optional, Tuple

from ..core import sexpr as S
from ..core.geometry import arc_points, rotate
from ..core.model import (Board, DesignRules, Footprint, FpText, Net, NetClass, Pad, Track, Via,
                          Zone, sort_copper_layers)
from . import project as kproject


def _xy(node) -> Tuple[float, float]:
    return S.fnum(node[1]), S.fnum(node[2])


def _net_of(node: list, codes: Dict[str, str]) -> Tuple[str, Optional[str]]:
    """Devuelve (nombre de red, estilo) a partir de un hijo ``(net ...)``."""
    n = S.find(node, "net")
    if n is None:
        name = S.value(node, "net_name")
        return (str(name) if name is not None else ""), None
    if len(n) >= 3:
        return str(n[2]), "numbered"
    if len(n) == 2:
        v = n[1]
        if not isinstance(v, S.QStr) and re.fullmatch(r"-?\d+", str(v)):
            return codes.get(str(v), ""), "numbered"
        return str(v), "named"
    return "", None


def load_board(path: str, project_path: Optional[str] = None) -> Board:
    tree = S.parse_file(path)
    if S.head(tree) != "kicad_pcb":
        raise ValueError(f"{path} no es un archivo .kicad_pcb")
    b = Board(path=path, tree=tree)
    b.version = str(S.value(tree, "version", ""))

    # ---- capas de cobre
    layers_node = S.find(tree, "layers")
    copper = []
    if layers_node:
        for ln in layers_node[1:]:
            if isinstance(ln, list) and len(ln) >= 2 and str(ln[1]).endswith(".Cu"):
                copper.append(str(ln[1]))
    b.copper_layers = sort_copper_layers(copper) or ["F.Cu", "B.Cu"]

    # ---- redes declaradas (estilo clásico)
    codes: Dict[str, str] = {}
    for n in S.find_all(tree, "net"):
        if len(n) >= 3:
            codes[str(n[1])] = str(n[2])
            if str(n[2]):
                b.nets[str(n[2])] = Net(code=int(S.fnum(n[1])), name=str(n[2]))

    styles = set()

    # ---- clases de red heredadas (KiCad 5)
    for nc in S.find_all(tree, "net_class"):
        cls = NetClass(name=str(nc[1]),
                       clearance=S.fnum(S.value(nc, "clearance"), 0.2),
                       track_width=S.fnum(S.value(nc, "trace_width"), 0.25),
                       via_diameter=S.fnum(S.value(nc, "via_dia"), 0.6),
                       via_drill=S.fnum(S.value(nc, "via_drill"), 0.3))
        b.netclasses[cls.name] = cls
        for an in S.find_all(nc, "add_net"):
            nm = str(an[1])
            b.nets.setdefault(nm, Net(code=len(b.nets) + 1, name=nm)).netclass = cls.name

    # ---- huellas
    for fpn in S.find_all(tree, "footprint") + S.find_all(tree, "module"):
        fp = _parse_footprint(fpn, codes, styles)
        b.footprints.append(fp)
        for p in fp.pads:
            if p.net and any(l.endswith(".Cu") or l == "*.Cu" for l in p.layers):
                net = b.nets.get(p.net)
                if net is None:
                    net = b.nets[p.net] = Net(code=len(b.nets) + 1, name=p.net)
                net.pads.append(p)

    # ---- pistas / vías existentes (quedan fijas)
    for seg in S.find_all(tree, "segment"):
        net, st = _net_of(seg, codes)
        styles.add(st)
        b.tracks.append(Track(_xy(S.find(seg, "start")), _xy(S.find(seg, "end")),
                              S.fnum(S.value(seg, "width"), 0.25), str(S.value(seg, "layer", "F.Cu")),
                              net, fixed=True))
    for arc in S.find_all(tree, "arc"):
        net, st = _net_of(arc, codes)
        pts = arc_points(_xy(S.find(arc, "start")), _xy(S.find(arc, "mid")), _xy(S.find(arc, "end")))
        w = S.fnum(S.value(arc, "width"), 0.25)
        lay = str(S.value(arc, "layer", "F.Cu"))
        for i in range(len(pts) - 1):
            b.tracks.append(Track(pts[i], pts[i + 1], w, lay, net, fixed=True))
    for v in S.find_all(tree, "via"):
        net, st = _net_of(v, codes)
        styles.add(st)
        at = S.find(v, "at")
        lay = S.find(v, "layers")
        kind = "through"
        if "blind" in v[1:3] or "buried" in v[1:3]:
            kind = "blind_buried"
        elif "micro" in v[1:3]:
            kind = "micro"
        layers = (str(lay[1]), str(lay[2])) if lay and len(lay) >= 3 else (b.copper_layers[0], b.copper_layers[-1])
        b.vias.append(Via(S.fnum(at[1]), S.fnum(at[2]), S.fnum(S.value(v, "size"), 0.6),
                          S.fnum(S.value(v, "drill"), 0.3), layers, net, kind, fixed=True))

    # ---- zonas (solo nos interesan las de exclusión como obstáculo)
    for z in S.find_all(tree, "zone"):
        net, st = _net_of(z, codes)
        lays = []
        if S.find(z, "layer"):
            lays = [str(S.value(z, "layer"))]
        elif S.find(z, "layers"):
            lays = [str(x) for x in S.find(z, "layers")[1:]]
        keep = S.find(z, "keepout")
        poly = S.find(z, "polygon")
        pts = []
        if poly and S.find(poly, "pts"):
            pts = [_xy(xy) for xy in S.find_all(S.find(poly, "pts"), "xy")]
        zone = Zone(net=net, layers=lays, polygon=pts, node=z)
        # relleno guardado (anillos «fracturados»: un solo contorno con los agujeros unidos)
        for fpn in S.find_all(z, "filled_polygon"):
            fl = str(S.value(fpn, "layer", lays[0] if lays else ""))
            if lays and fl != lays[0]:
                continue
            ptsn = S.find(fpn, "pts")
            if ptsn is not None:
                ring = [_xy(xy) for xy in S.find_all(ptsn, "xy")]
                if len(ring) >= 3:
                    zone.filled.append((ring, []))
        if keep:
            zone.keepout_tracks = S.value(keep, "tracks") == "not_allowed"
            zone.keepout_vias = S.value(keep, "vias") == "not_allowed"
        b.zones.append(zone)

    for p in b.pads:
        pass
    styles.discard(None)
    if "named" in styles:
        b.net_style = "named"
    elif not codes and styles != {"numbered"}:
        b.net_style = "named"
    else:
        b.net_style = "numbered"

    b.outline = _board_outline(tree)

    # ---- reglas del proyecto
    if project_path is None:
        cand = os.path.splitext(path)[0] + ".kicad_pro"
        project_path = cand if os.path.exists(cand) else None
    if project_path:
        kproject.apply_project(b, project_path)
    return b


def _parse_footprint(fpn: list, codes: Dict[str, str], styles: set) -> Footprint:
    lib_id = str(fpn[1]) if len(fpn) > 1 and not isinstance(fpn[1], list) else ""
    at = S.find(fpn, "at")
    fx, fy = S.fnum(at[1]), S.fnum(at[2])
    fa = S.fnum(at[3]) if len(at) > 3 else 0.0
    layer = str(S.value(fpn, "layer", "F.Cu"))
    ref, val = "?", ""
    for pr in S.find_all(fpn, "property"):
        if str(pr[1]) == "Reference":
            ref = str(pr[2])
        elif str(pr[1]) == "Value":
            val = str(pr[2])
    for t in S.find_all(fpn, "fp_text"):
        if len(t) > 2 and t[1] == "reference":
            ref = str(t[2])
        elif len(t) > 2 and t[1] == "value":
            val = str(t[2])
    fp = Footprint(ref=ref, value=val, lib_id=lib_id, x=fx, y=fy, angle=fa, layer=layer, node=fpn)
    fp.locked = "locked" in fpn[1:4] or S.value(fpn, "locked") == "yes"

    for pn in S.find_all(fpn, "pad"):
        number = str(pn[1])
        kind = str(pn[2])
        shape = str(pn[3])
        pat = S.find(pn, "at")
        px, py = S.fnum(pat[1]), S.fnum(pat[2])
        pa = S.fnum(pat[3]) if len(pat) > 3 else 0.0
        rx, ry = rotate(px, py, fa)
        size = S.find(pn, "size")
        w, h = S.fnum(size[1]), S.fnum(size[2])
        layers_n = S.find(pn, "layers")
        layers = [str(x) for x in layers_n[1:]] if layers_n else []
        drill = 0.0
        dn = S.find(pn, "drill")
        if dn:
            nums = [S.fnum(x) for x in dn[1:] if not isinstance(x, list) and x != "oval"]
            drill = nums[0] if nums else 0.0
        net, st = _net_of(pn, codes)
        styles.add(st)
        pad = Pad(footprint=fp, number=number, kind=kind, shape=shape,
                  x=fx + rx, y=fy + ry, angle=pa, w=w, h=h, layers=layers, net=net, drill=drill,
                  rratio=S.fnum(S.value(pn, "roundrect_rratio"), 0.25 if shape == "roundrect" else 0.0),
                  lx=px, ly=py, rel_angle=pa - fa)
        if shape == "custom":
            pad.shape = "rect"
        fp.pads.append(pad)

    _parse_fp_drawing(fpn, fp, fa)

    # courtyard
    pts = []
    for tag in ("fp_line", "fp_rect", "fp_poly", "fp_circle"):
        for g in S.find_all(fpn, tag):
            if not str(S.value(g, "layer", "")).endswith("CrtYd"):
                continue
            if tag in ("fp_line", "fp_rect"):
                s, e = _xy(S.find(g, "start")), _xy(S.find(g, "end"))
                pts += [s, e, (s[0], e[1]), (e[0], s[1])]
            elif tag == "fp_circle":
                c, e = _xy(S.find(g, "center")), _xy(S.find(g, "end"))
                r = math.hypot(e[0] - c[0], e[1] - c[1])
                pts += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r), (c[0] - r, c[1] + r), (c[0] + r, c[1] - r)]
            else:
                ptsn = S.find(g, "pts")
                if ptsn:
                    pts += [_xy(xy) for xy in S.find_all(ptsn, "xy")]
    if pts:
        fp.local_courtyard = list(pts)
        absp = [(fx + rotate(x, y, fa)[0], fy + rotate(x, y, fa)[1]) for x, y in pts]
        fp.courtyard = (min(p[0] for p in absp), min(p[1] for p in absp),
                        max(p[0] for p in absp), max(p[1] for p in absp))
    return fp


GRAPHIC_LAYERS = ("SilkS", "Silkscreen", "Fab", "CrtYd", "Courtyard")


def _is_hidden(node: list) -> bool:
    if "hide" in node[1:]:
        return True
    if S.value(node, "hide") == "yes":
        return True
    eff = S.find(node, "effects")
    if eff is not None and ("hide" in eff[1:] or S.value(eff, "hide") == "yes"):
        return True
    return False


def _parse_fp_drawing(fpn: list, fp: Footprint, fa: float) -> None:
    """Lee serigrafía/fabricación/courtyard y los textos de referencia y valor."""
    for g in fpn[1:]:
        if not isinstance(g, list) or g[0] not in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"):
            continue
        layer = str(S.value(g, "layer", ""))
        if not any(k in layer for k in GRAPHIC_LAYERS):
            continue
        t = g[0]
        try:
            if t == "fp_line":
                fp.graphics.append((layer, [_xy(S.find(g, "start")), _xy(S.find(g, "end"))]))
            elif t == "fp_rect":
                (x0, y0), (x1, y1) = _xy(S.find(g, "start")), _xy(S.find(g, "end"))
                fp.graphics.append((layer, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]))
            elif t == "fp_circle":
                c, e = _xy(S.find(g, "center")), _xy(S.find(g, "end"))
                r = math.hypot(e[0] - c[0], e[1] - c[1])
                n = 24
                fp.graphics.append((layer, [(c[0] + r * math.cos(2 * math.pi * k / n),
                                             c[1] + r * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]))
            elif t == "fp_arc":
                if S.find(g, "mid") is not None:
                    fp.graphics.append((layer, arc_points(_xy(S.find(g, "start")), _xy(S.find(g, "mid")),
                                                          _xy(S.find(g, "end")), seg_len=0.3)))
                else:   # KiCad 5: start = centro, end = inicio, angle
                    c, st = _xy(S.find(g, "start")), _xy(S.find(g, "end"))
                    ang = math.radians(S.fnum(S.value(g, "angle"), 90))
                    r = math.hypot(st[0] - c[0], st[1] - c[1])
                    a0 = math.atan2(st[1] - c[1], st[0] - c[0])
                    fp.graphics.append((layer, [(c[0] + r * math.cos(a0 + ang * k / 12),
                                                 c[1] + r * math.sin(a0 + ang * k / 12)) for k in range(13)]))
            elif t == "fp_poly":
                ptsn = S.find(g, "pts")
                if ptsn is not None:
                    pts = [_xy(xy) for xy in S.find_all(ptsn, "xy")]
                    if pts:
                        fp.graphics.append((layer, pts + [pts[0]]))
        except (TypeError, IndexError):
            continue

    def add_text(kind, text, node):
        at = S.find(node, "at")
        if at is None:
            return
        size = 1.0
        eff = S.find(node, "effects")
        font = S.find(eff, "font") if eff is not None else None
        sz = S.find(font, "size") if font is not None else None
        if sz is not None and len(sz) >= 2:
            size = S.fnum(sz[1], 1.0)
        ang = S.fnum(at[3]) if len(at) > 3 and not isinstance(at[3], list) else 0.0
        fp.texts.append(FpText(kind=kind, text=str(text), lx=S.fnum(at[1]), ly=S.fnum(at[2]),
                               rel_angle=ang - fa, layer=str(S.value(node, "layer", "")),
                               size=size, hidden=_is_hidden(node)))

    for pr in S.find_all(fpn, "property"):
        if len(pr) > 2 and str(pr[1]) in ("Reference", "Value"):
            add_text(str(pr[1]).lower(), pr[2], pr)
    for tx in S.find_all(fpn, "fp_text"):
        if len(tx) > 2 and tx[1] in ("reference", "value"):
            add_text(str(tx[1]), tx[2], tx)


def _board_outline(tree: list) -> List[Tuple[float, float]]:
    edges: List[List[Tuple[float, float]]] = []
    loops: List[List[Tuple[float, float]]] = []
    for g in tree[1:]:
        if not isinstance(g, list) or g[0] not in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly"):
            continue
        if str(S.value(g, "layer", "")) != "Edge.Cuts":
            continue
        t = g[0]
        if t == "gr_line":
            edges.append([_xy(S.find(g, "start")), _xy(S.find(g, "end"))])
        elif t == "gr_arc":
            if S.find(g, "mid"):
                edges.append(arc_points(_xy(S.find(g, "start")), _xy(S.find(g, "mid")), _xy(S.find(g, "end"))))
            else:  # formato antiguo: start=centro, end=inicio, angle
                c, s = _xy(S.find(g, "start")), _xy(S.find(g, "end"))
                ang = math.radians(S.fnum(S.value(g, "angle"), 90))
                r = math.hypot(s[0] - c[0], s[1] - c[1])
                a0 = math.atan2(s[1] - c[1], s[0] - c[0])
                n = 16
                edges.append([(c[0] + r * math.cos(a0 + ang * k / n), c[1] + r * math.sin(a0 + ang * k / n))
                              for k in range(n + 1)])
        elif t == "gr_rect":
            s, e = _xy(S.find(g, "start")), _xy(S.find(g, "end"))
            loops.append([s, (e[0], s[1]), e, (s[0], e[1])])
        elif t == "gr_circle":
            c, e = _xy(S.find(g, "center")), _xy(S.find(g, "end"))
            r = math.hypot(e[0] - c[0], e[1] - c[1])
            loops.append([(c[0] + r * math.cos(2 * math.pi * k / 64), c[1] + r * math.sin(2 * math.pi * k / 64))
                          for k in range(64)])
        elif t == "gr_poly":
            ptsn = S.find(g, "pts")
            if ptsn:
                loops.append([_xy(xy) for xy in S.find_all(ptsn, "xy")])

    # encadenar segmentos sueltos
    tol = 1e-3
    remaining = edges[:]
    while remaining:
        chain = list(remaining.pop(0))
        changed = True
        while changed and remaining:
            changed = False
            for i, e in enumerate(remaining):
                if math.dist(chain[-1], e[0]) < tol:
                    chain += e[1:]
                elif math.dist(chain[-1], e[-1]) < tol:
                    chain += list(reversed(e))[1:]
                else:
                    continue
                remaining.pop(i)
                changed = True
                break
        if len(chain) > 3 and math.dist(chain[0], chain[-1]) < tol:
            chain = chain[:-1]
        loops.append(chain)

    def area(p):
        return abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1]
                       for i in range(len(p)))) / 2
    loops = [l for l in loops if len(l) >= 3]
    if not loops:
        return []
    return max(loops, key=area)
