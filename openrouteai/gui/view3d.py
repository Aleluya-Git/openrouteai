"""Vista 3D ligera (proyección ortográfica con QPainter, sin OpenGL).

Muestra el sustrato, las capas de cobre apiladas con separación exagerada,
pistas, pads, vías, el dibujo de las huellas, cuerpos de componentes como
cajas y los textos de referencia/valor. Cada elemento se puede ocultar.
Arrastrar con el ratón rota la vista; la rueda acerca/aleja.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..core.model import Board, Footprint
from ..i18n import tr
from .board_view import TEXT_COLORS, graphic_style, layer_color

# qué se muestra por defecto
SHOW_DEFAULTS = {"ref": True, "value": False, "fp": True, "bodies": True}


def body_height(fp: Footprint) -> float:
    """Altura aproximada del cuerpo (mm) para la vista 3D."""
    x0, y0, x1, y1 = fp.bbox()
    small = min(x1 - x0, y1 - y0)
    if any(p.kind == "thru_hole" for p in fp.pads):
        return min(8.0, max(2.5, 0.35 * small + 1.5))
    return min(3.0, max(0.5, 0.2 * small + 0.3))


class Board3DView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.board: Optional[Board] = None
        self.yaw = -30.0
        self.pitch = 55.0
        self.zoom = 1.0
        self.layer_gap = 2.0     # mm de separación visual entre capas
        self.layer_visible: Dict[str, bool] = {}
        self.show: Dict[str, bool] = dict(SHOW_DEFAULTS)
        self._drag = None
        self.setMinimumSize(300, 200)
        self.setMouseTracking(False)

    # ------------------------------------------------------------ estado
    def set_board(self, b: Board):
        self.board = b
        self.layer_visible = {name: True for name in b.copper_layers}
        self.update()

    def set_layer_visible(self, layer: str, vis: bool):
        self.layer_visible[layer] = vis
        self.update()

    def set_show(self, what: str, vis: bool):
        self.show[what] = vis
        self.update()

    def _lv(self, layer: str) -> bool:
        return self.layer_visible.get(layer, True)

    # ------------------------------------------------------------ proyección
    def _proj(self, x, y, z, cx, cy, s):
        b = self.board
        x0, y0, x1, y1 = b.bbox()
        x -= (x0 + x1) / 2
        y -= (y0 + y1) / 2
        a = math.radians(self.yaw)
        xr = x * math.cos(a) - y * math.sin(a)
        yr = x * math.sin(a) + y * math.cos(a)
        p = math.radians(self.pitch)
        sy = yr * math.cos(p) - z * math.sin(p)
        return QPointF(cx + xr * s, cy + sy * s), yr * math.sin(p) + z * math.cos(p)

    # ------------------------------------------------------------ dibujo
    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), QColor(16, 20, 24))
        b = self.board
        if b is None:
            qp.setPen(QColor(200, 200, 200))
            qp.drawText(self.rect(), Qt.AlignCenter, tr("Abre un proyecto para ver la placa en 3D"))
            return
        x0, y0, x1, y1 = b.bbox()
        s = 0.8 * min(self.width(), self.height()) / max(x1 - x0, y1 - y0, 1) * self.zoom
        cx, cy = self.width() / 2, self.height() / 2
        L = len(b.copper_layers)
        zl = {name: (L - 1 - i) * self.layer_gap for i, name in enumerate(b.copper_layers)}
        z_top = zl[b.copper_layers[0]]
        z_bot = zl[b.copper_layers[-1]]
        outline = b.outline or [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

        def P(x, y, z):
            return self._proj(x, y, z, cx, cy, s)[0]

        # 1) componentes de la cara inferior (cuelgan hacia abajo): se dibujan primero
        self._draw_side(qp, b, P, s, back=True, z=z_bot - 0.05)

        # 2) capas de cobre de abajo arriba
        for name in reversed(b.copper_layers):
            z = zl[name]
            poly = QPolygonF([P(x, y, z) for x, y in outline])
            qp.setPen(QPen(QColor(200, 200, 0, 120), 1))
            qp.setBrush(QBrush(QColor(30, 80, 50, 70)))
            qp.drawPolygon(poly)
            if not self._lv(name):
                continue
            col = layer_color(name)
            # planos / zonas rellenas de esta capa
            for zn in b.zones:
                if zn.layers and zn.layers[0] == name and zn.filled and not (zn.keepout_tracks or zn.keepout_vias):
                    path = QPainterPath()
                    path.setFillRule(Qt.OddEvenFill)
                    for outer, holes in zn.filled:
                        for ring in [outer] + list(holes):
                            path.addPolygon(QPolygonF([P(x, y, z) for x, y in ring]))
                            path.closeSubpath()
                    zc = QColor(col)
                    zc.setAlpha(110)
                    qp.setPen(Qt.NoPen)
                    qp.setBrush(QBrush(zc))
                    qp.drawPath(path)
            for t in b.tracks:
                if t.layer == name:
                    qp.setPen(QPen(col, max(1.0, t.width * s), Qt.SolidLine, Qt.RoundCap))
                    qp.drawLine(P(t.start[0], t.start[1], z), P(t.end[0], t.end[1], z))
            for p in b.pads:
                if name in p.layers or "*.Cu" in p.layers:
                    poly, r = p.copper_shape()
                    qp.setPen(Qt.NoPen)
                    qp.setBrush(QBrush(QColor(190, 160, 90, 220)))
                    if len(poly) >= 3:
                        qp.drawPolygon(QPolygonF([P(x, y, z) for x, y in poly]))
                    else:
                        c = P(p.x, p.y, z)
                        rr = max(1.5, (r if len(poly) == 1 else max(p.w, p.h) / 2) * s)
                        qp.drawEllipse(c, rr, rr * math.cos(math.radians(self.pitch)) + 1)

        # 3) vías (solo si alguna de las capas que unen está visible)
        qp.setPen(QPen(QColor(230, 230, 230), 2))
        cu = b.copper_layers
        for v in b.vias:
            i0 = cu.index(v.layers[0]) if v.layers[0] in cu else 0
            i1 = cu.index(v.layers[1]) if v.layers[1] in cu else L - 1
            if not any(self._lv(n) for n in cu[min(i0, i1):max(i0, i1) + 1]):
                continue
            qp.drawLine(P(v.x, v.y, zl.get(v.layers[0], z_top)), P(v.x, v.y, zl.get(v.layers[1], z_bot)))

        # 4) componentes de la cara superior
        self._draw_side(qp, b, P, s, back=False, z=z_top + 0.05)

        qp.setPen(QColor(200, 200, 200))
        qp.drawText(10, 20, tr("Arrastra para rotar · rueda para zoom"))

    def _draw_side(self, qp, b: Board, P, s: float, back: bool, z: float):
        fps = [fp for fp in b.footprints if fp.is_back == back]
        if not fps:
            return
        sign = -1.0 if back else 1.0
        # dibujo de las huellas sobre la superficie
        if self.show.get("fp", True):
            for fp in fps:
                for layer, pts in fp.abs_graphics():
                    if len(pts) < 2:
                        continue
                    col, w = graphic_style(layer)
                    qp.setPen(QPen(col, max(1.0, w * s), Qt.SolidLine, Qt.RoundCap))
                    qp.drawPolyline(QPolygonF([P(x, y, z) for x, y in pts]))
        # cuerpos: de los más lejanos a los más cercanos
        tops = {}
        if self.show.get("bodies", True):
            order = []
            for fp in fps:
                x0, y0, x1, y1 = fp.bbox()
                m = min(0.15, 0.1 * min(x1 - x0, y1 - y0))
                x0, y0, x1, y1 = x0 + m, y0 + m, x1 - m, y1 - m
                h = body_height(fp)
                depth = self._proj((x0 + x1) / 2, (y0 + y1) / 2, z, 0, 0, 1)[1]
                order.append((depth, fp, (x0, y0, x1, y1), h))
            order.sort(key=lambda t: t[0] * (-1 if back else 1))
            for depth, fp, (x0, y0, x1, y1), h in order:
                zt = z + sign * h
                tops[id(fp)] = zt
                th = any(p.kind == "thru_hole" for p in fp.pads)
                base = QColor(45, 45, 52, 215) if not th else QColor(30, 30, 34, 225)
                edge = QPen(QColor(110, 110, 125, 230), 1)
                corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                qp.setPen(edge)
                qp.setBrush(QBrush(base.darker(130)))
                for i in range(4):
                    a, c = corners[i], corners[(i + 1) % 4]
                    qp.drawPolygon(QPolygonF([P(a[0], a[1], z), P(c[0], c[1], z), P(c[0], c[1], zt),
                                              P(a[0], a[1], zt)]))
                qp.setBrush(QBrush(base))
                qp.drawPolygon(QPolygonF([P(x, y, zt) for x, y in corners]))
        # textos (alineados con la pantalla, encima de todo)
        for what, kind in (("value", "value"), ("ref", "reference")):
            if not self.show.get(what, False):
                continue
            for fp in fps:
                zt = tops.get(id(fp), z) + sign * 0.02
                for t, x, y, ang in fp.abs_texts():
                    if t.kind != kind or not t.text:
                        continue
                    if id(fp) in tops:      # sobre la tapa del cuerpo, en su centro
                        bx0, by0, bx1, by1 = fp.bbox()
                        x, y = (bx0 + bx1) / 2, (by0 + by1) / 2
                        if kind == "value":
                            y += 0.9 * t.size
                    px = int(max(8, min(40, t.size * s * 1.3)))
                    f = QFont("Arial")
                    f.setPixelSize(px)
                    f.setBold(kind == "reference")
                    qp.setFont(f)
                    col = QColor(TEXT_COLORS[kind])
                    if back:
                        col.setAlpha(170)
                    qp.setPen(col)
                    c = P(x, y, zt)
                    w = qp.fontMetrics().horizontalAdvance(t.text)
                    qp.drawText(QPointF(c.x() - w / 2, c.y() + px / 3), t.text)

    # ------------------------------------------------------------ interacción
    def mousePressEvent(self, ev):
        self._drag = (ev.position().x(), ev.position().y(), self.yaw, self.pitch)

    def mouseMoveEvent(self, ev):
        if self._drag:
            x, y, yw, pt = self._drag
            self.yaw = yw + (ev.position().x() - x) * 0.4
            self.pitch = max(0.0, min(89.0, pt + (ev.position().y() - y) * 0.3))
            self.update()

    def mouseReleaseEvent(self, ev):
        self._drag = None

    def wheelEvent(self, ev):
        self.zoom *= 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.update()
