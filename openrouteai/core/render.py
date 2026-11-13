"""Renderizado de la placa a PNG con matplotlib (autoprueba, CLI e informes)."""
from __future__ import annotations

import math
from typing import Optional

from .model import Board

LAYER_COLORS = {"F.Cu": "#f04b4b", "B.Cu": "#5096ff", "In1.Cu": "#f0c328", "In2.Cu": "#50cd6e",
                "In3.Cu": "#c86ef5", "In4.Cu": "#28d2d2"}


def render_png(board: Board, path: str, title: str = "", congestion=None, grid_info: Optional[dict] = None,
               dpi: int = 110) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Polygon
    except ImportError:
        return False
    x0, y0, x1, y1 = board.bbox()
    w, h = x1 - x0, y1 - y0
    scale = 10 / max(w, h)
    fig, ax = plt.subplots(figsize=(max(4, w * scale) + 0.5, max(3, h * scale) + 0.8))
    ax.set_facecolor("#101418")
    if congestion is not None and grid_info:
        import numpy as np
        c = congestion.sum(axis=0) if congestion.ndim == 3 else congestion
        if c.max() > 0:
            ext = (grid_info["x0"], grid_info["x0"] + grid_info["nx"] * grid_info["pitch"],
                   grid_info["y0"] + grid_info["ny"] * grid_info["pitch"], grid_info["y0"])
            ax.imshow(np.ma.masked_equal(c, 0), extent=ext, cmap="inferno", alpha=0.6, zorder=0)
    if board.outline:
        ax.add_patch(Polygon(board.outline, closed=True, fill=False, ec="#e0e000", lw=1.0, zorder=1))
    for z in board.zones:
        if len(z.polygon) >= 3 and not (z.keepout_tracks or z.keepout_vias):
            col = LAYER_COLORS.get(z.layers[0] if z.layers else "B.Cu", "#888888")
            ax.add_patch(Polygon(z.polygon, closed=True, color=col, alpha=0.18, zorder=1))
    order = list(reversed(board.copper_layers))
    for li, layer in enumerate(order):
        col = LAYER_COLORS.get(layer, "#888888")
        for t in board.tracks:
            if t.layer == layer:
                ax.plot([t.start[0], t.end[0]], [t.start[1], t.end[1]], color=col, lw=max(0.6, t.width * 72 * scale / 2.6),
                        solid_capstyle="round", alpha=0.85, zorder=2 + li)
    for p in board.pads:
        poly, r = p.copper_shape()
        col = "#c0a060" if p.kind == "thru_hole" else LAYER_COLORS.get(p.layers[0] if p.layers else "F.Cu", "#999")
        if len(poly) == 1:
            ax.add_patch(Circle(poly[0], r, color=col, zorder=10, alpha=0.9))
        elif len(poly) == 2:
            ax.plot([poly[0][0], poly[1][0]], [poly[0][1], poly[1][1]], color=col, lw=2 * r * 72 * scale / 2.6,
                    solid_capstyle="round", zorder=10)
        else:
            ax.add_patch(Polygon(poly, closed=True, color=col, zorder=10, alpha=0.9))
        if p.drill:
            ax.add_patch(Circle(p.pos, p.drill / 2, color="#101418", zorder=11))
    for v in board.vias:
        ax.add_patch(Circle(v.pos, v.diameter / 2, color="#e0e0e0", zorder=12))
        ax.add_patch(Circle(v.pos, v.drill / 2, color="#101418", zorder=13))
    # ratsnest de lo que queda sin conectar
    try:
        from ..drc.drc import unconnected
        pending = {n for n, _, _ in unconnected(board)}
        from ..analysis.netlist_analyzer import mst_connections
        for name in pending:
            for c in mst_connections(board.nets[name]):
                ax.plot([c.a.x, c.b.x], [c.a.y, c.b.y], color="#ffffff", lw=0.5, ls="--", zorder=20)
    except Exception:
        pass
    ax.set_xlim(x0 - 1, x1 + 1)
    ax.set_ylim(y1 + 1, y0 - 1)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=9)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return True
