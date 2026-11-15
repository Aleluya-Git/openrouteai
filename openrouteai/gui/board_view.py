"""Vista 2D de la placa (QGraphicsView, 1 unidad de escena = 1 mm)."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView)

from .. import editing as E
from ..i18n import tr
from ..core.model import Board

LAYER_COLORS = {"F.Cu": QColor(240, 75, 75, 235), "B.Cu": QColor(80, 150, 255, 235),
                "In1.Cu": QColor(240, 195, 40, 230), "In2.Cu": QColor(80, 205, 110, 230),
                "In3.Cu": QColor(200, 110, 245, 230), "In4.Cu": QColor(40, 210, 210, 230)}
HIGHLIGHT = QColor(255, 255, 255, 230)


def layer_color(name: str) -> QColor:
    return LAYER_COLORS.get(name, QColor(150, 150, 150, 190))


# categorías de elementos que se pueden mostrar/ocultar (además de las capas)
CATEGORIES = {"ref": "Referencias", "value": "Valores", "padnum": "Números de pad", "fp": "Huellas"}
CAT_DEFAULTS = {"ref": True, "value": False, "padnum": False, "fp": True}
TEXT_COLORS = {"reference": QColor(235, 235, 235), "value": QColor(140, 190, 255)}


def graphic_style(layer: str):
    """(color, grosor mm) de un trazo de huella según su capa."""
    back = layer.startswith("B.")
    if "Silk" in layer:
        col, w = QColor(235, 235, 235, 120 if back else 220), 0.12
    elif "Fab" in layer:
        col, w = QColor(150, 150, 170, 90 if back else 170), 0.08
    else:  # courtyard
        col, w = QColor(210, 90, 210, 70 if back else 130), 0.0
    return col, w


def upright(angle: float) -> float:
    """Ángulo de lectura (como KiCad, el texto nunca queda boca abajo)."""
    a = angle % 360.0
    if 90.0 < a <= 270.0:
        a -= 180.0
    return a


def make_text(text: str, x: float, y: float, angle: float, size_mm: float, color: QColor,
              bold: bool = False) -> QGraphicsSimpleTextItem:
    """Texto centrado en (x, y) mm, de altura de mayúsculas ``size_mm`` y girado ``angle`` (antihorario)."""
    it = QGraphicsSimpleTextItem(text)
    f = QFont("Arial")
    f.setPointSizeF(10.0)
    f.setBold(bold)
    it.setFont(f)
    it.setBrush(QBrush(color))
    br = it.boundingRect()
    k = size_mm * 1.45 / max(br.height(), 1e-6)
    c = br.center()
    it.setTransformOriginPoint(c)
    it.setScale(k)
    it.setRotation(-upright(angle))
    it.setPos(x - c.x(), y - c.y())
    return it


class BoardView(QGraphicsView):
    netClicked = Signal(str)
    mouseMoved = Signal(float, float)
    footprintMoved = Signal(object)      # lista de huellas movidas o giradas
    tracksEdited = Signal(str)           # pistas/vías arrastradas o borradas (mensaje)
    beforeEdit = Signal()                # justo antes de modificar el modelo (para deshacer)
    info = Signal(str)
    selectionChanged = Signal(object, int)   # (redes seleccionadas, nº de vías seleccionadas)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(16, 20, 24)))
        self.setMouseTracking(True)
        self.board: Optional[Board] = None
        self.layer_items: Dict[str, List[QGraphicsItem]] = {}
        self.net_items: Dict[str, List[QGraphicsItem]] = {}
        self.ratsnest: List[QGraphicsItem] = []
        self.congestion_item: Optional[QGraphicsPixmapItem] = None
        self.layer_visible: Dict[str, bool] = {}
        self.cat_items: Dict[str, List[QGraphicsItem]] = {k: [] for k in CATEGORIES}
        self.cat_visible: Dict[str, bool] = dict(CAT_DEFAULTS)
        self.highlighted: Optional[str] = None
        self._fitted = False
        self.fp_items: Dict[int, List[QGraphicsItem]] = {}
        self.track_item: Dict[int, QGraphicsItem] = {}
        self.via_item: Dict[int, List[QGraphicsItem]] = {}
        self.rats_meta: List[tuple] = []          # (item, pad_a, pad_b)
        self.mode: Optional[str] = None           # None | "fp" | "track"
        self.snap = 0.5
        self.sel_fps: List = []
        self.sel_tracks: List = []
        self.sel_vias: List = []
        self._sel_items: List[QGraphicsItem] = []
        self._drag = None
        self._hover_fp = None
        self.highlighted_nets: set = set()
        self._orig_style: Dict[int, tuple] = {}
        self.sel_nets: set = set()
        self._pan = None
        self._show_welcome()

    def _show_welcome(self):
        """Logo y aviso mientras no hay ninguna placa abierta."""
        from .branding import logo_pixmap
        sc = self.scene()
        pm = logo_pixmap(900)
        if pm.isNull():
            return
        it = sc.addPixmap(pm)
        it.setTransformationMode(Qt.SmoothTransformation)
        it.setOpacity(0.9)
        t = sc.addSimpleText(tr("Abre un proyecto KiCad (.kicad_pro o .kicad_pcb)"))
        f = QFont("Arial")
        f.setPointSizeF(16)
        t.setFont(f)
        t.setBrush(QBrush(QColor(170, 180, 190)))
        t.setPos((pm.width() - t.boundingRect().width()) / 2, pm.height() + 30)
        sc.setSceneRect(sc.itemsBoundingRect().adjusted(-80, -80, 80, 80))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.board is None and self.scene() is not None:
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    # ------------------------------------------------------------ dibujo
    def set_board(self, board: Board, refit: bool = True):
        self.board = board
        if refit:
            self._fitted = False
        self.rebuild()

    def rebuild(self, congestion=None, grid_info=None, show_ratsnest=True):
        sc = self.scene()
        sc.clear()
        sc.setSceneRect(QRectF())          # vuelve al tamaño automático (tras el logo de bienvenida)
        self.layer_items.clear()
        self.net_items.clear()
        self.cat_items = {k: [] for k in CATEGORIES}
        self.fp_items = {}
        self.track_item = {}
        self.via_item = {}
        self.rats_meta = []
        self._sel_items = []
        self._orig_style = {}
        self._drag = None
        self.ratsnest = []
        self.congestion_item = None
        b = self.board
        if b is None:
            return
        # contorno
        if b.outline:
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(x, y) for x, y in b.outline]))
            path.closeSubpath()
            it = sc.addPath(path, QPen(QColor(220, 220, 0), 0), QBrush(QColor(20, 40, 28)))
            it.setZValue(-10)
        # zonas / planos
        for z in b.zones:
            if len(z.polygon) < 3:
                continue
            zp = QPainterPath()
            zp.addPolygon(QPolygonF([QPointF(x, y) for x, y in z.polygon]))
            zp.closeSubpath()
            if z.keepout_tracks or z.keepout_vias:
                it = sc.addPath(zp, QPen(QColor(255, 80, 80), 0, Qt.DashLine), QBrush(Qt.NoBrush))
            else:
                base = QColor(layer_color(z.layers[0] if z.layers else "B.Cu"))
                edge = QColor(base)
                edge.setAlpha(220)
                # contorno de la zona (rayado si aún no tiene relleno)
                hatch = QBrush(QColor(base.red(), base.green(), base.blue(), 90),
                               Qt.BDiagPattern if not z.filled else Qt.NoBrush)
                it = sc.addPath(zp, QPen(edge, 0, Qt.DashLine), hatch)
                for l in z.layers:
                    self.layer_items.setdefault(l, []).append(it)
                if z.filled:
                    fp_ = QPainterPath()
                    fp_.setFillRule(Qt.OddEvenFill)
                    for outer, holes in z.filled:
                        for ring in [outer] + list(holes):
                            fp_.addPolygon(QPolygonF([QPointF(x, y) for x, y in ring]))
                            fp_.closeSubpath()
                    fill = QColor(base)
                    fill.setAlpha(70 if z.stale else 120)
                    fi = sc.addPath(fp_, QPen(Qt.NoPen) if z.stale else QPen(edge, 0), QBrush(fill))
                    fi.setZValue(-7)
                    fi.setAcceptedMouseButtons(Qt.NoButton)
                    for l in z.layers:
                        self.layer_items.setdefault(l, []).append(fi)
            it.setZValue(-8)
        # mapa de congestión
        if congestion is not None and grid_info is not None:
            self._add_congestion(congestion, grid_info)
        # pistas (capas inferiores primero)
        order = list(reversed(b.copper_layers))
        for zi, layer in enumerate(order):
            col = layer_color(layer)
            for t in b.tracks:
                if t.layer != layer:
                    continue
                pen = QPen(col, t.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                it = sc.addLine(t.start[0], t.start[1], t.end[0], t.end[1], pen)
                it.setZValue(zi)
                it.setData(0, t.net)
                self.layer_items.setdefault(layer, []).append(it)
                self.net_items.setdefault(t.net, []).append(it)
                self.track_item[id(t)] = it
        # pads
        for p in b.pads:
            poly, r = p.copper_shape()
            th = p.kind == "thru_hole"
            col = QColor(190, 160, 90, 230) if th else layer_color(next((l for l in b.copper_layers if l in p.layers), "F.Cu"))
            path = QPainterPath()
            if len(poly) == 1:
                path.addEllipse(QPointF(*poly[0]), r, r)
            elif len(poly) == 2:
                stroker = QPainterPath()
                stroker.moveTo(*poly[0])
                stroker.lineTo(*poly[1])
                from PySide6.QtGui import QPainterPathStroker
                s = QPainterPathStroker()
                s.setWidth(2 * r)
                s.setCapStyle(Qt.RoundCap)
                path = s.createStroke(stroker)
            else:
                path.addPolygon(QPolygonF([QPointF(x, y) for x, y in poly]))
                path.closeSubpath()
                if r > 0:
                    from PySide6.QtGui import QPainterPathStroker
                    s = QPainterPathStroker()
                    s.setWidth(2 * r)
                    s.setJoinStyle(Qt.RoundJoin)
                    path = path.united(s.createStroke(path))
            if p.drill > 0:
                hole = QPainterPath()
                hole.addEllipse(QPointF(p.x, p.y), p.drill / 2, p.drill / 2)
                path = path.subtracted(hole)
            it = sc.addPath(path, QPen(Qt.NoPen), QBrush(col))
            it.setZValue(20)
            it.setData(0, p.net)
            it.setToolTip(f"{p.ref}  {p.net}")
            lay = "F.Cu" if "F.Cu" in p.layers else next((l for l in b.copper_layers if l in p.layers), "*")
            if th or "*.Cu" in p.layers:
                lay = "*"
            self.layer_items.setdefault(lay, []).append(it)
            self.net_items.setdefault(p.net, []).append(it)
            self.fp_items.setdefault(id(p.footprint), []).append(it)
        self._add_footprint_items(sc, b)
        # vías
        for v in b.vias:
            it = sc.addEllipse(v.x - v.diameter / 2, v.y - v.diameter / 2, v.diameter, v.diameter,
                               QPen(Qt.NoPen), QBrush(QColor(225, 225, 225)))
            it.setZValue(30)
            it.setData(0, v.net)
            hole = sc.addEllipse(v.x - v.drill / 2, v.y - v.drill / 2, v.drill, v.drill,
                                 QPen(Qt.NoPen), QBrush(QColor(16, 20, 24)))
            hole.setZValue(31)
            self.net_items.setdefault(v.net, []).append(it)
            self.via_item[id(v)] = [it, hole]
        if show_ratsnest:
            self.draw_ratsnest()
        self.apply_visibility()
        alive_n = set(b.nets)
        self.sel_nets &= alive_n
        if self.sel_nets or self.highlighted_nets:
            self.highlight_nets(self.sel_nets or self.highlighted_nets)
        # la selección sobrevive a la reconstrucción si los objetos siguen existiendo
        alive_t = {id(t) for t in b.tracks}
        alive_v = {id(v) for v in b.vias}
        self.sel_tracks = [t for t in self.sel_tracks if id(t) in alive_t]
        self.sel_vias = [v for v in self.sel_vias if id(v) in alive_v]
        self.sel_fps = [f for f in self.sel_fps if f in b.footprints]
        self._draw_selection()
        if not self._fitted:
            x0, y0, x1, y1 = b.bbox()
            self.fitInView(QRectF(x0 - 2, y0 - 2, x1 - x0 + 4, y1 - y0 + 4), Qt.KeepAspectRatio)
            self._fitted = True

    def _add_footprint_items(self, sc, b: Board):
        """Dibujo de huellas, referencias, valores y números de pad."""
        for fp in b.footprints:
            for layer, pts in fp.abs_graphics():
                if len(pts) < 2:
                    continue
                col, w = graphic_style(layer)
                path = QPainterPath()
                path.moveTo(*pts[0])
                for q in pts[1:]:
                    path.lineTo(*q)
                it = sc.addPath(path, QPen(col, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin), QBrush(Qt.NoBrush))
                it.setZValue(25)
                self.cat_items["fp"].append(it)
                self.fp_items.setdefault(id(fp), []).append(it)
            for t, x, y, ang in fp.abs_texts():
                if not t.text:
                    continue
                cat = "ref" if t.kind == "reference" else "value"
                it = make_text(t.text, x, y, ang, t.size, TEXT_COLORS[t.kind], bold=cat == "ref")
                if fp.is_back:
                    it.setOpacity(0.7)
                it.setZValue(45 if cat == "ref" else 44)
                it.setToolTip(f"{fp.ref}  {fp.value}")
                sc.addItem(it)
                self.cat_items[cat].append(it)
                self.fp_items.setdefault(id(fp), []).append(it)
            for p in fp.pads:
                if not p.number:
                    continue
                size = max(0.25, min(1.0, 0.45 * min(p.w, p.h)))
                if len(p.number) > 2:
                    size *= 0.75
                ang = (p.angle + (90.0 if p.h > p.w * 1.2 else 0.0))
                it = make_text(p.number, p.x, p.y, ang, size, QColor(255, 255, 255, 230))
                it.setZValue(46)
                sc.addItem(it)
                self.cat_items["padnum"].append(it)
                self.fp_items.setdefault(id(fp), []).append(it)

    def draw_ratsnest(self):
        from ..analysis.netlist_analyzer import mst_connections
        from ..drc.drc import unconnected
        b = self.board
        pen = QPen(QColor(255, 255, 255, 160), 0, Qt.DashLine)
        pending = {n for n, _, _ in unconnected(b)}
        for name in pending:
            for c in mst_connections(b.nets[name]):
                it = self.scene().addLine(c.a.x, c.a.y, c.b.x, c.b.y, pen)
                it.setZValue(40)
                self.ratsnest.append(it)
                self.rats_meta.append((it, c.a, c.b))

    def _add_congestion(self, cong, gi):
        c = cong.sum(axis=0) if cong.ndim == 3 else cong
        if c.max() <= 0:
            return
        n = np.clip(c / c.max(), 0, 1)
        rgba = np.zeros(n.shape + (4,), dtype=np.uint8)
        rgba[..., 0] = (255 * np.clip(n * 2, 0, 1)).astype(np.uint8)
        rgba[..., 1] = (255 * np.clip(n * 2 - 1, 0, 1)).astype(np.uint8)
        rgba[..., 2] = (80 * (1 - n)).astype(np.uint8)
        rgba[..., 3] = np.where(c > 0, 150, 0).astype(np.uint8)
        rgba = np.ascontiguousarray(rgba)
        h, w = n.shape
        buf = rgba.tobytes()
        img = QImage(buf, w, h, 4 * w, QImage.Format_RGBA8888).copy()
        it = QGraphicsPixmapItem(QPixmap.fromImage(img))
        it.setScale(gi["pitch"])
        it.setPos(gi["x0"] - gi["pitch"] / 2, gi["y0"] - gi["pitch"] / 2)
        it.setZValue(-5)
        it.setTransformationMode(Qt.FastTransformation)
        self.scene().addItem(it)
        self.congestion_item = it

    # ------------------------------------------------------------ estado
    def set_layer_visible(self, layer: str, vis: bool):
        self.layer_visible[layer] = vis
        self.apply_visibility()

    def set_category_visible(self, cat: str, vis: bool):
        self.cat_visible[cat] = vis
        for it in self.cat_items.get(cat, []):
            it.setVisible(vis)

    def apply_visibility(self):
        for layer, items in self.layer_items.items():
            vis = self.layer_visible.get(layer, True)
            for it in items:
                it.setVisible(vis)
        for cat, items in self.cat_items.items():
            vis = self.cat_visible.get(cat, True)
            for it in items:
                it.setVisible(vis)

    def set_ratsnest_visible(self, vis: bool):
        for it in self.ratsnest:
            it.setVisible(vis)

    # ------------------------------------------------------------ resaltado
    def highlight_net(self, net: Optional[str]):
        self.highlight_nets({net} if net else set())

    def highlight_nets(self, nets):
        """Resalta pads, pistas y vías de las redes dadas (más brillantes y encima) y atenúa el resto."""
        nets = {n for n in (nets or ()) if n}
        for iid, (item, pen, brush, z) in list(self._orig_style.items()):
            try:
                if pen is not None:
                    item.setPen(pen)
                if brush is not None:
                    item.setBrush(brush)
                item.setZValue(z)
            except RuntimeError:
                pass
        self._orig_style = {}
        for name, items in self.net_items.items():
            on = name in nets
            for it in items:
                it.setOpacity(1.0 if not nets or on else 0.22)
                if on:
                    pen = it.pen() if hasattr(it, "pen") else None
                    brush = it.brush() if hasattr(it, "brush") else None
                    self._orig_style[id(it)] = (it, QPen(pen) if pen is not None else None,
                                                QBrush(brush) if brush is not None else None, it.zValue())
                    if pen is not None and pen.style() != Qt.NoPen:
                        np_ = QPen(pen)
                        np_.setColor(QColor(pen.color()).lighter(150))
                        c = np_.color()
                        c.setAlpha(255)
                        np_.setColor(c)
                        it.setPen(np_)
                    if brush is not None and brush.style() != Qt.NoBrush:
                        c = QColor(brush.color()).lighter(140)
                        c.setAlpha(255)
                        it.setBrush(QBrush(c))
                    it.setZValue(it.zValue() + 50)
        self.highlighted_nets = nets

    # ------------------------------------------------------------ interacción
    def wheelEvent(self, ev):
        f = 1.25 if ev.angleDelta().y() > 0 else 0.8
        self.scale(f, f)

    def set_mode(self, mode: Optional[str]):
        """None (navegar), "fp" (mover huellas) o "track" (editar pistas)."""
        self.mode = mode
        self._cancel_drag()
        self.sel_fps, self.sel_tracks, self.sel_vias = [], [], []
        self.setDragMode(QGraphicsView.NoDrag)        # se desplaza con el botón central o el derecho
        self.viewport().setCursor(Qt.ArrowCursor if mode is None else
                                  (Qt.SizeAllCursor if mode == "fp" else Qt.CrossCursor))
        self._draw_selection()
        self.selectionChanged.emit(sorted(self.sel_nets), 0)

    def set_move_mode(self, on: bool):          # compatibilidad
        self.set_mode("fp" if on else None)

    @property
    def move_mode(self) -> bool:
        return self.mode == "fp"

    def footprint_at(self, x: float, y: float):
        """Huella bajo el punto (la de courtyard más pequeño si hay varias)."""
        if self.board is None:
            return None
        best = None
        for fp in self.board.footprints:
            x0, y0, x1, y1 = fp.bbox()
            if x0 <= x <= x1 and y0 <= y <= y1:
                area = (x1 - x0) * (y1 - y0)
                if best is None or area < best[0]:
                    best = (area, fp)
        return best[1] if best else None

    def _snapped(self, v: float) -> float:
        return round(v / self.snap) * self.snap if self.snap > 0 else v

    def _px(self, n: float = 4.0) -> float:
        """n píxeles de pantalla en mm."""
        return n / max(self.transform().m11(), 1e-6)

    def visible_layers(self):
        return {l for l in (self.board.copper_layers if self.board else []) if self.layer_visible.get(l, True)}

    # ------------------------------------------------------------ selección
    def _draw_selection(self):
        sc = self.scene()
        for it in self._sel_items:
            try:
                sc.removeItem(it)
            except RuntimeError:
                pass
        self._sel_items = []
        cyan = QColor(0, 230, 255)
        for fp in self.sel_fps:
            x0, y0, x1, y1 = fp.bbox()
            it = sc.addRect(QRectF(x0 - 0.2, y0 - 0.2, x1 - x0 + 0.4, y1 - y0 + 0.4),
                            QPen(cyan, 0, Qt.DashLine), QBrush(QColor(0, 230, 255, 25)))
            it.setZValue(60)
            self._sel_items.append(it)
        for t in self.sel_tracks:
            it = sc.addLine(t.start[0], t.start[1], t.end[0], t.end[1],
                            QPen(QColor(255, 255, 255, 200), t.width + self._px(3), Qt.SolidLine, Qt.RoundCap))
            it.setZValue(55)
            self._sel_items.append(it)
            for q in (t.start, t.end):
                r = self._px(3)
                h = sc.addRect(QRectF(q[0] - r, q[1] - r, 2 * r, 2 * r), QPen(cyan, 0), QBrush(cyan))
                h.setZValue(61)
                self._sel_items.append(h)
        for v in self.sel_vias:
            r = v.diameter / 2 + self._px(3)
            it = sc.addEllipse(v.x - r, v.y - r, 2 * r, 2 * r, QPen(cyan, self._px(2)), QBrush(Qt.NoBrush))
            it.setZValue(61)
            self._sel_items.append(it)

    def clear_selection(self):
        self.sel_fps, self.sel_tracks, self.sel_vias = [], [], []
        self._draw_selection()

    def set_selected_nets(self, nets, emit: bool = False):
        """Redes seleccionadas (resaltadas en la vista). Desde la lista no se reemite."""
        self.sel_nets = {n for n in nets if n}
        self.highlight_nets(self.sel_nets)
        if emit:
            self.selectionChanged.emit(sorted(self.sel_nets), len(self.sel_vias))

    def _emit_selection(self):
        self.highlight_nets(self.sel_nets)
        self._draw_selection()
        self.selectionChanged.emit(sorted(self.sel_nets), len(self.sel_vias))

    def pad_at(self, x: float, y: float):
        vis = self.visible_layers()
        for p in self.board.pads:
            if not p.net:
                continue
            if not (p.kind == "thru_hole" or "*.Cu" in p.layers or any(l in vis for l in p.layers)):
                continue
            if E.point_in_pad((x, y), p, tol=self._px(1)):
                return p
        return None

    def _select_press(self, x, y, ctrl):
        """Modo seleccionar: vía → la vía; pista o pad → su red; vacío → cuadro."""
        v = E.via_at(self.board, x, y, tol=self._px(2))
        if v is not None:
            if ctrl:
                self._toggle(self.sel_vias, v)
            else:
                self.sel_vias = [v]
                self.sel_nets = set()
            self._emit_selection()
            return True
        t = E.track_at(self.board, x, y, self.visible_layers(), tol=self._px(3))
        net = t.net if t is not None else None
        if net is None:
            p = self.pad_at(x, y)
            net = p.net if p is not None else None
        if net:
            if ctrl:
                self.sel_nets ^= {net}
            else:
                self.sel_nets = {net}
                self.sel_vias = []
            self._emit_selection()
            return True
        return False

    def _toggle(self, lst, obj):
        if obj in lst:
            lst.remove(obj)
        else:
            lst.append(obj)

    # ------------------------------------------------------------ acciones
    def rotate_selection(self, delta: float = 90.0):
        """Gira 90° las huellas seleccionadas (o la que está bajo el cursor) alrededor de su centro."""
        if self.board is None:
            return
        fps = [f for f in (self.sel_fps or ([self._hover_fp] if self._hover_fp else [])) if not f.locked]
        if not fps:
            self.info.emit(tr("Selecciona antes las huellas a girar"))
            return
        self.beforeEdit.emit()
        plan = E.plan_footprint_move(self.board, fps)
        cx, cy = E.selection_center(fps)
        E.apply_footprint_plan(plan, E.rotation(cx, cy, delta), delta)
        self.footprintMoved.emit(fps)

    def flip_selection(self):
        """Pasa las huellas seleccionadas a la otra cara (espejo horizontal sobre su origen).

        Las pistas/vías generadas de sus redes se borran: los pads cambian de capa y habrá que reenrutar.
        """
        if self.board is None:
            return
        fps = [f for f in (self.sel_fps or ([self._hover_fp] if self._hover_fp else [])) if not f.locked]
        if not fps:
            self.info.emit(tr("Selecciona antes las huellas a cambiar de cara"))
            return
        if self._drag and self._drag.get("kind") == "fp":
            self._cancel_drag()
        self.beforeEdit.emit()
        nets = {p.net for fp in fps for p in fp.pads if p.net}
        for fp in fps:
            fp.flip()
        self.board.tracks = [t for t in self.board.tracks if not (t.net in nets and E.is_generated(t))]
        self.board.vias = [v for v in self.board.vias if not (v.net in nets and E.is_generated(v))]
        self.sel_tracks, self.sel_vias = [], []
        self.footprintMoved.emit(fps)

    def rotate_footprint(self, fp, delta: float = 90.0):   # compatibilidad
        if fp is not None and fp not in self.sel_fps:
            self.sel_fps = [fp]
        self.rotate_selection(delta)

    def delete_selected_tracks(self):
        if self.board is None or not (self.sel_tracks or self.sel_vias):
            return
        trs = [t for t in self.sel_tracks if E.is_generated(t)]
        vs = [v for v in self.sel_vias if E.is_generated(v)]
        self.beforeEdit.emit()
        ids_t, ids_v = {id(t) for t in trs}, {id(v) for v in vs}
        self.board.tracks = [t for t in self.board.tracks if id(t) not in ids_t]
        self.board.vias = [v for v in self.board.vias if id(v) not in ids_v]
        self.sel_tracks, self.sel_vias = [], []
        self.tracksEdited.emit(tr("Borrados {n} segmentos y {v} vías", n=len(trs), v=len(vs)))

    # ------------------------------------------------------------ ratón
    def mousePressEvent(self, ev):
        if ev.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan = (ev.position().x(), ev.position().y())
            self.viewport().setCursor(Qt.ClosedHandCursor)
            ev.accept()
            return
        if ev.button() == Qt.LeftButton and self.board is not None:
            p = self.mapToScene(ev.position().toPoint())
            x, y = p.x(), p.y()
            ctrl = bool(ev.modifiers() & Qt.ControlModifier)
            self.setFocus()
            if self.mode is None:
                if self._select_press(x, y, ctrl):
                    ev.accept()
                    return
            elif self.mode == "fp":
                fp = self.footprint_at(x, y)
                if fp is not None:
                    if ctrl:
                        self._toggle(self.sel_fps, fp)
                        self._draw_selection()
                    else:
                        if fp not in self.sel_fps:
                            self.sel_fps = [fp]
                            self._draw_selection()
                        self._start_fp_drag(fp, x, y)
                    ev.accept()
                    return
            else:
                v = E.via_at(self.board, x, y, tol=self._px(2))
                t = None if v is not None else E.track_at(self.board, x, y, self.visible_layers(), tol=self._px(3))
                obj, lst = (v, self.sel_vias) if v is not None else (t, self.sel_tracks)
                if obj is not None:
                    if not E.is_generated(obj):
                        self.info.emit(tr("Esa pista/vía viene del archivo original: solo se pueden editar las generadas"))
                    elif ctrl:
                        self._toggle(lst, obj)
                        self._draw_selection()
                    else:
                        if obj not in lst:
                            self.sel_tracks, self.sel_vias = [], []
                            (self.sel_vias if v is not None else self.sel_tracks).append(obj)
                            self._draw_selection()
                        self._start_track_drag(x, y)
                    ev.accept()
                    return
            # zona vacía: cuadro de selección
            if not ctrl:
                self.clear_selection()
                if self.mode is None and self.sel_nets:
                    self.sel_nets = set()
                    self._emit_selection()
            rect = self.scene().addRect(QRectF(x, y, 0, 0), QPen(QColor(0, 230, 255), 0, Qt.DashLine),
                                        QBrush(QColor(0, 230, 255, 30)))
            rect.setZValue(70)
            self._drag = {"kind": "rubber", "start": (x, y), "item": rect}
            ev.accept()
            return
        super().mousePressEvent(ev)

    def _start_fp_drag(self, anchor_fp, x, y):
        fps = [f for f in self.sel_fps if not f.locked]
        if not fps:
            self.info.emit(tr("Huella bloqueada en KiCad: no se puede mover"))
            return
        plan = E.plan_footprint_move(self.board, fps)
        # al partir pistas por las uniones en T cambian los objetos: refrescar sus elementos
        self._refresh_track_items()
        rigid = {id(t) for t in plan.rigid_tracks}
        ends: Dict[int, set] = {}
        for t, k in plan.stretch:
            ends.setdefault(id(t), set()).add(k)
        moved = {id(f) for f in fps}
        rats = [(it, a, b) for it, a, b in self.rats_meta
                if id(a.footprint) in moved or id(b.footprint) in moved]
        self._drag = {"kind": "fp", "start": (x, y), "anchor": anchor_fp, "fps": fps, "plan": plan,
                      "rigid": rigid, "ends": ends, "d": (0.0, 0.0), "rats": rats,
                      "tracks": [t for t in self.board.tracks if id(t) in rigid or id(t) in ends],
                      "vias": plan.rigid_vias + plan.moved_vias, "moved": moved}
        for f in fps:
            for it in self.fp_items.get(id(f), []):
                it.setOpacity(0.65)

    def _start_track_drag(self, x, y):
        plan = E.plan_track_move(self.board, self.sel_tracks, self.sel_vias)
        self._refresh_track_items()
        bridges = []
        for (pt, layer, w, net) in plan.bridges:
            it = self.scene().addLine(pt[0], pt[1], pt[0], pt[1],
                                      QPen(layer_color(layer), w, Qt.SolidLine, Qt.RoundCap))
            it.setZValue(54)
            bridges.append((it, pt))
        self._drag = {"kind": "track", "start": (x, y), "plan": plan, "d": (0.0, 0.0), "bridges": bridges,
                      "orig": {id(t): (t.start, t.end) for t in plan.tracks + [o for o, _ in plan.follow]},
                      "vorig": {id(v): (v.x, v.y) for v in plan.vias}}

    def _refresh_track_items(self):
        """Crea elementos gráficos para pistas nuevas (p. ej. tras partir uniones en T)."""
        sc = self.scene()
        alive = {id(t) for t in self.board.tracks}
        for tid in [k for k in self.track_item if k not in alive]:
            try:
                sc.removeItem(self.track_item.pop(tid))
            except RuntimeError:
                pass
        zmap = {l: i for i, l in enumerate(reversed(self.board.copper_layers))}
        for t in self.board.tracks:
            if id(t) not in self.track_item:
                it = sc.addLine(t.start[0], t.start[1], t.end[0], t.end[1],
                                QPen(layer_color(t.layer), t.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                it.setZValue(zmap.get(t.layer, 0))
                it.setData(0, t.net)
                self.track_item[id(t)] = it
                self.layer_items.setdefault(t.layer, []).append(it)
                self.net_items.setdefault(t.net, []).append(it)

    def mouseMoveEvent(self, ev):
        if self._pan is not None:
            px, py = self._pan
            nx, ny = ev.position().x(), ev.position().y()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(nx - px))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(ny - py))
            self._pan = (nx, ny)
            ev.accept()
            return
        p = self.mapToScene(ev.position().toPoint())
        x, y = p.x(), p.y()
        self.mouseMoved.emit(x, y)
        d = self._drag
        if d is not None:
            if d["kind"] == "rubber":
                sx, sy = d["start"]
                d["item"].setRect(QRectF(min(sx, x), min(sy, y), abs(x - sx), abs(y - sy)))
            elif d["kind"] == "fp":
                fp, (sx, sy) = d["anchor"], d["start"]
                ndx = self._snapped(fp.x + x - sx) - fp.x
                ndy = self._snapped(fp.y + y - sy) - fp.y
                odx, ody = d["d"]
                if abs(ndx - odx) > 1e-9 or abs(ndy - ody) > 1e-9:
                    for f in d["fps"]:
                        for it in self.fp_items.get(id(f), []):
                            it.moveBy(ndx - odx, ndy - ody)
                    for it in self._sel_items:
                        it.moveBy(ndx - odx, ndy - ody)
                    G = E.translation(ndx, ndy)
                    for t in d["tracks"]:
                        a, b = E.preview_track(t, d["ends"], d["rigid"], G)
                        it = self.track_item.get(id(t))
                        if it is not None:
                            it.setLine(a[0], a[1], b[0], b[1])
                    for v in d["vias"]:
                        for it in self.via_item.get(id(v), []):
                            it.setPos(ndx, ndy)
                    for it, pa, pb in d["rats"]:
                        a = G((pa.x, pa.y)) if id(pa.footprint) in d["moved"] else (pa.x, pa.y)
                        b = G((pb.x, pb.y)) if id(pb.footprint) in d["moved"] else (pb.x, pb.y)
                        it.setLine(a[0], a[1], b[0], b[1])
                    d["d"] = (ndx, ndy)
            elif d["kind"] == "track":
                sx, sy = d["start"]
                ndx, ndy = self._snapped(x - sx), self._snapped(y - sy)
                if (ndx, ndy) != d["d"]:
                    self._preview_track_drag(ndx, ndy)
                    d["d"] = (ndx, ndy)
            ev.accept()
            return
        if self.mode == "fp":
            self._hover_fp = self.footprint_at(x, y)
            self.setFocus()
        super().mouseMoveEvent(ev)

    def _preview_track_drag(self, dx, dy):
        d = self._drag
        plan = d["plan"]
        G = E.translation(dx, dy)
        sel = {id(t) for t in plan.tracks}
        moved_pts = {}
        for t in plan.tracks:
            a, b = d["orig"][id(t)]
            moved_pts[id(t)] = (G(a), G(b))
        for o, k in plan.follow:
            a, b = moved_pts.get(id(o), d["orig"][id(o)])
            moved_pts[id(o)] = (G(a), b) if k == 0 else (a, G(b))
        for tid, (a, b) in moved_pts.items():
            it = self.track_item.get(tid)
            if it is not None:
                it.setLine(a[0], a[1], b[0], b[1])
        for v in plan.vias:
            for it in self.via_item.get(id(v), []):
                it.setPos(dx, dy)
        for it, pt in d["bridges"]:
            it.setLine(pt[0], pt[1], pt[0] + dx, pt[1] + dy)
        for it in self._sel_items:
            it.setPos(dx, dy)

    def mouseReleaseEvent(self, ev):
        if self._pan is not None and ev.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan = None
            self.viewport().setCursor(Qt.ArrowCursor if self.mode is None else
                                      (Qt.SizeAllCursor if self.mode == "fp" else Qt.CrossCursor))
            ev.accept()
            return
        d = self._drag
        if d is not None and ev.button() == Qt.LeftButton:
            self._drag = None
            if d["kind"] == "rubber":
                sx, sy = d["start"]
                p = self.mapToScene(ev.position().toPoint())
                x, y = p.x(), p.y()
                self.scene().removeItem(d["item"])
                touching = x < sx          # de derecha a izquierda: basta con tocar (como KiCad)
                if self.mode is None:
                    x0, x1, y0, y1 = min(sx, x), max(sx, x), min(sy, y), max(sy, y)
                    vis = self.visible_layers()

                    def inside(q):
                        return x0 <= q[0] <= x1 and y0 <= q[1] <= y1
                    for t in self.board.tracks:
                        if t.layer in vis and ((inside(t.start) or inside(t.end)) if touching
                                               else (inside(t.start) and inside(t.end))):
                            self.sel_nets.add(t.net)
                    for pd in self.board.pads:
                        if pd.net and inside((pd.x, pd.y)):
                            self.sel_nets.add(pd.net)
                    for v in self.board.vias:
                        if inside((v.x, v.y)) and v not in self.sel_vias:
                            self.sel_vias.append(v)
                    self.sel_nets.discard("")
                    self._emit_selection()
                    self.info.emit(tr("{n} redes y {v} vías seleccionadas", n=len(self.sel_nets), v=len(self.sel_vias)))
                    ev.accept()
                    return
                if self.mode == "fp":
                    for fp in E.footprints_in_rect(self.board, sx, sy, x, y, touching):
                        if fp not in self.sel_fps:
                            self.sel_fps.append(fp)
                else:
                    x0, x1, y0, y1 = min(sx, x), max(sx, x), min(sy, y), max(sy, y)
                    vis = self.visible_layers()

                    def inside(q):
                        return x0 <= q[0] <= x1 and y0 <= q[1] <= y1
                    for t in self.board.tracks:
                        if E.is_generated(t) and t.layer in vis and t not in self.sel_tracks and \
                                ((inside(t.start) or inside(t.end)) if touching else (inside(t.start) and inside(t.end))):
                            self.sel_tracks.append(t)
                    for v in self.board.vias:
                        if E.is_generated(v) and inside((v.x, v.y)) and v not in self.sel_vias:
                            self.sel_vias.append(v)
                self._draw_selection()
                n = len(self.sel_fps) if self.mode == "fp" else len(self.sel_tracks) + len(self.sel_vias)
                self.info.emit(tr("{n} elementos seleccionados", n=n))
            elif d["kind"] == "fp":
                dx, dy = d["d"]
                if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                    self.beforeEdit.emit()
                    E.apply_footprint_plan(d["plan"], E.translation(dx, dy))
                    self.footprintMoved.emit(d["fps"])
                else:
                    for f in d["fps"]:
                        for it in self.fp_items.get(id(f), []):
                            it.setOpacity(1.0)
            elif d["kind"] == "track":
                dx, dy = d["d"]
                for it, _ in d["bridges"]:
                    self.scene().removeItem(it)
                if abs(dx) > 1e-9 or abs(dy) > 1e-9:
                    self.beforeEdit.emit()
                    new = E.apply_track_plan(self.board, d["plan"], dx, dy)
                    msg = tr("Movidos {n} segmentos y {v} vías", n=len(d["plan"].tracks), v=len(d["plan"].vias))
                    if new:
                        msg += " " + tr("(+{n} tramos de unión con pads)", n=len(new))
                    self.tracksEdited.emit(msg)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def _cancel_drag(self):
        d = self._drag
        self._drag = None
        if d is None:
            return
        if d["kind"] == "rubber":
            self.scene().removeItem(d["item"])
        else:
            self.rebuild_keep()

    def rebuild_keep(self):
        """Redibuja sin perder el encuadre."""
        self.rebuild(show_ratsnest=True)

    def keyPressEvent(self, ev):
        k = ev.key()
        if self.mode == "fp" and k == Qt.Key_R:
            if self._drag and self._drag["kind"] == "fp":       # aplicar el movimiento pendiente
                d = self._drag
                self._drag = None
                self.beforeEdit.emit()
                E.apply_footprint_plan(d["plan"], E.translation(*d["d"]))
            self.rotate_selection(-90.0 if ev.modifiers() & Qt.ShiftModifier else 90.0)
            return
        if self.mode == "fp" and k == Qt.Key_F:
            self.flip_selection()
            return
        if self.mode is None and k == Qt.Key_Escape:
            self.sel_nets, self.sel_vias = set(), []
            self._emit_selection()
            return
        if self.mode and k == Qt.Key_Escape:
            if self._drag:
                self._cancel_drag()
            else:
                self.clear_selection()
            return
        if self.mode == "track" and k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected_tracks()
            return
        super().keyPressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if self.mode:
            ev.accept()
            return
        it = self.itemAt(ev.position().toPoint())
        net = it.data(0) if it is not None else None
        self.netClicked.emit(net or "")
        super().mouseDoubleClickEvent(ev)

    def center_on(self, x: float, y: float, zoom_mm: float = 6.0):
        self.fitInView(QRectF(x - zoom_mm, y - zoom_mm, 2 * zoom_mm, 2 * zoom_mm), Qt.KeepAspectRatio)
