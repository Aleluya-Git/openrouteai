"""Ventana principal de OpenRouteAI (PySide6 / Qt 6)."""
from __future__ import annotations

import copy
import os
import sys
import time
import traceback
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QSize, QThread, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
                               QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..i18n import LANGUAGES, language, load_config, save_config, set_language, tr
from ..routing.router import STRATEGIES, RouterConfig
from .board_view import BoardView
from .view3d import Board3DView

DEFAULT_WIDTHS = [0.2, 0.25, 0.3, 0.4, 0.5, 0.8, 1.0]
DEFAULT_VIAS = [(0.6, 0.3), (0.8, 0.4), (1.0, 0.5)]
_WINDOWS: List["MainWindow"] = []        # mantiene viva la ventana al cambiar de idioma


def strategy_names() -> Dict[str, str]:
    return {
        "negotiated": tr("Negotiated Congestion (PathFinder)"),
        "ripup": tr("A* + Rip-up and Retry"),
        "astar": tr("A* secuencial (sin rip-up)"),
        "maze": tr("Maze routing (Dijkstra) + rip-up"),
        "lee": tr("Algoritmo de Lee + rip-up"),
        "hadlock": tr("Algoritmo de Hadlock + rip-up"),
    }


METRIC_NAMES = {
    "componentes": "Componentes", "pads": "Pads", "redes": "Redes", "redes_enrutables": "Redes enrutables",
    "conexiones": "Conexiones", "pares_diferenciales": "Pares diferenciales", "completado_%": "Completado %",
    "longitud_total_mm": "Longitud total (mm)", "vias": "Vías", "codos": "Codos", "tiempo_s": "Tiempo (s)",
    "iteraciones": "Iteraciones", "congestion_max": "Congestión máx.", "coste_fabricacion_rel": "Coste de fabricación (rel.)",
    "motor": "Motor", "redes_fallidas": "Redes sin completar",
}


def metric_label(k: str) -> str:
    if k in METRIC_NAMES:
        return tr(METRIC_NAMES[k])
    if k.startswith("redes_"):
        return tr("Redes ({kind})", kind=k[6:])
    if k.startswith("drc_"):
        return "DRC: " + k[4:]
    return k


class Worker(QObject):
    progress = Signal(str, float)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            res = self.fn(lambda m, p: self.progress.emit(m, p))
            self.finished.emit(res)
        except Exception:
            self.failed.emit(traceback.format_exc())


