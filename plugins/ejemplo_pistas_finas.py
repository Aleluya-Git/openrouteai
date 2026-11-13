"""Plugin de ejemplo para OpenRouteAI.

Añade una comprobación DRC que avisa de pistas más finas que 0,15 mm (límite
habitual de fabricantes económicos) y de vías con taladro menor de 0,3 mm.
"""
from openrouteai.drc.drc import Violation

MIN_TRACK = 0.15
MIN_DRILL = 0.3


def check_fabricacion(board):
    out = []
    for t in board.tracks:
        if t.width < MIN_TRACK - 1e-9:
            out.append(Violation("fabricacion", f"Pista de {t.width} mm (< {MIN_TRACK}) en {t.net}",
                                 t.start[0], t.start[1], t.layer, (t.net,), severity="warning"))
    for v in board.vias:
        if v.drill < MIN_DRILL - 1e-9:
            out.append(Violation("fabricacion", f"Vía con taladro {v.drill} mm (< {MIN_DRILL})",
                                 v.x, v.y, "", (v.net,), severity="warning"))
    return out


def register(api):
    api.add_drc_check("fabricacion_economica", check_fabricacion)
