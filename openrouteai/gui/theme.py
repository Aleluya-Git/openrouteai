"""Tema claro / oscuro de la interfaz (la vista de la placa conserva su fondo oscuro)."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEMES = ("dark", "light")


def dark_palette() -> QPalette:
    p = QPalette()
    base, alt, text = QColor(30, 32, 36), QColor(42, 45, 50), QColor(225, 228, 232)
    win = QColor(37, 39, 44)
    p.setColor(QPalette.Window, win)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, alt)
    p.setColor(QPalette.ToolTipBase, QColor(50, 53, 60))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor(140, 145, 150))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, alt)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(255, 90, 90))
    p.setColor(QPalette.Link, QColor(90, 170, 255))
    p.setColor(QPalette.Highlight, QColor(46, 120, 210))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Light, QColor(70, 74, 80))
    p.setColor(QPalette.Midlight, QColor(55, 58, 64))
    p.setColor(QPalette.Mid, QColor(45, 48, 53))
    p.setColor(QPalette.Dark, QColor(22, 23, 26))
    p.setColor(QPalette.Shadow, QColor(0, 0, 0))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(115, 118, 124))
    return p


def apply_theme(mode: str, app=None) -> str:
    app = app or QApplication.instance()
    mode = mode if mode in THEMES else "light"
    from . import icons
    icons.set_dark(mode == "dark")
    if app is None:
        return mode
    app.setStyle("Fusion")
    if mode == "dark":
        app.setPalette(dark_palette())
    else:
        app.setPalette(app.style().standardPalette())
    return mode
