"""Aceleración por GPU (NVIDIA CUDA mediante CuPy, opcional).

Qué se hace en la GPU:
  - se mantiene una copia del estado denso de la rejilla (campos de distancia,
    borde, zonas de exclusión, huellas del cobre enrutado e histórico)
  - se calculan en la GPU los campos de coste de cada ventana de búsqueda
    (``costfield.window_costs``) y solo se devuelve el resultado a la CPU

Qué NO se hace en la GPU: la búsqueda de caminos en sí (A*/Lee/Hadlock), que es
un algoritmo secuencial con cola de prioridad; el núcleo nativo en C de la CPU
es más rápido para eso que cualquier versión GPU por red.

En consecuencia la GPU aporta en placas grandes (muchos millones de celdas,
4+ capas o paso de rejilla fino) y no en placas pequeñas, donde la transferencia
por PCIe cuesta más de lo que se ahorra. El modo ``auto`` solo la usa a partir
de ``AUTO_MIN_CELLS``. ``python -m openrouteai bench`` mide ambas en tu equipo.

Instalación: ``install_gpu.bat`` (``pip install cupy-cuda12x`` o ``cupy-cuda13x``
según la versión de CUDA del driver). Solo GPUs NVIDIA.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

from ..i18n import tr
from .costfield import window_costs

AUTO_MIN_CELLS = 6_000_000
_cp = None
_err = ""


def cupy_module():
    """Devuelve el módulo cupy si hay una GPU CUDA utilizable, o None."""
    global _cp, _err
    if _cp is not None or _err:
        return _cp
    try:
        import cupy as cp
        n = cp.cuda.runtime.getDeviceCount()
        if n < 1:
            raise RuntimeError("no hay GPU CUDA")
        cp.zeros(1, dtype=cp.float32).sum().item()   # inicializa el contexto
        _cp = cp
    except Exception as exc:  # ImportError, CUDARuntimeError, driver ausente...
        _err = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
    return _cp


def available() -> bool:
    return cupy_module() is not None


def short_status() -> str:
    """Texto corto para la interfaz."""
    if available():
        return device_name()
    if "No module named 'cupy'" in _err:
        return tr("no instalada (falta CuPy)")
    return tr("no disponible (sin GPU NVIDIA o sin driver)")


def help_text() -> str:
    """Explicación de por qué no hay GPU y cómo activarla."""
    import sys
    if available():
        return tr("GPU disponible: {d}", d=device_name())
    pyv = f"{sys.version_info[0]}.{sys.version_info[1]}"
    lines = []
    if "No module named 'cupy'" in _err:
        lines.append(tr("La GPU necesita el paquete CuPy, que no está instalado en este entorno."))
        lines.append("")
        lines.append(tr("Requisitos: tarjeta NVIDIA con driver actualizado."))
        if sys.version_info[:2] > (3, 14):
            lines.append(tr("CuPy solo funciona con Python 3.10 a 3.14 y este entorno usa Python {v}: "
                            "install_gpu.bat recreará el entorno con Python 3.14 (instálalo antes desde python.org).",
                            v=pyv))
        lines.append("")
        lines.append(tr("Pasos: cierra OpenRouteAI, ejecuta install_gpu.bat y vuelve a abrir con run.bat."))
    else:
        lines.append(tr("CuPy está instalado pero no encuentra una GPU NVIDIA utilizable."))
        lines.append(tr("Detalle: {e}", e=_err))
        lines.append("")
        lines.append(tr("Comprueba que la tarjeta es NVIDIA y que el driver está instalado (nvidia-smi)."))
    lines.append("")
    lines.append(tr("Mientras tanto se usa la CPU. En placas como las de prueba la GPU no aporta velocidad; "
                    "solo compensa en placas grandes (millones de celdas)."))
    return "\n".join(lines)


def device_name() -> str:
    cp = cupy_module()
    if cp is None:
        return tr("no disponible") + f" ({_err})"
    try:
        props = cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)
        name = props.get("name", b"GPU")
        name = name.decode() if isinstance(name, bytes) else str(name)
        mem = props.get("totalGlobalMem", 0) / 2 ** 30
        return f"{name} ({mem:.1f} GB)"
    except Exception:
        return "GPU CUDA"


def resolve(device: str, n_cells: int) -> Tuple[str, Optional[object], str]:
    """Decide el dispositivo. Devuelve (nombre, módulo xp para GPU o None, motivo)."""
    device = (device or "cpu").lower()
    if device == "cpu":
        return "cpu", None, "elegido"
    if device == "sim":                     # pruebas: misma ruta de código con numpy
        return "sim", np, "simulación GPU con NumPy"
    cp = cupy_module()
    if cp is None:
        return "cpu", None, f"GPU no disponible: {_err}"
    if device == "auto" and n_cells < AUTO_MIN_CELLS:
        return "cpu", None, f"placa pequeña ({n_cells:,} celdas < {AUTO_MIN_CELLS:,})"
    return "gpu", cp, device_name()


class GpuGrid:
    """Espejo en el dispositivo del estado denso de ``RoutingGrid``."""

    def __init__(self, grid, xp):
        self.xp = xp
        self.g = grid
        a = lambda arr: xp.array(arr, copy=True)   # copia real (también en la simulación con numpy)
        self.d1 = a(grid.d1)
        self.d2 = a(grid.d2)
        self.o1 = a(grid.o1)
        self.dedge = a(grid.dedge)
        self.keep_t = a(grid.keepout_track)
        self.keep_v = a(grid.keepout_via)
        self.hist = a(grid.hist)
        self.stamp: Dict[float, object] = {Q: a(arr) for Q, arr in grid.stamp.items()}
        self.blocked = [int(i) for i in np.flatnonzero(grid.reserved)]

    def nbytes(self) -> int:
        arrs = [self.d1, self.d2, self.o1, self.dedge, self.keep_t, self.keep_v, self.hist] + list(self.stamp.values())
        return int(sum(x.nbytes for x in arrs))

    def add_stamps(self, stamps, sign: int):
        xp = self.xp
        for Q, idx in stamps.items():
            if len(idx):
                flat = self.stamp[Q].reshape(-1)
                flat[xp.asarray(idx)] += xp.int16(sign)

    def sync_hist(self):
        self.hist = self.xp.array(self.g.hist, copy=True)

    def window(self, sl, *, nid, hw, rv, c, ec, slack, extra_layers, pres, via_cost_cells,
               reach_t, reach_v):
        xp = self.xp
        ys, xs = sl[1], sl[2]
        cost, vcost = window_costs(
            xp, self.d1[sl], self.d2[sl], self.o1[sl], self.dedge[ys, xs], self.keep_t[sl],
            self.keep_v[ys, xs], self.stamp[reach_t][sl], self.stamp[reach_v][sl], self.hist[sl],
            nid=nid, hw=hw, rv=rv, c=c, ec=ec, slack=slack,
            blocked_layers=self.blocked + list(extra_layers), pres=pres, via_cost_cells=via_cost_cells)
        if xp is np:
            return cost, vcost
        return xp.asnumpy(cost), xp.asnumpy(vcost)
