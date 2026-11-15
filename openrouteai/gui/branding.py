"""Logo e icono de OpenRouteAI (archivos en gui/resources)."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

RES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")


def resource(name: str) -> str:
    return os.path.join(RES, name)


def app_icon() -> QIcon:
    """Icono de la aplicación (ventana, barra de tareas, diálogos)."""
    ico = QIcon()
    png = resource("icon.png")
    if os.path.exists(png):
        base = QPixmap(png)
        for s in (16, 24, 32, 48, 64, 128, 256):
            ico.addPixmap(base.scaled(s, s, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return ico


def logo_pixmap(width: int = 0) -> QPixmap:
    """Logo con el nombre (fondo oscuro). ``width`` > 0 lo escala a ese ancho."""
    pm = QPixmap(resource("logo.png"))
    if width and not pm.isNull():
        pm = pm.scaledToWidth(width, Qt.SmoothTransformation)
    return pm


def set_windows_app_id() -> None:
    """En Windows, para que la barra de tareas muestre el icono propio y no el de Python."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenRouteAI.OpenRouteAI")
    except Exception:
        pass


def splash(app):
    """Pantalla de inicio con el logo mientras se construye la ventana (None si no hay logo)."""
    from PySide6.QtWidgets import QSplashScreen
    pm = logo_pixmap(620)
    if pm.isNull():
        return None
    sp = QSplashScreen(pm, Qt.WindowStaysOnTopHint)
    sp.show()
    app.processEvents()
    return sp
