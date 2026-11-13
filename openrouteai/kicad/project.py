"""Lectura de reglas y clases de red desde el .kicad_pro (JSON)."""
from __future__ import annotations

import fnmatch
import json
import re

from ..core.model import Board, NetClass


def _f(d, k, default):
    v = d.get(k, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _match(pattern: str, name: str) -> bool:
    """KiCad usa comodines tipo glob; también se acepta regex."""
    if fnmatch.fnmatchcase(name, pattern):
        return True
    try:
        return re.fullmatch(pattern, name) is not None
    except re.error:
        return False


def apply_project(board: Board, path: str) -> None:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    ds = data.get("board", {}).get("design_settings", {})
    rules = ds.get("rules", {})
    r = board.rules
    r.min_clearance = _f(rules, "min_clearance", r.min_clearance)
    r.min_track_width = _f(rules, "min_track_width", r.min_track_width)
    r.min_via_diameter = _f(rules, "min_via_diameter", r.min_via_diameter)
    r.min_via_drill = _f(rules, "min_through_hole_diameter", r.min_via_drill)
    r.copper_edge_clearance = _f(rules, "min_copper_edge_clearance", r.copper_edge_clearance)
    r.hole_clearance = _f(rules, "min_hole_clearance", r.hole_clearance)
    r.allow_blind_buried = bool(rules.get("allow_blind_buried_vias", False))
    r.allow_microvias = bool(rules.get("allow_microvias", False))

    # listas predefinidas de anchos de pista y vías (la entrada 0 de KiCad = "usar la clase")
    board.track_widths = [float(w) for w in ds.get("track_widths", []) if float(w) > 0]
    board.via_sizes = [(float(v.get("diameter", 0)), float(v.get("drill", 0)))
                       for v in ds.get("via_dimensions", []) if float(v.get("diameter", 0)) > 0]

    ns = data.get("net_settings", {})
    for c in ns.get("classes", []):
        name = c.get("name", "Default")
        board.netclasses[name] = NetClass(
            name=name,
            clearance=_f(c, "clearance", 0.2),
            track_width=_f(c, "track_width", 0.25),
            via_diameter=_f(c, "via_diameter", 0.6),
            via_drill=_f(c, "via_drill", 0.3),
            diff_pair_width=_f(c, "diff_pair_width", 0.2),
            diff_pair_gap=_f(c, "diff_pair_gap", 0.25),
            priority=int(_f(c, "priority", 0)),
        )

    # Asignaciones explícitas (KiCad 6) y por patrón (KiCad 7+)
    assignments = ns.get("netclass_assignments") or {}
    if isinstance(assignments, dict):
        for net, cls in assignments.items():
            if net in board.nets and cls in board.netclasses:
                board.nets[net].netclass = cls if isinstance(cls, str) else cls[0]
    for pat in ns.get("netclass_patterns", []) or []:
        cls = pat.get("netclass")
        p = pat.get("pattern", "")
        if cls not in board.netclasses:
            continue
        for net in board.nets.values():
            if net.netclass == "Default" and _match(p, net.name):
                net.netclass = cls


def write_size_lists(path: str, widths, vias) -> bool:
    """Guarda en un .kicad_pro las listas de anchos de pista y de vías (formato de KiCad)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    ds = data.setdefault("board", {}).setdefault("design_settings", {})
    ds["track_widths"] = [0.0] + [round(float(w), 4) for w in widths]
    ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0}] + \
        [{"diameter": round(float(d), 4), "drill": round(float(r), 4)} for d, r in vias]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return True
