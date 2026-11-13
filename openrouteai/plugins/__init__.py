"""Sistema de plugins.

Un plugin es un archivo ``.py`` en la carpeta ``plugins/`` del programa o en
``~/.openrouteai/plugins/`` que define ``register(api)``. Con ``api`` puede:

  api.add_drc_check(nombre, funcion(board) -> list[Violation])
  api.add_post_route(nombre, funcion(board, resultado))
  api.add_net_classifier(nombre, funcion(net, board) -> str | None)
  api.add_strategy(nombre, funcion(board, router_config) -> RouteResult)

Ver ``plugins/ejemplo_pistas_finas.py``.
"""
from __future__ import annotations

import importlib.util
import os
import traceback
from typing import Callable, Dict, List, Tuple


class PluginAPI:
    def __init__(self):
        self.drc_checks: Dict[str, Callable] = {}
        self.post_route: Dict[str, Callable] = {}
        self.net_classifiers: Dict[str, Callable] = {}
        self.strategies: Dict[str, Callable] = {}
        self.loaded: List[str] = []
        self.errors: List[Tuple[str, str]] = []

    def add_drc_check(self, name: str, fn: Callable):
        self.drc_checks[name] = fn

    def add_post_route(self, name: str, fn: Callable):
        self.post_route[name] = fn

    def add_net_classifier(self, name: str, fn: Callable):
        self.net_classifiers[name] = fn

    def add_strategy(self, name: str, fn: Callable):
        self.strategies[name] = fn


_API = None


def plugin_dirs() -> List[str]:
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return [os.path.join(here, "plugins"), os.path.join(os.path.expanduser("~"), ".openrouteai", "plugins")]


def load_plugins(force: bool = False) -> PluginAPI:
    global _API
    if _API is not None and not force:
        return _API
    api = PluginAPI()
    for d in plugin_dirs():
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py") or name.startswith("_"):
                continue
            path = os.path.join(d, name)
            try:
                spec = importlib.util.spec_from_file_location(f"openrouteai_plugin_{name[:-3]}", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(api)
                    api.loaded.append(name)
            except Exception:
                api.errors.append((name, traceback.format_exc(limit=2)))
    _API = api
    return api


def run_plugin_drc(board) -> list:
    out = []
    for name, fn in load_plugins().drc_checks.items():
        try:
            out += list(fn(board) or [])
        except Exception as exc:
            from ..drc.drc import Violation
            out.append(Violation("plugin", f"Plugin {name} falló: {exc}", severity="warning"))
    return out
