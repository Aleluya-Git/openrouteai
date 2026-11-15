"""Iconos de color dibujados con QPainter (sin archivos de imagen)."""
from __future__ import annotations

import math
import os
from typing import Dict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

COLORS = {
    "open": "#f2b632", "save": "#8e5bd6", "route": "#2fa84f", "partial": "#f08a24",
    "drc": "#2f7fd6", "clear": "#d64545", "move": "#7a5af0", "rotate": "#13a3a3",
    "track": "#e0457b", "undo": "#6b7280", "select": "#1f9fd6", "flip": "#c2410c", "fill": "#b8860b",
    "theme": "#374151", "prefs": "#4b5563",
}
_cache: Dict[str, QIcon] = {}
S = 64


def _circle(qp: QPainter, color: str):
    qp.setPen(Qt.NoPen)
    qp.setBrush(QBrush(QColor(color)))
    qp.drawEllipse(QRectF(3, 3, S - 6, S - 6))


def _white(width: float = 6.0) -> QPen:
    return QPen(QColor("white"), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def _arrow_head(qp: QPainter, tip: QPointF, angle: float, size: float = 11.0):
    a1, a2 = angle + math.radians(150), angle - math.radians(150)
    pts = [tip, QPointF(tip.x() + size * math.cos(a1), tip.y() + size * math.sin(a1)),
           QPointF(tip.x() + size * math.cos(a2), tip.y() + size * math.sin(a2))]
    qp.setPen(Qt.NoPen)
    qp.setBrush(QBrush(QColor("white")))
    qp.drawPolygon(QPolygonF(pts))


def _draw(name: str, qp: QPainter):
    c = COLORS[name]
    if name == "open":
        qp.setPen(QPen(QColor("#b07d10"), 2))
        qp.setBrush(QBrush(QColor(c)))
        path = QPainterPath()
        path.moveTo(6, 16); path.lineTo(24, 16); path.lineTo(29, 22); path.lineTo(58, 22)
        path.lineTo(58, 52); path.lineTo(6, 52); path.closeSubpath()
        qp.drawPath(path)
        qp.setBrush(QBrush(QColor("#ffd66b")))
        qp.drawPolygon(QPolygonF([QPointF(12, 30), QPointF(62, 30), QPointF(56, 52), QPointF(6, 52)]))
    elif name == "save":
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(QColor(c)))
        qp.drawRoundedRect(QRectF(8, 8, 48, 48), 6, 6)
        qp.setBrush(QBrush(QColor("white")))
        qp.drawRect(QRectF(18, 8, 28, 16))
        qp.setBrush(QBrush(QColor(c)))
        qp.drawRect(QRectF(36, 11, 6, 10))
        qp.setBrush(QBrush(QColor("#e8e0f8")))
        qp.drawRoundedRect(QRectF(15, 32, 34, 20), 3, 3)
    elif name == "route":
        _circle(qp, c)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(QColor("white")))
        qp.drawPolygon(QPolygonF([QPointF(25, 17), QPointF(48, 32), QPointF(25, 47)]))
    elif name == "partial":
        _circle(qp, c)
        qp.setPen(_white(6))
        qp.setBrush(Qt.NoBrush)
        qp.drawArc(QRectF(17, 17, 30, 30), 40 * 16, 270 * 16)
        _arrow_head(qp, QPointF(47, 26), math.radians(50))      # flecha en sentido horario
    elif name == "drc":
        _circle(qp, c)
        qp.setPen(_white(7))
        qp.drawPolyline(QPolygonF([QPointF(18, 33), QPointF(28, 43), QPointF(46, 22)]))
    elif name == "clear":
        _circle(qp, c)
        qp.setPen(_white(7))
        qp.drawLine(QPointF(22, 22), QPointF(42, 42))
        qp.drawLine(QPointF(42, 22), QPointF(22, 42))
    elif name == "move":
        _circle(qp, c)
        qp.setPen(_white(5))
        qp.drawLine(QPointF(32, 15), QPointF(32, 49))
        qp.drawLine(QPointF(15, 32), QPointF(49, 32))
        for tip, ang in ((QPointF(32, 11), -90), (QPointF(32, 53), 90), (QPointF(11, 32), 180), (QPointF(53, 32), 0)):
            _arrow_head(qp, tip, math.radians(ang), 10)
    elif name == "track":
        _circle(qp, c)
        qp.setPen(_white(5))
        qp.setBrush(Qt.NoBrush)
        qp.drawPolyline(QPolygonF([QPointF(15, 44), QPointF(27, 44), QPointF(39, 24), QPointF(50, 24)]))
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(QColor("white")))
        qp.drawEllipse(QPointF(33, 34), 6, 6)
        qp.setBrush(QBrush(QColor(c)))
        qp.drawEllipse(QPointF(33, 34), 2.5, 2.5)
    elif name == "select":
        _circle(qp, c)
        qp.setPen(QPen(QColor("#0b4f6c"), 2))
        qp.setBrush(QBrush(QColor("white")))
        qp.drawPolygon(QPolygonF([QPointF(22, 14), QPointF(22, 46), QPointF(30, 39), QPointF(36, 51),
                                  QPointF(41, 48), QPointF(35, 37), QPointF(46, 36)]))
    elif name == "undo":
        _circle(qp, c)
        qp.setPen(_white(6))
        qp.setBrush(Qt.NoBrush)
        qp.drawArc(QRectF(19, 21, 28, 26), 90 * 16, -230 * 16)
        _arrow_head(qp, QPointF(18, 30), math.radians(180), 11)
    elif name == "rotate":
        _circle(qp, c)
        qp.setPen(_white(6))
        qp.setBrush(Qt.NoBrush)
        qp.drawArc(QRectF(17, 17, 30, 30), 90 * 16, 270 * 16)
        _arrow_head(qp, QPointF(38, 17), math.radians(0))
    elif name == "flip":
        _circle(qp, c)
        qp.setPen(QPen(QColor("white"), 3, Qt.DashLine))
        qp.drawLine(QPointF(32, 12), QPointF(32, 52))
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(QColor("white")))
        qp.drawPolygon(QPolygonF([QPointF(27, 20), QPointF(27, 44), QPointF(13, 44)]))
        qp.setBrush(QBrush(QColor(255, 255, 255, 120)))
        qp.drawPolygon(QPolygonF([QPointF(37, 20), QPointF(37, 44), QPointF(51, 44)]))
    elif name == "fill":
        _circle(qp, c)
        qp.setPen(_white(3))
        qp.setBrush(QBrush(QColor(255, 255, 255, 200)))
        path = QPainterPath()
        path.addRect(QRectF(16, 18, 32, 28))
        path.addEllipse(QPointF(26, 32), 4.5, 4.5)
        path.addEllipse(QPointF(39, 28), 3.5, 3.5)
        path.setFillRule(Qt.OddEvenFill)
        qp.drawPath(path)
    elif name == "prefs":
        _circle(qp, c)
        qp.setPen(_white(5))
        qp.setBrush(Qt.NoBrush)
        qp.drawEllipse(QPointF(32, 32), 11, 11)
        for k in range(8):
            a = math.radians(k * 45)
            qp.drawLine(QPointF(32 + 15 * math.cos(a), 32 + 15 * math.sin(a)),
                        QPointF(32 + 21 * math.cos(a), 32 + 21 * math.sin(a)))
    elif name == "theme":
        _circle(qp, c)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(QColor("#fde68a")))
        qp.drawEllipse(QPointF(32, 32), 15, 15)
        qp.setBrush(QBrush(QColor(c)))
        qp.drawEllipse(QPointF(40, 26), 13, 13)


_ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icons")


def icon(name: str) -> QIcon:
    """Icono de la barra de herramientas: imagen de resources/icons si existe, si no se dibuja."""
    if name not in _cache:
        png = os.path.join(_ICON_DIR, name + ".png")
        if os.path.exists(png):
            base = QPixmap(png)
            if not base.isNull():
                ic = QIcon()
                for s in (16, 24, 32, 48, 64, 128):
                    ic.addPixmap(base.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                _cache[name] = ic
                return ic
        pm = QPixmap(S, S)
        pm.fill(Qt.transparent)
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing)
        _draw(name, qp)
        qp.end()
        _cache[name] = QIcon(pm)
    return _cache[name]


def save_preview(path: str):
    """Guarda todos los iconos en una tira PNG (para revisar el diseño)."""
    names = list(COLORS)
    pm = QPixmap(S * len(names) + 8 * (len(names) + 1), S + 16)
    pm.fill(QColor("#f4f4f4"))
    qp = QPainter(pm)
    for i, n in enumerate(names):
        qp.drawPixmap(8 + i * (S + 8), 8, icon(n).pixmap(S, S))
    qp.end()
    pm.save(path)
