"""Carga del núcleo nativo de búsqueda (C) mediante ctypes.

Orden de búsqueda de la librería:
  1. binario precompilado incluido en ``routing/_native/`` para esta plataforma
     (Windows x64 .dll, Linux x86_64 .so)
  2. copia compilada en caché en ``~/.openrouteai/native/``
  3. compilación automática con el compilador de C del sistema (cc/clang/gcc),
     típico en macOS (Xcode CLT) y Linux
Si nada funciona se usa la implementación en Python puro (``search.py``), más lenta.

Variable de entorno ``OPENROUTEAI_NO_NATIVE=1`` fuerza el modo Python.
"""
from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
from typing import List, Optional, Sequence

import numpy as np

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_native")
SRC = os.path.join(HERE, "ora_search.c")
MODES = {"astar": 0, "maze": 1, "lee": 2, "hadlock": 3}

_lib = None
_load_error = ""


def _platform_tag() -> str:
    m = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "win_amd64" if m in ("amd64", "x86_64") else f"win_{m}"
    if sys.platform == "darwin":
        return f"macos_{m}"
    return f"linux_{'x86_64' if m in ('x86_64', 'amd64') else m}"


def _ext() -> str:
    return ".dll" if sys.platform.startswith("win") else (".dylib" if sys.platform == "darwin" else ".so")


def _try_load(path: str):
    lib = ctypes.CDLL(path)
    if lib.ora_version() != 2:
        raise OSError("versión de librería incompatible")
    f = lib.ora_search
    P = ctypes.c_void_p
    f.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                  P, P, P, P, ctypes.c_int,
                  P, ctypes.c_float, ctypes.c_int, ctypes.c_longlong, ctypes.c_float,
                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                  P, P, P, P, P, P, ctypes.c_int, P,
                  P, ctypes.c_int, P]
    f.restype = ctypes.c_int
    return lib


def _compile(dst: str) -> Optional[str]:
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc or not os.path.exists(SRC):
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    args = [cc, "-O3", "-shared", "-fPIC", "-fvisibility=hidden", "-o", dst, SRC]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    return dst if r.returncode == 0 and os.path.exists(dst) else None


def load():
    """Devuelve la librería nativa o None."""
    global _lib, _load_error
    if _lib is not None or _load_error:
        return _lib
    if os.environ.get("OPENROUTEAI_NO_NATIVE"):
        _load_error = "desactivado por OPENROUTEAI_NO_NATIVE"
        return None
    tag = _platform_tag()
    cands = [os.path.join(HERE, f"ora_search_{tag}{_ext()}"),
             os.path.join(os.path.expanduser("~"), ".openrouteai", "native", f"ora_search_{tag}{_ext()}")]
    errors = []
    for c in cands:
        if os.path.exists(c):
            try:
                _lib = _try_load(c)
                return _lib
            except OSError as exc:
                errors.append(f"{os.path.basename(c)}: {exc}")
    built = _compile(cands[1])
    if built:
        try:
            _lib = _try_load(built)
            return _lib
        except OSError as exc:
            errors.append(f"compilada: {exc}")
    _load_error = "; ".join(errors) or "no hay binario para esta plataforma ni compilador de C"
    return None


def available() -> bool:
    return load() is not None


def status() -> str:
    from ..i18n import tr
    return tr("nativo (C)") if available() else tr("Python puro ({e})", e=_load_error)


class NativeSearcher:
    """Mantiene los búferes de trabajo entre búsquedas (se reutilizan y crecen)."""

    def __init__(self):
        self.lib = load()
        self.cap = 0
        self.curgen = 0
        self.expansions = ctypes.c_longlong(0)

    def _ensure(self, n: int):
        if n <= self.cap:
            return
        cap = int(n * 1.25) + 1024
        self.g = np.empty(cap, dtype=np.float32)
        self.key = np.empty(cap, dtype=np.float32)
        self.parent = np.empty(cap, dtype=np.int32)
        self.pos = np.empty(cap, dtype=np.int32)
        self.heap = np.empty(cap, dtype=np.int32)
        self.gen = np.zeros(cap, dtype=np.int32)
        self.pdir = np.empty(cap, dtype=np.uint8)
        self.out = np.empty(cap, dtype=np.int32)
        self.cap = cap
        self.curgen = 0

    def search(self, W: int, H: int, L: int, cost: np.ndarray, via_cost: np.ndarray,
               sources: Sequence[int], target_mask: np.ndarray, mode: str,
               layer_mult: np.ndarray, turn_penalty: float, tbox, max_exp: int = 50_000_000,
               heur_weight: float = 1.0) -> Optional[List[int]]:
        n = W * H * L
        self._ensure(n)
        self.curgen += 1
        if self.curgen >= 2_000_000_000:
            self.gen[:] = 0
            self.curgen = 1
        cost = np.ascontiguousarray(cost, dtype=np.float32).reshape(-1)
        via_cost = np.ascontiguousarray(via_cost, dtype=np.float32).reshape(-1)
        tm = np.ascontiguousarray(target_mask, dtype=np.uint8).reshape(-1)
        src = np.ascontiguousarray(np.asarray(list(sources), dtype=np.int32))
        lm = np.ascontiguousarray(layer_mult, dtype=np.float32).reshape(-1)
        tx0, tx1, ty0, ty1 = tbox
        P = lambda a: a.ctypes.data_as(ctypes.c_void_p)
        r = self.lib.ora_search(W, H, L, P(cost), P(via_cost), P(tm), P(src), len(src),
                                P(lm), float(turn_penalty), MODES.get(mode, 0), int(max_exp), float(heur_weight),
                                int(tx0), int(tx1), int(ty0), int(ty1),
                                P(self.g), P(self.key), P(self.parent), P(self.pos), P(self.heap),
                                P(self.gen), self.curgen, P(self.pdir),
                                P(self.out), self.cap, ctypes.byref(self.expansions))
        if r <= 0:
            return None
        return self.out[:r].tolist()
