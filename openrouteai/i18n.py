"""Traducción de la interfaz (español, inglés, alemán y francés) y configuración del usuario.

Las cadenas del código están en español y sirven de clave; ``translations.py``
contiene las versiones en inglés, alemán y francés. Las cadenas con datos usan
marcadores con nombre: ``tr("{n} huellas movidas", n=3)``.

La configuración se guarda en ``~/.openrouteai/config.json`` (idioma, listas de
anchos de pista y tamaños de vía).
"""
from __future__ import annotations

import json
import locale
import os
from typing import Any, Dict

LANGUAGES = {"es": "Español", "en": "English", "de": "Deutsch", "fr": "Français"}
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".openrouteai", "config.json")
_lang = "es"


def load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(**values) -> None:
    cfg = load_config()
    cfg.update(values)
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass


def system_language() -> str:
    try:
        code = (locale.getlocale()[0] or os.environ.get("LANG", "") or "").lower()
    except Exception:
        code = ""
    for k in LANGUAGES:
        if code.startswith(k):
            return k
    return "es"


def set_language(code: str) -> None:
    global _lang
    _lang = code if code in LANGUAGES else "es"


def language() -> str:
    return _lang


def init_language() -> str:
    """Idioma guardado, o el del sistema si no hay ninguno."""
    set_language(load_config().get("language") or system_language())
    return _lang


def tr(text: str, **kw) -> str:
    if _lang != "es":
        from .translations import TRANSLATIONS
        text = TRANSLATIONS.get(_lang, {}).get(text, text)
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text
