"""Banco de pruebas de velocidad: CPU frente a GPU y núcleo nativo frente a Python."""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import replace
from typing import Callable, List, Optional

import numpy as np


def _route_once(path: str, cfg):
    from . import pipeline
    pr = pipeline.open_project(path)
    t = time.time()
    res = pipeline.route(pr, cfg)
    el = time.time() - t
    rep = pipeline.drc(pr)
    errs = rep.count() - rep.count("diff_skew") - rep.count("fabricacion")
    return el, res, errs


def _window_bench(path: str, xp_device: str, n: int = 10) -> Optional[float]:
    """Tiempo medio (ms) de calcular los costes de una ventana del tamaño de la placa."""
    from .kicad.pcb_import import load_board
    from .routing.router import Router, RouterConfig
    b = load_board(path)
    r = Router(b, RouterConfig(device=xp_device, max_iterations=1, strategy="astar", nets=set()))
    r.run()                                   # construye rejilla y estado (sin enrutar)
    g = r.grid
    name = next(iter(b.nets))
    r._window_arrays(name, (0, g.ny - 1, 0, g.nx - 1), 0, 1.0)   # calentamiento
    t = time.time()
    for _ in range(n):
        r._window_arrays(name, (0, g.ny - 1, 0, g.nx - 1), 0, 1.0)
    return (time.time() - t) / n * 1000, g.L * g.ny * g.nx


def run(path: Optional[str] = None, pitch: Optional[float] = None,
        log: Callable[[str], None] = print, python_too: bool = False) -> List[dict]:
    from .routing import gpu, native
    from .routing.router import RouterConfig
    log(f"Núcleo de búsqueda: {native.status()}")
    log(f"GPU: {gpu.device_name()}")
    if path is None:
        from .testing.board_gen import board_dense
        d = tempfile.mkdtemp(prefix="openrouteai_bench_")
        path = os.path.join(d, "bench_4capas.kicad_pcb")
        board_dense(path, seed=11, n_comp=96, n_nets=120, cols=12, n_copper=4)
        log(f"Placa de prueba generada: 96 componentes, 120 redes, 4 capas ({path})")
    base = RouterConfig(pitch=pitch, time_limit=1800)
    rows = []
    combos = [("cpu", "auto")]
    if gpu.available():
        combos.append(("gpu", "auto"))
    if python_too:
        combos.append(("cpu", "python"))
    for dev, eng in combos:
        try:
            ms, cells = _window_bench(path, dev)
            log(f"  Costes de ventana completa ({cells:,} celdas) en {dev.upper()}: {ms:.1f} ms")
        except Exception as exc:
            ms = None
            log(f"  Costes de ventana en {dev.upper()}: error {exc}")
        el, res, errs = _route_once(path, replace(base, device=dev, engine=eng))
        m = res.metrics()
        rows.append({"dispositivo": dev, "motor": eng, "tiempo_s": round(el, 2), "ventana_ms": ms,
                     "completado": m["completado_%"], "vias": m["vias"], "drc_errores": errs})
        log(f"  Enrutado completo [{dev.upper()}, motor {res.grid_info.get('engine')}]: {el:.2f} s — "
            f"{m['completado_%']}% {m['conexiones']} — {m['vias']} vías — DRC errores: {errs}")
    if len(rows) > 1 and rows[1]["dispositivo"] == "gpu":
        a, b = rows[0]["tiempo_s"], rows[1]["tiempo_s"]
        log(f"Aceleración GPU en el enrutado completo: x{a / max(b, 1e-9):.2f}"
            + ("" if b < a else "  (en esta placa la GPU no compensa: usa CPU o 'auto')"))
    return rows