def _clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            it.widget().deleteLater()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        from .branding import app_icon
        self.setWindowIcon(app_icon())
        self.resize(1500, 920)
        self.project = None
        self.result = None
        self.zones = []
        self.runs: List[dict] = []
        self.placement_changed = False
        self._orig_zones = []
        self._thread: Optional[QThread] = None
        self.net_widths: Dict[str, float] = {}               # ancho de pista elegido por red (mm)
        self.net_vias: Dict[str, Tuple[float, float]] = {}   # vía elegida por red (Ø, taladro)
        self.undo_stack: List[tuple] = []
        cfg = load_config()
        self.track_widths: List[float] = list(cfg.get("track_widths") or DEFAULT_WIDTHS)
        self.via_sizes: List[Tuple[float, float]] = [tuple(v) for v in (cfg.get("via_sizes") or DEFAULT_VIAS)]
        self._syncing = False

        self.view = BoardView()
        self.view3d = Board3DView()
        self.view.netClicked.connect(self._net_clicked)
        self.view.mouseMoved.connect(lambda x, y: self.coord.setText(f"X {x:7.2f}  Y {y:7.2f} mm"))
        self.view.footprintMoved.connect(self._footprint_moved)
        self.view.tracksEdited.connect(self._tracks_edited)
        self.view.beforeEdit.connect(self._push_undo)
        self.view.info.connect(lambda m: self.statusBar().showMessage(m))
        self.view.selectionChanged.connect(self._view_selection)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.view, tr("Vista 2D"))
        self.tabs.addTab(self._build_3d_tab(), tr("Vista 3D"))
        self.setCentralWidget(self.tabs)
        self._build_menu()
        self._build_toolbar()
        self._build_controls()
        self._build_info()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.coord = QLabel("")
        self.statusBar().addPermanentWidget(self.coord)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage(tr("Abre un proyecto KiCad (.kicad_pro o .kicad_pcb)"))
        self._set_enabled(False)

    # ================================================================= UI
    def _build_3d_tab(self) -> QWidget:
        """Pestaña 3D: barra de visibilidad + vista."""
        from .view3d import SHOW_DEFAULTS
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        bar.addWidget(QLabel("<b>" + tr("Capas de cobre:") + "</b>"))
        self.l3d_layers = QHBoxLayout()
        bar.addLayout(self.l3d_layers)
        bar.addSpacing(16)
        bar.addWidget(QLabel("<b>" + tr("Mostrar:") + "</b>"))
        self.chk_show3d = {}
        for key, label in (("ref", tr("Referencias")), ("value", tr("Valores")), ("fp", tr("Huellas")),
                           ("bodies", tr("Cuerpos"))):
            cb = QCheckBox(label)
            cb.setChecked(self.view3d.show.get(key, SHOW_DEFAULTS[key]))
            cb.toggled.connect(lambda on, k=key: self.view3d.set_show(k, on))
            bar.addWidget(cb)
            self.chk_show3d[key] = cb
        bar.addStretch(1)
        v.addLayout(bar)
        v.addWidget(self.view3d, 1)
        return w

    def _build_toolbar(self):
        from .icons import icon
        tb = QToolBar(tr("Acciones"), self)
        tb.setObjectName("acciones")
        tb.setIconSize(QSize(30, 30))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        def act(name, text, tip, slot, shortcut=None, checkable=False):
            a = QAction(icon(name), text, self)
            a.setToolTip(tip)
            a.setCheckable(checkable)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        act("open", tr("Abrir"), tr("Abrir proyecto KiCad (Ctrl+O)"), self.open_dialog)
        self.act_save = act("save", tr("Guardar"), tr("Guardar .kicad_pcb enrutado (Ctrl+S)"), self.save_dialog)
        tb.addSeparator()
        self.act_route = act("route", tr("Enrutar todo"), tr("Enrutar todas las redes (F5)"),
                             lambda: self.start_route(partial=False), "F5")
        self.act_partial = act("partial", tr("Reenrutar selección"),
                               tr("Reenrutar solo las redes seleccionadas (en la vista o en la pestaña «Redes»)"),
                               lambda: self.start_route(partial=True))
        self.act_drc = act("drc", tr("DRC"), tr("Comprobar reglas de diseño"), self.run_drc)
        self.act_clear = act("clear", tr("Borrar enrutado"), tr("Quitar las pistas y vías generadas"),
                             self.clear_routing)
        tb.addSeparator()
        self.act_select = act("select", tr("Seleccionar"),
                              tr("Clic en una pista o pad selecciona su red · clic en una vía la selecciona · "
                                 "Ctrl+clic añade · cuadro para varias · botón central o derecho desplaza"),
                              lambda on: self._set_mode(None), "S", checkable=True)
        self.act_move = act("move", tr("Mover huellas"),
                            tr("Clic, Ctrl+clic o cuadro para seleccionar · arrastra para mover · "
                               "R gira 90° (Mayús+R al revés) · F cambia de cara · Esc cancela"),
                            lambda on: self._set_mode("fp" if on else None), "M", checkable=True)
        self.act_rotate = act("rotate", tr("Girar 90°"), tr("Girar las huellas seleccionadas (tecla R en modo mover)"),
                              lambda: self.view.rotate_selection(90.0))
        self.act_flip = act("flip", tr("Cambiar de cara"),
                            tr("Pasar las huellas seleccionadas a la otra cara de la placa, en espejo "
                               "(tecla F en modo mover)"),
                            lambda: self.view.flip_selection())
        self.act_edit = act("track", tr("Editar pistas"),
                            tr("Clic, Ctrl+clic o cuadro para seleccionar segmentos/vías · arrastra para moverlos "
                               "sin romper las conexiones · Supr borra · Esc cancela"),
                            lambda on: self._set_mode("track" if on else None), "E", checkable=True)
        self.act_undo = act("undo", tr("Deshacer"), tr("Deshacer el último movimiento o edición (Ctrl+Z)"),
                            self.undo, QKeySequence.Undo)
        tb.addSeparator()
        self.act_fill = act("fill", tr("Rellenar zonas"),
                            tr("Calcular el relleno de cobre de los planos y zonas (B)"), self.fill_zones, "B")
        tb.addWidget(QLabel("  " + tr("Rejilla:") + " "))
        self.cb_snap = QComboBox()
        for v in (0.05, 0.1, 0.25, 0.5, 0.635, 1.0, 1.27, 2.54):
            self.cb_snap.addItem(f"{v:g} mm", v)
        self.cb_snap.setCurrentIndex([0.05, 0.1, 0.25, 0.5, 0.635, 1.0, 1.27, 2.54].index(self.view.snap)
                                     if self.view.snap in (0.05, 0.1, 0.25, 0.5, 0.635, 1.0, 1.27, 2.54) else 3)
        self.cb_snap.setToolTip(tr("Rejilla a la que se ajustan huellas y pistas al moverlas"))
        self.cb_snap.currentIndexChanged.connect(lambda _: setattr(self.view, "snap", float(self.cb_snap.currentData())))
        tb.addWidget(self.cb_snap)
        tb.addSeparator()
        self.act_prefs = act("prefs", tr("Preferencias"), tr("Idioma, tema claro/oscuro y rejilla de colocación"),
                             self.preferences)
        self.act_select.setChecked(True)

    def _build_menu(self):
        mb = self.menuBar()
        mb.clear()
        m = mb.addMenu(tr("&Archivo"))
        for text, slot, sc in ((tr("&Abrir proyecto..."), self.open_dialog, QKeySequence.Open),
                               (tr("&Guardar .kicad_pcb enrutado..."), self.save_dialog, QKeySequence.Save),
                               (tr("Exportar imagen PNG..."), self.export_png, None)):
            a = QAction(text, self)
            if sc:
                a.setShortcut(sc)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        a = QAction(tr("Salir"), self)
        a.triggered.connect(self.close)
        m.addAction(a)

        t = mb.addMenu(tr("&Herramientas"))
        for text, slot in ((tr("Generar placas de prueba..."), self.gen_tests),
                           (tr("Aprender de proyectos KiCad (IA)..."), self.learn_dialog),
                           (tr("Cargar modelo de priors (IA)..."), self.load_model),
                           (tr("Exportar grafo de la placa (JSON)..."), self.export_graph),
                           (tr("Plugins cargados"), self.show_plugins)):
            a = QAction(text, self)
            a.triggered.connect(slot)
            t.addAction(a)

        lm = mb.addMenu(tr("&Idioma") + " / Language")
        grp = QActionGroup(self)
        for code, name in LANGUAGES.items():
            a = QAction(name, self)
            a.setCheckable(True)
            a.setChecked(code == language())
            a.triggered.connect(lambda _=False, c=code: self.switch_language(c))
            grp.addAction(a)
            lm.addAction(a)

        v = mb.addMenu(tr("&Ver"))
        self.act_dark = QAction(tr("Modo oscuro"), self)
        self.act_dark.setCheckable(True)
        self.act_dark.setChecked(load_config().get("theme", "light") == "dark")
        self.act_dark.toggled.connect(self.set_theme)
        v.addAction(self.act_dark)

        h = mb.addMenu(tr("A&yuda"))
        a = QAction(tr("Acerca de"), self)
        a.triggered.connect(self._about)
        h.addAction(a)

    def preferences(self):
        """Diálogo de preferencias: idioma, tema y rejilla."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Preferencias"))
        f = QFormLayout(dlg)
        cb_lang = QComboBox()
        for code, name in LANGUAGES.items():
            cb_lang.addItem(name, code)
        cb_lang.setCurrentIndex(max(0, list(LANGUAGES).index(language()) if language() in LANGUAGES else 0))
        f.addRow(tr("Idioma") + " / Language", cb_lang)
        cb_theme = QComboBox()
        cb_theme.addItem(tr("Tema claro"), "light")
        cb_theme.addItem(tr("Tema oscuro"), "dark")
        cb_theme.setCurrentIndex(1 if self.act_dark.isChecked() else 0)
        f.addRow(tr("Tema"), cb_theme)
        cb_grid = QComboBox()
        for i in range(self.cb_snap.count()):
            cb_grid.addItem(self.cb_snap.itemText(i), self.cb_snap.itemData(i))
        cb_grid.setCurrentIndex(self.cb_snap.currentIndex())
        f.addRow(tr("Rejilla:"), cb_grid)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        self.cb_snap.setCurrentIndex(cb_grid.currentIndex())
        dark = cb_theme.currentData() == "dark"
        if dark != self.act_dark.isChecked():
            self.act_dark.setChecked(dark)          # aplica y guarda el tema
        code = cb_lang.currentData()
        if code and code != language():
            self.switch_language(code)

    def set_theme(self, dark: bool):
        from .theme import apply_theme
        mode = apply_theme("dark" if dark else "light")
        save_config(theme=mode)
        self.statusBar().showMessage(tr("Tema oscuro") if dark else tr("Tema claro"))

    def _about(self):
        from ..routing import gpu as _gpu, native as _native
        from .branding import logo_pixmap
        box = QMessageBox(self)
        box.setWindowTitle(tr("Acerca de") + f" {APP_NAME}")
        pm = logo_pixmap(420)
        if not pm.isNull():
            box.setIconPixmap(pm)
        box.setText(f"<b>{APP_NAME} {__version__}</b><br>" + tr("Autorouter de PCB de código abierto para KiCad.") +
                    "<br>" + tr("Búsqueda:") + f" {_native.status()}<br>GPU: {_gpu.short_status()}<br>" +
                    tr("Licencia GPL-3.0-or-later.") +
                    "<br><a href='https://github.com/Aleluya-Git/openrouteai'>github.com/Aleluya-Git/openrouteai</a>")
        box.setTextFormat(Qt.RichText)
        box.exec()

    def _spin(self, lo, hi, dec=2, step=0.05, val=None, special=None):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(dec)
        sp.setSingleStep(step)
        if special:
            sp.setSpecialValueText(special)
        if val is not None:
            sp.setValue(val)
        return sp

    def _build_controls(self):
        dock = QDockWidget(tr("Enrutado"), self)
        dock.setObjectName("dock_enrutado")
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        w = QWidget()
        lay = QVBoxLayout(w)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(350)

        # --- estrategia
        g = QGroupBox(tr("Estrategia"))
        f = QFormLayout(g)
        self.cb_strategy = QComboBox()
        for k, name in strategy_names().items():
            self.cb_strategy.addItem(name, k)
        f.addRow(self.cb_strategy)
        self.sp_pitch = self._spin(0.0, 1.0, 3, 0.01, 0.0, tr("automático"))
        f.addRow(tr("Paso de rejilla (mm)"), self.sp_pitch)
        self.sp_iter = QSpinBox()
        self.sp_iter.setRange(1, 200)
        self.sp_iter.setValue(25)
        f.addRow(tr("Iteraciones máx."), self.sp_iter)
        self.sp_via = self._spin(0.1, 10, 2, 0.1, 1.5)
        f.addRow(tr("Coste de vía (mm eq.)"), self.sp_via)
        self.sp_dir = self._spin(0, 2, 2, 0.1, 0.3)
        f.addRow(tr("Penalización dirección"), self.sp_dir)
        self.sp_time = QSpinBox()
        self.sp_time.setRange(10, 36000)
        self.sp_time.setValue(900)
        f.addRow(tr("Tiempo máx. (s)"), self.sp_time)
        self.chk_keep = QCheckBox(tr("Conservar pistas existentes"))
        self.chk_keep.setChecked(True)
        f.addRow(self.chk_keep)
        self.chk_cleanup = QCheckBox(tr("Depurar pistas tras enrutar"))
        self.chk_cleanup.setChecked(True)
        self.chk_cleanup.setToolTip(tr("Endereza las pistas: sustituye quiebros innecesarios por la conexión "
                                       "recta/45° más corta cuando respeta el clearance"))
        f.addRow(self.chk_cleanup)
        lay.addWidget(g)

        # --- pistas y vías
        g = QGroupBox(tr("Pistas y vías"))
        f = QFormLayout(g)
        self.tbl_classes = QTableWidget(0, 5)
        self.tbl_classes.setHorizontalHeaderLabels([tr("Clase"), tr("Pista"), tr("Vía Ø"), tr("Taladro"), tr("Clear.")])
        self.tbl_classes.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_classes.verticalHeader().setVisible(False)
        self.tbl_classes.setToolTip(tr("Tamaños por clase de red (mm), leídos del .kicad_pro. Edita con doble clic."))
        self.tbl_classes.setMaximumHeight(120)
        self.tbl_classes.itemChanged.connect(self._class_edited)
        f.addRow(self.tbl_classes)
        self.sp_via_min = self._spin(0.0, 5.0, 2, 0.05, 0.0, tr("sin límite"))
        f.addRow(tr("Vía Ø mínimo (mm)"), self.sp_via_min)
        self.sp_via_max = self._spin(0.0, 5.0, 2, 0.05, 0.0, tr("sin límite"))
        f.addRow(tr("Vía Ø máximo (mm)"), self.sp_via_max)
        for sp in (self.sp_via_min, self.sp_via_max):
            sp.setToolTip(tr("El diámetro de vía de cada clase se ajusta a este rango; el taladro se reduce "
                             "si hace falta para mantener 0,1 mm de anillo"))

        # lista de anchos de pista
        f.addRow(QLabel("<b>" + tr("Anchos de pista (mm)") + "</b>"))
        self.lst_widths = QListWidget()
        self.lst_widths.setMaximumHeight(96)
        f.addRow(self.lst_widths)
        hb = QHBoxLayout()
        self.sp_new_w = self._spin(0.05, 10.0, 3, 0.05, 0.3)
        hb.addWidget(self.sp_new_w)
        b = QPushButton("+")
        b.setToolTip(tr("Añadir este ancho a la lista"))
        b.setMaximumWidth(34)
        b.clicked.connect(self._add_width)
        hb.addWidget(b)
        b = QPushButton("−")
        b.setToolTip(tr("Quitar el ancho seleccionado de la lista"))
        b.setMaximumWidth(34)
        b.clicked.connect(self._del_width)
        hb.addWidget(b)
        f.addRow(tr("Nuevo"), hb)
        hb = QHBoxLayout()
        b = QPushButton(tr("Aplicar a redes selecc."))
        b.setToolTip(tr("Usa el ancho elegido en la lista para las redes seleccionadas: cambia sus pistas "
                        "ya generadas y se usa en el próximo enrutado"))
        b.clicked.connect(self._apply_net_width)
        hb.addWidget(b)
        b = QPushButton(tr("Ancho de clase"))
        b.setToolTip(tr("Las redes seleccionadas vuelven al ancho de su clase"))
        b.clicked.connect(lambda: self._apply_net_width(reset=True))
        hb.addWidget(b)
        f.addRow(hb)

        # lista de vías
        f.addRow(QLabel("<b>" + tr("Tamaños de vía (Ø / taladro mm)") + "</b>"))
        self.lst_vias = QListWidget()
        self.lst_vias.setMaximumHeight(80)
        f.addRow(self.lst_vias)
        hb = QHBoxLayout()
        self.sp_new_vd = self._spin(0.2, 5.0, 3, 0.05, 0.8)
        self.sp_new_vdr = self._spin(0.1, 4.0, 3, 0.05, 0.4)
        hb.addWidget(self.sp_new_vd)
        hb.addWidget(QLabel("/"))
        hb.addWidget(self.sp_new_vdr)
        b = QPushButton("+")
        b.setToolTip(tr("Añadir este tamaño de vía a la lista"))
        b.setMaximumWidth(34)
        b.clicked.connect(self._add_via)
        hb.addWidget(b)
        b = QPushButton("−")
        b.setToolTip(tr("Quitar el tamaño seleccionado de la lista"))
        b.setMaximumWidth(34)
        b.clicked.connect(self._del_via)
        hb.addWidget(b)
        f.addRow(tr("Nueva"), hb)
        hb = QHBoxLayout()
        b = QPushButton(tr("Aplicar a vías selecc."))
        b.setToolTip(tr("Cambia el tamaño de las vías seleccionadas en la vista (modo Seleccionar o Editar pistas)"))
        b.clicked.connect(self._apply_via_selected)
        hb.addWidget(b)
        b = QPushButton(tr("A redes selecc."))
        b.setToolTip(tr("Usa este tamaño en todas las vías de las redes seleccionadas, también en el próximo enrutado"))
        b.clicked.connect(self._apply_via_nets)
        hb.addWidget(b)
        f.addRow(hb)
        self.lbl_sel = QLabel(tr("Selección: nada"))
        self.lbl_sel.setWordWrap(True)
        f.addRow(self.lbl_sel)
        lay.addWidget(g)
        self._fill_size_lists()

        # --- rendimiento
        g = QGroupBox(tr("Rendimiento"))
        f = QFormLayout(g)
        self.cb_speed = QComboBox()
        self.cb_speed.addItem(tr("Máxima calidad"), 1.0)
        self.cb_speed.addItem(tr("Equilibrado (≈1,5-2× más rápido)"), 1.3)
        self.cb_speed.addItem(tr("Rápido (≈2-3× más rápido)"), 1.6)
        self.cb_speed.setToolTip(tr("A* ponderado: más rápido a cambio de pistas algo más largas y más vías"))
        f.addRow(tr("Modo"), self.cb_speed)
        self.cb_device = QComboBox()
        self.cb_device.addItem(tr("Automático"), "auto")
        self.cb_device.addItem("CPU", "cpu")
        self.cb_device.addItem("GPU (NVIDIA CUDA)", "gpu")
        self.cb_device.setToolTip(tr("La GPU calcula la rejilla de costes; compensa en placas grandes. "
                                     "Automático la usa a partir de ~6 millones de celdas."))
        f.addRow(tr("Dispositivo"), self.cb_device)
        from ..routing import gpu as _gpu, native as _native
        self.lbl_engine = QLabel(tr("Búsqueda:") + f" {_native.status()}<br>GPU: {_gpu.short_status()}")
        self.lbl_engine.setWordWrap(True)
        f.addRow(self.lbl_engine)
        self.cb_device.currentIndexChanged.connect(self._device_changed)
        lay.addWidget(g)

        # --- planos
        g = QGroupBox(tr("Planos y cosido"))
        f = QFormLayout(g)
        self.chk_plane = QCheckBox(tr("Plano de masa"))
        f.addRow(self.chk_plane)
        self.cb_plane_net = QComboBox()
        f.addRow(tr("Red"), self.cb_plane_net)
        self.cb_plane_layer = QComboBox()
        f.addRow(tr("Capa"), self.cb_plane_layer)
        self.chk_plane2 = QCheckBox(tr("Plano también en capa superior (F.Cu)"))
        f.addRow(self.chk_plane2)
        self.sp_stitch = self._spin(0, 50, 1, 0.5, 0.0, tr("sin cosido"))
        f.addRow(tr("Cosido de vías cada (mm)"), self.sp_stitch)
        lay.addWidget(g)

        # --- colocación
        g = QGroupBox(tr("Colocación y optimización"))
        f = QFormLayout(g)
        self.chk_place = QCheckBox(tr("Colocar componentes antes (experimental)"))
        f.addRow(self.chk_place)
        self.cb_opt = QComboBox()
        self.cb_opt.addItem(tr("Sin optimización"), "")
        self.cb_opt.addItem(tr("Algoritmo genético"), "genetic")
        self.cb_opt.addItem(tr("Particle Swarm (PSO)"), "pso")
        f.addRow(tr("Optimizar"), self.cb_opt)
        lay.addWidget(g)

        # --- visualización
        g = QGroupBox(tr("Visualización"))
        v = QVBoxLayout(g)
        self.layer_box = QVBoxLayout()
        v.addLayout(self.layer_box)
        self.chk_rats = QCheckBox(tr("Ratsnest (pendientes)"))
        self.chk_rats.setChecked(True)
        self.chk_rats.toggled.connect(self.view.set_ratsnest_visible)
        v.addWidget(self.chk_rats)
        self.chk_cong = QCheckBox(tr("Mapa de congestión"))
        self.chk_cong.toggled.connect(lambda _: self.refresh())
        v.addWidget(self.chk_cong)
        self.chk_cat2d = {}
        for cat, label in (("ref", tr("Referencias")), ("value", tr("Valores")), ("padnum", tr("Números de pad")),
                           ("fp", tr("Huellas (serigrafía y contorno)"))):
            cb = QCheckBox(label)
            cb.setChecked(self.view.cat_visible.get(cat, True))
            cb.toggled.connect(lambda on, c=cat: self.view.set_category_visible(c, on))
            v.addWidget(cb)
            self.chk_cat2d[cat] = cb
        lay.addWidget(g)
        lay.addStretch(1)
        scroll.setWidget(w)
        dock.setWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.dock_controls = dock

    def _build_info(self):
        dock = QDockWidget(tr("Información"), self)
        dock.setObjectName("dock_info")
        self.btabs = QTabWidget()
        self.stats = QTableWidget(0, 2)
        self.stats.setHorizontalHeaderLabels([tr("Métrica"), tr("Valor")])
        self.stats.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stats.verticalHeader().setVisible(False)
        self.btabs.addTab(self.stats, tr("Estadísticas"))

        self.net_list = QListWidget()
        self.net_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.net_list.itemSelectionChanged.connect(self._net_selected)
        self.btabs.addTab(self.net_list, tr("Redes"))

        self.drc_list = QListWidget()
        self.drc_list.itemDoubleClicked.connect(self._drc_clicked)
        self.btabs.addTab(self.drc_list, "DRC")

        cmpw = QWidget()
        cl = QVBoxLayout(cmpw)
        self.cmp = QTableWidget(0, 8)
        self.cmp.setHorizontalHeaderLabels(["#", tr("Estrategia"), tr("Completado %"), tr("Longitud mm"), tr("Vías"),
                                            tr("Codos"), tr("Tiempo s"), "DRC"])
        self.cmp.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.cmp.setSelectionBehavior(QAbstractItemView.SelectRows)
        cl.addWidget(self.cmp)
        b = QPushButton(tr("Restaurar ruta seleccionada"))
        b.clicked.connect(self.restore_run)
        cl.addWidget(b)
        self.btabs.addTab(cmpw, tr("Comparación"))

        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        self.btabs.addTab(self.logw, tr("Registro"))
        dock.setWidget(self.btabs)
        dock.setMinimumWidth(300)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.dock_info = dock

    def _set_enabled(self, on: bool):
        for wdg in (self.act_route, self.act_partial, self.act_drc, self.act_save, self.act_clear, self.act_select,
                    self.act_move, self.act_rotate, self.act_edit, self.act_undo, self.act_flip, self.act_fill):
            wdg.setEnabled(on)

    def log(self, msg: str):
        self.logw.appendPlainText(msg)

    # ================================================================ idioma
    def switch_language(self, code: str):
        if code == language():
            return
        set_language(code)
        save_config(language=code)
        if self._thread is not None:
            QMessageBox.information(self, APP_NAME, tr("El idioma cambiará al terminar el enrutado en curso."))
            return
        new = MainWindow()
        new.adopt(self)
        new.setGeometry(self.geometry())
        new.show()
        _WINDOWS.append(new)
        self._closing_for_language = True
        self.close()
        if self in _WINDOWS:
            _WINDOWS.remove(self)

    def adopt(self, old: "MainWindow"):
        """Recoge el estado de otra ventana (al cambiar de idioma)."""
        self.track_widths, self.via_sizes = old.track_widths, old.via_sizes
        self._fill_size_lists()
        self.view.snap = old.view.snap
        self.view.cat_visible = dict(old.view.cat_visible)
        self.view3d.show = dict(old.view3d.show)
        for k, cb in self.chk_cat2d.items():
            cb.setChecked(self.view.cat_visible.get(k, True))
        for k, cb in self.chk_show3d.items():
            cb.setChecked(self.view3d.show.get(k, True))
        if old.project is None:
            return
        self._load_project(old.project)
        for attr in ("result", "zones", "runs", "placement_changed", "_orig_zones", "net_widths", "net_vias",
                     "undo_stack"):
            setattr(self, attr, getattr(old, attr))
        for i, r in enumerate(self.runs):
            self._add_cmp_row(i + 1, r["res"], None, r.get("drc", 0))
        self._fill_net_list()
        self.logw.setPlainText(old.logw.toPlainText())
        self.refresh()
        self.show_stats(self.result)

    # ============================================================== modos
    def _set_mode(self, mode):
        for a, m in ((self.act_select, None), (self.act_move, "fp"), (self.act_edit, "track")):
            a.blockSignals(True)
            a.setChecked(mode == m)
            a.blockSignals(False)
        self.view.set_mode(mode)
        self.tabs.setCurrentWidget(self.view)
        if mode == "fp":
            self.statusBar().showMessage(tr("Mover huellas: clic / Ctrl+clic / cuadro para seleccionar, arrastra para "
                                            "mover (las pistas conectadas las siguen), R gira, F cambia de cara, Esc cancela."))
        elif mode == "track":
            self.statusBar().showMessage(tr("Editar pistas: selecciona segmentos o vías y arrástralos; las pistas "
                                            "vecinas se estiran para no romper la conexión. Supr borra."))
        else:
            self.statusBar().showMessage(tr("Seleccionar: clic en pista o pad = su red, clic en vía = la vía, "
                                            "Ctrl+clic añade, cuadro para varias; botón central o derecho desplaza."))

    # ============================================================ deshacer
    def _snapshot(self):
        b = self.project.board
        return ([copy.copy(t) for t in b.tracks], [copy.copy(v) for v in b.vias],
                [(fp.x, fp.y, fp.angle, fp.layer) for fp in b.footprints], self.placement_changed,
                dict(self.net_widths), dict(self.net_vias), [(z, z.filled, z.stale) for z in b.zones])

    def _push_undo(self):
        if self.project:
            self.undo_stack.append(self._snapshot())
            del self.undo_stack[:-30]          # como mucho 30 pasos

    def undo(self):
        if not self.project or not self.undo_stack:
            self.statusBar().showMessage(tr("Nada que deshacer"))
            return
        from ..analysis.netlist_analyzer import analyze
        tracks, vias, poses, placed, widths, nvias, zstate = self.undo_stack.pop()
        b = self.project.board
        b.tracks, b.vias = tracks, vias
        for fp, (x, y, a, lay) in zip(b.footprints, poses):
            if fp.layer != lay:
                fp.flip()
            if (fp.x, fp.y, fp.angle) != (x, y, a):
                fp.set_pose(x, y, a)
        for z, filled, stale in zstate:
            z.filled, z.stale = filled, stale
        self.placement_changed = placed
        self.net_widths, self.net_vias = widths, nvias
        self.project.analysis = analyze(b)
        self.view.clear_selection()
        self._fill_net_list()
        self.refresh()
        self.statusBar().showMessage(tr("Deshecho ({n} pasos más disponibles)", n=len(self.undo_stack)))

    def _mark_stale(self) -> bool:
        """Tras editar: los rellenos de zona dejan de estar al día."""
        any_ = False
        for z in self.project.board.zones:
            if z.filled and not (z.keepout_tracks or z.keepout_vias):
                z.stale = True
                any_ = True
        return any_

    def fill_zones(self):
        """Rellena (o vuelve a rellenar) todas las zonas de cobre de la placa, como la tecla B de KiCad."""
        if not self.project or self._thread is not None:
            return
        b = self.project.board
        zones = [z for z in b.zones if not (z.keepout_tracks or z.keepout_vias)]
        if not zones:
            self.statusBar().showMessage(tr("No hay zonas ni planos que rellenar: activa «Plano de masa» y enruta"))
            return
        from ..zones_fill import fill_board_zones
        self._push_undo()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        t0 = time.time()
        try:
            fill_board_zones(b, zones)
        finally:
            QApplication.restoreOverrideCursor()
        n = sum(len(z.filled) for z in zones)
        self.refresh()
        msg = tr("Zonas rellenadas: {z} ({n} islas) en {t} s", z=len(zones), n=n, t=f"{time.time() - t0:.1f}")
        msg += " — " + self._quick_drc()
        self.statusBar().showMessage(msg)
        self.log(msg)

    def _quick_drc(self) -> str:
        from ..pipeline import drc
        rep = drc(self.project)
        summ = {k: v for k, v in rep.summary().items() if k not in ("diff_skew", "fabricacion")}
        self.show_drc(rep)
        return "DRC: " + (", ".join(f"{k} {v}" for k, v in summ.items()) if summ else tr("sin errores"))

    def _footprint_moved(self, fps):
        """Tras mover/girar huellas: las pistas ya han seguido el movimiento; se reanaliza y se comprueba."""
        from ..analysis.netlist_analyzer import analyze
        if not isinstance(fps, (list, tuple)):
            fps = [fps]
        b = self.project.board
        self.placement_changed = True
        self.project.analysis = analyze(b)
        stale = self._mark_stale()
        clash = set()
        for fp in fps:
            a = fp.bbox()
            for o in b.footprints:
                if o in fps:
                    continue
                ob = o.bbox()
                if min(a[2], ob[2]) > max(a[0], ob[0]) and min(a[3], ob[3]) > max(a[1], ob[1]):
                    clash.add(o.ref)
        self.refresh()
        if len(fps) == 1:
            fp = fps[0]
            msg = f"{fp.ref} → ({fp.x:.2f}, {fp.y:.2f}) mm, {fp.angle:g}°"
        else:
            msg = tr("{n} huellas movidas", n=len(fps))
        msg += " — " + self._quick_drc()
        if clash:
            msg += " — " + tr("¡courtyard solapado con {refs}!", refs=", ".join(sorted(clash)[:5]))
        if stale:
            msg += " — " + tr("planos desactualizados: pulsa B para rellenar")
        self.statusBar().showMessage(msg)
        self.log(msg)

    def _tracks_edited(self, msg: str):
        stale = self._mark_stale()
        self.refresh()
        msg = msg + " — " + self._quick_drc()
        if stale:
            msg += " — " + tr("planos desactualizados: pulsa B para rellenar")
        self.statusBar().showMessage(msg)
        self.log(msg)

    # ============================================================ proyecto
    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Abrir proyecto KiCad"), "",
                                              "KiCad (*.kicad_pro *.kicad_pcb);;" + tr("Todos") + " (*)")
        if path:
            self.open_project(path)

    def open_project(self, path: str):
        from ..pipeline import open_project
        try:
            pr = open_project(path)
        except Exception as exc:
            QMessageBox.critical(self, tr("Error al abrir"), str(exc))
            return
        self._load_project(pr)
        self.result = None
        self.zones = []
        self.runs = []
        self.placement_changed = False
        self.net_widths, self.net_vias = {}, {}
        self.undo_stack = []
        self.cmp.setRowCount(0)
        self._orig_zones = list(pr.board.zones)
        b = pr.board
        # listas de tamaños del proyecto (si las tiene)
        if b.track_widths:
            self.track_widths = sorted(set(self.track_widths) | set(b.track_widths))
        if b.via_sizes:
            self.via_sizes = sorted(set(self.via_sizes) | set(b.via_sizes))
        self._fill_size_lists()
        self._fill_net_list()
        self.show_stats(None)
        self.log(tr("Abierto {path}", path=pr.pcb_path))
        for n in pr.notes:
            self.log("  · " + n)
        self.statusBar().showMessage(tr("{f} huellas, {n} redes, capas: {l}", f=len(b.footprints),
                                        n=len(b.routable_nets()), l=", ".join(b.copper_layers)))

    def _load_project(self, pr):
        """Rellena la interfaz con un proyecto (sin tocar el estado de enrutado)."""
        self.project = pr
        b = pr.board
        self.setWindowTitle(f"{APP_NAME} {__version__} — {os.path.basename(pr.pcb_path)}")
        _clear_layout(self.layer_box)
        for name in b.copper_layers + ["*"]:
            cb = QCheckBox(name if name != "*" else tr("Pads pasantes"))
            cb.setChecked(self.view.layer_visible.get(name, True))
            cb.toggled.connect(lambda on, n=name: self.view.set_layer_visible(n, on))
            self.layer_box.addWidget(cb)
        _clear_layout(self.l3d_layers)
        for name in b.copper_layers:
            cb = QCheckBox(name)
            cb.setChecked(True)
            cb.toggled.connect(lambda on, n=name: self.view3d.set_layer_visible(n, on))
            self.l3d_layers.addWidget(cb)
        self.cb_plane_layer.clear()
        self.cb_plane_layer.addItems(list(reversed(b.copper_layers)))
        self.cb_plane_net.clear()
        nets = sorted(n for n in b.nets if n)
        grounds = [n for n in nets if b.nets[n].kind == "ground"] or nets
        self.cb_plane_net.addItems(grounds + [n for n in nets if n not in grounds])
        self._fill_classes()
        self._set_mode(None)
        self.view.sel_nets = set()
        self.view.set_board(b)
        self.view3d.set_board(b)
        self._set_enabled(True)

    def _fill_net_list(self):
        if not self.project:
            return
        b = self.project.board
        sel = set(self.view.sel_nets)
        self._syncing = True
        self.net_list.clear()
        for n in self.project.analysis.order:
            net = b.nets[n]
            extra = ""
            if n in self.net_widths:
                extra += ", " + tr("pista {w} mm", w=f"{self.net_widths[n]:g}")
            if n in self.net_vias:
                d, r = self.net_vias[n]
                extra += ", " + tr("vía {d}/{r} mm", d=f"{d:g}", r=f"{r:g}")
            it = QListWidgetItem(f"{n}   [{net.kind}, {net.netclass}, {len(net.pads)} pads{extra}]")
            it.setData(Qt.UserRole, n)
            self.net_list.addItem(it)
            if n in sel:
                it.setSelected(True)
        self._syncing = False

    def _fill_classes(self):
        b = self.project.board
        self.tbl_classes.blockSignals(True)
        self.tbl_classes.setRowCount(0)
        for name, nc in b.netclasses.items():
            r = self.tbl_classes.rowCount()
            self.tbl_classes.insertRow(r)
            it = QTableWidgetItem(name)
            it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            self.tbl_classes.setItem(r, 0, it)
            for c, v in enumerate((nc.track_width, nc.via_diameter, nc.via_drill, nc.clearance), start=1):
                self.tbl_classes.setItem(r, c, QTableWidgetItem(f"{v:g}"))
        self.tbl_classes.blockSignals(False)

    def _class_edited(self, item):
        if not self.project or item.column() == 0:
            return
        name = self.tbl_classes.item(item.row(), 0).text()
        nc = self.project.board.netclasses.get(name)
        attr = {1: "track_width", 2: "via_diameter", 3: "via_drill", 4: "clearance"}[item.column()]
        try:
            v = float(item.text().replace(",", "."))
            if not (0.0 < v < 20.0):
                raise ValueError
        except ValueError:
            self.tbl_classes.blockSignals(True)
            item.setText(f"{getattr(nc, attr):g}")
            self.tbl_classes.blockSignals(False)
            return
        setattr(nc, attr, v)
        self.log(tr("Clase {c}: {a} = {v} mm (se aplicará al próximo enrutado)", c=name, a=attr, v=f"{v:g}"))

    # ================================================== listas de tamaños
    def _fill_size_lists(self):
        if not hasattr(self, "lst_widths"):
            return
        self.track_widths = sorted({round(w, 4) for w in self.track_widths})
        self.via_sizes = sorted({(round(d, 4), round(r, 4)) for d, r in self.via_sizes})
        cw = self.lst_widths.currentRow()
        cv = self.lst_vias.currentRow()
        self.lst_widths.clear()
        for w in self.track_widths:
            it = QListWidgetItem(f"{w:g} mm")
            it.setData(Qt.UserRole, w)
            self.lst_widths.addItem(it)
        self.lst_vias.clear()
        for d, r in self.via_sizes:
            it = QListWidgetItem(f"Ø {d:g} / {r:g} mm")
            it.setData(Qt.UserRole, (d, r))
            self.lst_vias.addItem(it)
        if self.lst_widths.count():
            self.lst_widths.setCurrentRow(min(max(cw, 0), self.lst_widths.count() - 1))
        if self.lst_vias.count():
            self.lst_vias.setCurrentRow(min(max(cv, 0), self.lst_vias.count() - 1))
        save_config(track_widths=self.track_widths, via_sizes=[list(v) for v in self.via_sizes])

    def _add_width(self):
        w = round(float(self.sp_new_w.value()), 4)
        self.track_widths.append(w)
        self._fill_size_lists()
        for i in range(self.lst_widths.count()):
            if abs(self.lst_widths.item(i).data(Qt.UserRole) - w) < 1e-9:
                self.lst_widths.setCurrentRow(i)

    def _del_width(self):
        it = self.lst_widths.currentItem()
        if it is not None:
            self.track_widths = [w for w in self.track_widths if abs(w - it.data(Qt.UserRole)) > 1e-9]
            self._fill_size_lists()

    def _add_via(self):
        d, r = round(float(self.sp_new_vd.value()), 4), round(float(self.sp_new_vdr.value()), 4)
        if r >= d:
            QMessageBox.information(self, APP_NAME, tr("El taladro tiene que ser menor que el diámetro de la vía."))
            return
        self.via_sizes.append((d, r))
        self._fill_size_lists()
        for i in range(self.lst_vias.count()):
            if tuple(self.lst_vias.item(i).data(Qt.UserRole)) == (d, r):
                self.lst_vias.setCurrentRow(i)

    def _del_via(self):
        it = self.lst_vias.currentItem()
        if it is not None:
            v = tuple(it.data(Qt.UserRole))
            self.via_sizes = [x for x in self.via_sizes if tuple(x) != v]
            self._fill_size_lists()

    def _selected_nets(self) -> List[str]:
        return sorted(self.view.sel_nets)

    def _need_nets(self) -> Optional[List[str]]:
        sel = self._selected_nets()
        if not sel:
            QMessageBox.information(self, APP_NAME, tr("Selecciona una o más redes: haz clic en sus pistas o pads en la "
                                                       "vista (modo Seleccionar) o elígelas en la pestaña «Redes»."))
            return None
        return sel

    def _apply_net_width(self, reset: bool = False):
        if not self.project:
            return
        from .. import editing as E
        sel = self._need_nets()
        if sel is None:
            return
        it = self.lst_widths.currentItem()
        if not reset and it is None:
            QMessageBox.information(self, APP_NAME, tr("Elige un ancho en la lista de anchos de pista."))
            return
        self._push_undo()
        b = self.project.board
        changed = 0
        for n in sel:
            if reset:
                self.net_widths.pop(n, None)
                w = b.netclass_of(n).track_width
            else:
                w = float(it.data(Qt.UserRole))
                self.net_widths[n] = w
            for t in b.tracks:
                if t.net == n and E.is_generated(t) and abs(t.width - w) > 1e-9:
                    t.width = w
                    changed += 1
        self._fill_net_list()
        self._mark_stale()
        self.refresh()
        msg = (tr("Ancho de clase para {n} redes", n=len(sel)) if reset else
               tr("Pista de {w} mm para {n} redes", w=f"{w:g}", n=len(sel)))
        msg += " — " + tr("{c} segmentos cambiados", c=changed) + " — " + self._quick_drc()
        self.statusBar().showMessage(msg)
        self.log(msg + ": " + ", ".join(sel))

    def _current_via(self):
        it = self.lst_vias.currentItem()
        if it is None:
            QMessageBox.information(self, APP_NAME, tr("Elige un tamaño en la lista de vías."))
            return None
        return tuple(it.data(Qt.UserRole))

    def _apply_via_selected(self):
        if not self.project:
            return
        from .. import editing as E
        vs = [v for v in self.view.sel_vias if E.is_generated(v)]
        if not vs:
            QMessageBox.information(self, APP_NAME, tr("Selecciona una o más vías en la vista: modo Seleccionar, clic "
                                                       "en la vía (Ctrl+clic para varias o un cuadro)."))
            return
        size = self._current_via()
        if size is None:
            return
        self._push_undo()
        for v in vs:
            v.diameter, v.drill = size
        self._mark_stale()
        self.refresh()
        msg = tr("{n} vías cambiadas a Ø {d} / {r} mm", n=len(vs), d=f"{size[0]:g}", r=f"{size[1]:g}") + \
            " — " + self._quick_drc()
        self.statusBar().showMessage(msg)
        self.log(msg)

    def _apply_via_nets(self):
        if not self.project:
            return
        from .. import editing as E
        sel = self._need_nets()
        size = self._current_via() if sel else None
        if size is None:
            return
        self._push_undo()
        changed = 0
        for n in sel:
            self.net_vias[n] = size
            for v in self.project.board.vias:
                if v.net == n and E.is_generated(v):
                    v.diameter, v.drill = size
                    changed += 1
        self._fill_net_list()
        self._mark_stale()
        self.refresh()
        msg = tr("Vía Ø {d} / {r} mm para {n} redes", d=f"{size[0]:g}", r=f"{size[1]:g}", n=len(sel)) + \
            " — " + tr("{c} vías cambiadas", c=changed) + " — " + self._quick_drc()
        self.statusBar().showMessage(msg)
        self.log(msg + ": " + ", ".join(sel))

    # ======================================================== selección
    def _view_selection(self, nets, nvias):
        """La vista ha cambiado la selección: sincronizar la lista de redes."""
        self._syncing = True
        sel = set(nets)
        first = None
        for i in range(self.net_list.count()):
            it = self.net_list.item(i)
            on = it.data(Qt.UserRole) in sel
            it.setSelected(on)
            if on and first is None:
                first = it
        if first is not None:
            self.net_list.scrollToItem(first)
        self._syncing = False
        self._update_sel_label(nets, nvias)

    def _update_sel_label(self, nets, nvias):
        if not nets and not nvias:
            self.lbl_sel.setText(tr("Selección: nada"))
            return
        txt = tr("Selección: {n} redes, {v} vías", n=len(nets), v=nvias)
        if nets:
            txt += " — " + ", ".join(list(nets)[:4]) + ("…" if len(nets) > 4 else "")
        self.lbl_sel.setText(txt)

    def _net_selected(self):
        if self._syncing:
            return
        sel = [it.data(Qt.UserRole) for it in self.net_list.selectedItems()]
        self.view.set_selected_nets(sel)
        self._update_sel_label(sorted(sel), len(self.view.sel_vias))

    def _net_clicked(self, net: str):
        self.view.set_selected_nets({net} if net else set(), emit=True)

    # ======================================================== enrutado
    def refresh(self):
        if not self.project:
            return
        cong = gi = None
        if self.chk_cong.isChecked() and self.result is not None:
            cong, gi = self.result.history + self.result.congestion, self.result.grid_info
        self.view.rebuild(cong, gi, show_ratsnest=self.chk_rats.isChecked())
        self.view3d.update()

    def _device_changed(self, _idx=None):
        from ..routing import gpu as _gpu
        if self.cb_device.currentData() == "gpu" and not _gpu.available():
            QMessageBox.information(self, tr("GPU no disponible"), _gpu.help_text())
            self.cb_device.blockSignals(True)
            self.cb_device.setCurrentIndex(0)      # vuelve a Automático
            self.cb_device.blockSignals(False)

    def _config(self) -> RouterConfig:
        cfg = RouterConfig(strategy=self.cb_strategy.currentData(),
                           pitch=self.sp_pitch.value() or None,
                           max_iterations=self.sp_iter.value(),
                           via_cost_mm=self.sp_via.value(),
                           dir_penalty=self.sp_dir.value(),
                           time_limit=float(self.sp_time.value()),
                           keep_existing=self.chk_keep.isChecked(),
                           stitch_pitch=self.sp_stitch.value(),
                           heur_weight=float(self.cb_speed.currentData()),
                           device=self.cb_device.currentData(),
                           cleanup=self.chk_cleanup.isChecked(),
                           net_widths=dict(self.net_widths),
                           net_vias=dict(self.net_vias),
                           via_min=self.sp_via_min.value(), via_max=self.sp_via_max.value())
        if cfg.via_min and cfg.via_max and cfg.via_min > cfg.via_max:
            cfg.via_min, cfg.via_max = cfg.via_max, cfg.via_min
        if self.chk_plane.isChecked() and self.cb_plane_net.currentText():
            cfg.planes[self.cb_plane_layer.currentText()] = self.cb_plane_net.currentText()
            if self.chk_plane2.isChecked() and "F.Cu" in self.project.board.copper_layers:
                cfg.planes["F.Cu"] = self.cb_plane_net.currentText()
        return cfg

    def start_route(self, partial: bool):
        if not self.project or self._thread is not None:
            return
        cfg = self._config()
        if partial:
            sel = self._selected_nets()
            if not sel:
                QMessageBox.information(self, APP_NAME, tr("Selecciona una o más redes (en la vista o en la pestaña «Redes»)."))
                return
            cfg.nets = set(sel)
        do_place = self.chk_place.isChecked() and not partial
        opt = self.cb_opt.currentData()
        pr = self.project
        orig_zones = list(self._orig_zones)
        self._push_undo()

        def job(progress):
            from ..analysis.netlist_analyzer import analyze
            from ..pipeline import drc, route
            cfg.progress = progress
            if do_place:
                from ..placement.placer import Placer, PlacerConfig
                st = Placer(pr.board, PlacerConfig(progress=progress)).run()
                progress(tr("Colocación: {st}", st=st), 0.0)
                pr.analysis = analyze(pr.board)
            if opt:
                from ..optimization.optimizer import OptConfig, Optimizer
                best, res = Optimizer(pr.board, cfg, OptConfig(method=opt, progress=progress)).run()
                progress(tr("Mejores parámetros: {p}", p={k: round(v, 3) for k, v in best.items() if k != "order"}), 1.0)
            else:
                res = route(pr, cfg)
            if res.zones or not partial:
                pr.board.zones = list(orig_zones) + list(res.zones)
            rep = drc(pr)
            return res, rep, do_place

        self._set_enabled(False)
        self.progress.setValue(0)
        self._thread = QThread(self)
        self._worker = Worker(job)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    def _on_progress(self, msg: str, frac: float):
        self.progress.setValue(int(frac * 100))
        self.statusBar().showMessage(msg)
        self.log(msg)

    def _stop_thread(self):
        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
        self._set_enabled(True)

    def _on_failed(self, tb: str):
        self._stop_thread()
        self.log(tb)
        QMessageBox.critical(self, tr("Error de enrutado"), tb.splitlines()[-1])

    def _on_finished(self, payload):
        self._stop_thread()
        res, rep, placed = payload
        self.result = res
        self.zones = list(self.project.board.zones[len(self._orig_zones):])
        self.placement_changed = self.placement_changed or placed
        self.show_stats(res, rep)
        self.show_drc(rep)
        b = self.project.board
        from ..pipeline import routed_items
        tracks, vias = routed_items(b)
        self.runs.append({"res": res, "tracks": [copy.copy(t) for t in tracks],
                          "vias": [copy.copy(v) for v in vias], "zones": list(self.zones),
                          "drc": rep.count() - rep.count("diff_skew")})
        self._add_cmp_row(len(self.runs), res, rep)
        self.refresh()
        self.progress.setValue(100)
        m = res.metrics()
        gi = res.grid_info
        self.statusBar().showMessage(tr("Enrutado {c}% en {t} s [{e}, {d}] — {v} vías — {l} mm — DRC: {n} avisos",
                                        c=m["completado_%"], t=m["tiempo_s"],
                                        e=tr("nativo (C)") if gi.get("engine") == "nativo" else "Python",
                                        d=gi.get("device", "cpu").upper(), v=m["vias"], l=m["longitud_total_mm"],
                                        n=rep.count()))

    # ========================================================== paneles
    def show_stats(self, res, rep=None):
        rows = []
        if self.project:
            rows += [(k, v) for k, v in self.project.analysis.stats.items()]
        if res:
            rows += [(k, v) for k, v in res.metrics().items()]
            rows.append(("redes_fallidas", ", ".join(res.failed_nets) or "—"))
        if rep:
            for k, v in rep.summary().items():
                rows.append((f"drc_{k}", v))
        self.stats.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.stats.setItem(i, 0, QTableWidgetItem(metric_label(str(k))))
            self.stats.setItem(i, 1, QTableWidgetItem(str(v)))

    def show_drc(self, rep):
        self.drc_list.clear()
        for v in rep.violations:
            it = QListWidgetItem(f"[{v.kind}] {v.message}")
            it.setData(Qt.UserRole, (v.x, v.y))
            self.drc_list.addItem(it)
        if not rep.violations:
            self.drc_list.addItem(tr("Sin violaciones DRC"))

    def _add_cmp_row(self, n, res, rep, drc_count=None):
        m = res.metrics()
        r = self.cmp.rowCount()
        self.cmp.insertRow(r)
        vals = [n, strategy_names().get(res.strategy, res.strategy), m["completado_%"], m["longitud_total_mm"],
                m["vias"], m["codos"], m["tiempo_s"], rep.count() if rep is not None else drc_count]
        for c, v in enumerate(vals):
            self.cmp.setItem(r, c, QTableWidgetItem(str(v)))

    def restore_run(self):
        rows = {i.row() for i in self.cmp.selectedIndexes()}
        if not rows or not self.project:
            return
        self._push_undo()
        run = self.runs[min(rows)]
        b = self.project.board
        b.tracks = [t for t in b.tracks if t.fixed and not getattr(t, "_was_routed", False)] + \
            [copy.copy(t) for t in run["tracks"]]
        b.vias = [v for v in b.vias if v.fixed and not getattr(v, "_was_routed", False)] + \
            [copy.copy(v) for v in run["vias"]]
        self.zones = list(run["zones"])
        b.zones = list(self._orig_zones) + self.zones
        self.result = run["res"]
        self.refresh()
        self.run_drc()
        self.log(tr("Restaurada la ruta #{n}", n=min(rows) + 1))

    def run_drc(self):
        if not self.project:
            return
        from ..pipeline import drc
        rep = drc(self.project)
        self.show_drc(rep)
        self.show_stats(self.result, rep)
        self.btabs.setCurrentWidget(self.drc_list)

    def clear_routing(self):
        if not self.project:
            return
        self._push_undo()
        b = self.project.board
        b.tracks = [t for t in b.tracks if t.fixed and not getattr(t, "_was_routed", False)]
        b.vias = [v for v in b.vias if v.fixed and not getattr(v, "_was_routed", False)]
        self.zones = []
        b.zones = list(self._orig_zones)
        self.result = None
        self.refresh()

    def _drc_clicked(self, item):
        pos = item.data(Qt.UserRole)
        if pos:
            self.tabs.setCurrentWidget(self.view)
            self.view.center_on(*pos)

    # ========================================================== archivos
    def save_dialog(self):
        if not self.project:
            return
        base = os.path.splitext(self.project.pcb_path)[0] + "_enrutado.kicad_pcb"
        path, _ = QFileDialog.getSaveFileName(self, tr("Guardar PCB enrutada"), base, "KiCad PCB (*.kicad_pcb)")
        if not path:
            return
        if os.path.abspath(path) == os.path.abspath(self.project.pcb_path):
            if QMessageBox.question(self, APP_NAME, tr("¿Sobrescribir el archivo original? (se crea copia .bak)")) \
                    != QMessageBox.Yes:
                return
            import shutil
            shutil.copy2(path, path + ".bak")
        from ..pipeline import save
        todo = [z for z in self.project.board.zones
                if not (z.keepout_tracks or z.keepout_vias) and (z.stale or (z in self.zones and not z.filled))]
        if todo:
            from ..zones_fill import fill_board_zones
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                fill_board_zones(self.project.board, todo)
            finally:
                QApplication.restoreOverrideCursor()
            self.log(tr("Zonas rellenadas antes de guardar: {n}", n=len(todo)))
            self.refresh()
        try:
            save(self.project, self.result, path, keep_existing=self.chk_keep.isChecked(),
                 placement_changed=self.placement_changed, zones=self.zones)
            from ..kicad.project import write_size_lists
            pro = os.path.splitext(path)[0] + ".kicad_pro"
            if os.path.exists(pro):
                write_size_lists(pro, self.track_widths, self.via_sizes)
        except Exception as exc:
            QMessageBox.critical(self, tr("Error al guardar"), str(exc))
            return
        msg = tr("Guardado: {path}", path=path)
        if self.zones:
            msg += "\n" + tr("Plano(s) guardado(s) ya rellenos: {n}", n=len(self.zones))
        self.log(msg)
        QMessageBox.information(self, APP_NAME, msg)

    def export_png(self):
        if not self.project:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar PNG"), "placa.png", "PNG (*.png)")
        if path:
            self.view.grab().save(path)

    def gen_tests(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Carpeta para las placas de prueba"))
        if folder:
            from ..testing.board_gen import generate_all
            files = generate_all(folder)
            self.log(tr("Generadas: {f}", f=", ".join(files)))
            self.open_project(files[1])

    def learn_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, tr("Carpeta con proyectos KiCad enrutados"))
        if not folder:
            return
        out, _ = QFileDialog.getSaveFileName(self, tr("Guardar modelo"), "modelo_openrouteai.json", "JSON (*.json)")
        if not out:
            return
        from ..ai.learn import learn_priors, save_model, scan_projects
        ds = scan_projects(folder, progress=lambda m, p: self.statusBar().showMessage(m))
        pri = learn_priors(ds)
        save_model(out, ds, pri)
        self._apply_priors(pri)
        self.log(tr("Modelo aprendido de {n} placas: {p}", n=len(ds), p=pri))

    def load_model(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Modelo de priors"), "", "JSON (*.json)")
        if path:
            from ..ai.learn import load_priors
            pri = load_priors(path)
            self._apply_priors(pri)
            self.log(tr("Priors cargados: {p}", p=pri))

    def _apply_priors(self, pri):
        if "via_cost_mm" in pri:
            self.sp_via.setValue(float(pri["via_cost_mm"]))
        if "dir_penalty" in pri:
            self.sp_dir.setValue(float(pri["dir_penalty"]))

    def export_graph(self):
        if not self.project:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar grafo"), "grafo.json", "JSON (*.json)")
        if path:
            import json
            from ..ai.learn import export_graph
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(export_graph(self.project.board), fh)
            self.log(tr("Grafo exportado: {path}", path=path))

    def show_plugins(self):
        from ..plugins import load_plugins
        api = load_plugins()
        txt = tr("Cargados:") + " " + (", ".join(api.loaded) or tr("ninguno"))
        if api.errors:
            txt += "\n\n" + tr("Errores:") + "\n" + "\n".join(f"{n}: {e}" for n, e in api.errors)
        QMessageBox.information(self, "Plugins", txt)

    def closeEvent(self, ev):
        if self._thread is not None and not getattr(self, "_closing_for_language", False):
            QMessageBox.information(self, APP_NAME, tr("Espera a que termine el enrutado en curso."))
            ev.ignore()
            return
        super().closeEvent(ev)


def main(path: Optional[str] = None) -> int:
    from ..i18n import init_language
    from .branding import app_icon, set_windows_app_id, splash
    init_language()
    set_windows_app_id()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    sp = splash(app)
    from .theme import apply_theme
    apply_theme(load_config().get("theme", "light"), app)
    w = MainWindow()
    _WINDOWS.append(w)
    w.show()
    if sp is not None:
        sp.finish(w)
    if path:
        w.open_project(path)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
