"""Interfaz de línea de comandos de OpenRouteAI.

Ejemplos:
  python -m openrouteai route mi_proyecto.kicad_pro -o enrutado.kicad_pcb
  python -m openrouteai route placa.kicad_pcb --strategy ripup --planes B.Cu=GND --png vista.png
  python -m openrouteai drc placa.kicad_pcb
  python -m openrouteai analyze mi_proyecto/
  python -m openrouteai learn carpeta_con_proyectos/ -o modelo.json
  python -m openrouteai gui
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .routing.router import STRATEGIES, RouterConfig


def _progress(msg, frac):
    print(f"[{frac * 100:5.1f}%] {msg}", flush=True)


def cmd_route(a):
    from . import pipeline
    pr = pipeline.open_project(a.input)
    for n in pr.notes[:10]:
        print("  ·", n)
    cfg = RouterConfig(strategy=a.strategy, pitch=a.pitch, max_iterations=a.iterations,
                       keep_existing=not a.discard_existing, time_limit=a.time_limit, progress=_progress,
                       stitch_pitch=a.stitch, device=a.device, engine=a.engine,
                       cleanup=not a.no_cleanup, via_min=a.via_min, via_max=a.via_max)
    for nw in a.net_width or []:
        net, w = nw.rsplit("=", 1)
        cfg.net_widths[net] = float(w)
    if a.nets:
        cfg.nets = set(a.nets.split(","))
    for pl in a.planes or []:
        layer, net = pl.split("=", 1)
        cfg.planes[layer] = net
    if a.model:
        from .ai.learn import apply_priors, load_priors
        apply_priors(cfg, load_priors(a.model))
    placed = False
    if a.place:
        from .placement.placer import Placer, PlacerConfig
        st = Placer(pr.board, PlacerConfig(iterations=a.place_iterations, progress=_progress)).run()
        print("Colocación:", st)
        placed = True
        from .analysis.netlist_analyzer import analyze
        pr.analysis = analyze(pr.board)
    if a.optimize:
        from .optimization.optimizer import OptConfig, Optimizer
        best, res = Optimizer(pr.board, cfg, OptConfig(method=a.optimize, progress=_progress)).run()
        print("Mejores parámetros:", {k: round(v, 3) for k, v in best.items() if k != "order"})
    else:
        from . import pipeline as P
        res = P.route(pr, cfg)
    rep = pipeline.drc(pr)
    out = a.output or os.path.splitext(pr.pcb_path)[0] + "_enrutado.kicad_pcb"
    pipeline.save(pr, res, out, keep_existing=not a.discard_existing, placement_changed=placed)
    m = res.metrics()
    print("\nResultado:", json.dumps(m, ensure_ascii=False))
    if res.failed_nets:
        print("Redes sin completar:", ", ".join(res.failed_nets))
    print("DRC:", rep.summary() or "sin errores")
    print("Guardado en:", out)
    if a.png:
        from .core.render import render_png
        render_png(pr.board, a.png, f"{os.path.basename(out)} - {m['completado_%']}%")
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump({"metricas": m, "fallidas": res.failed_nets, "drc": rep.summary(),
                       "violaciones": [v.message for v in rep.violations], "log": res.log}, fh,
                      indent=2, ensure_ascii=False)
    return 0 if not res.failed_nets else 2


def cmd_drc(a):
    from . import pipeline
    pr = pipeline.open_project(a.input)
    rep = pipeline.drc(pr)
    print("DRC:", rep.summary() or "sin errores")
    for v in rep.violations[: a.max]:
        print(f"  [{v.kind}] {v.message} @ ({v.x:.2f}, {v.y:.2f}) {v.layer}")
    return 1 if rep.count() else 0


def cmd_analyze(a):
    from . import pipeline
    pr = pipeline.open_project(a.input)
    an = pr.analysis
    print(json.dumps(an.stats, ensure_ascii=False, indent=2))
    print("Pares diferenciales:", an.diff_pairs)
    print("Clases de red:", {n: c.track_width for n, c in pr.board.netclasses.items()})
    print("Orden de enrutado:", ", ".join(an.order))
    for n in pr.notes:
        print("  ·", n)
    return 0


def cmd_learn(a):
    from .ai.learn import learn_priors, save_model, scan_projects
    ds = scan_projects(a.folder, progress=_progress)
    pri = learn_priors(ds)
    save_model(a.output, ds, pri)
    print("Placas analizadas:", len(ds))
    print("Priors aprendidos:", pri)
    return 0


def cmd_graph(a):
    from .ai.learn import export_graph
    from .kicad.pcb_import import load_board
    g = export_graph(load_board(a.input))
    with open(a.output, "w", encoding="utf-8") as fh:
        json.dump(g, fh)
    print(f"Grafo: {len(g['nodes'])} nodos, {len(g['edges'])} aristas -> {a.output}")
    return 0


def cmd_gen(a):
    from .testing.board_gen import generate_all
    for p in generate_all(a.folder):
        print(p)
    return 0


def cmd_bench(a):
    from .bench import run
    run(a.input, pitch=a.pitch, python_too=a.python)
    return 0


def cmd_info(a):
    from . import __version__
    from .routing import gpu, native
    print(f"OpenRouteAI {__version__}")
    print("Núcleo de búsqueda:", native.status())
    print("GPU:", gpu.short_status())
    if not gpu.available():
        print()
        print(gpu.help_text())
    return 0


def cmd_gui(a):
    from .gui.main_window import main
    return main(a.input)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="openrouteai", description=f"OpenRouteAI {__version__} - autorouter para KiCad")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("route", help="enrutar un proyecto")
    r.add_argument("input", help=".kicad_pro, .kicad_pcb o carpeta del proyecto")
    r.add_argument("-o", "--output")
    r.add_argument("-s", "--strategy", choices=STRATEGIES, default="negotiated")
    r.add_argument("--pitch", type=float, default=None, help="paso de rejilla en mm (auto por defecto)")
    r.add_argument("--iterations", type=int, default=25)
    r.add_argument("--time-limit", type=float, default=900)
    r.add_argument("--planes", action="append", help="plano: CAPA=RED, p. ej. B.Cu=GND (repetible)")
    r.add_argument("--stitch", type=float, default=0.0, help="cosido de vías cada N mm (requiere 2 planos)")
    r.add_argument("--nets", help="enrutar solo estas redes (separadas por comas)")
    r.add_argument("--discard-existing", action="store_true", help="borrar pistas existentes antes de enrutar")
    r.add_argument("--place", action="store_true", help="colocar componentes automáticamente antes")
    r.add_argument("--place-iterations", type=int, default=20000)
    r.add_argument("--optimize", choices=["genetic", "pso"])
    r.add_argument("--model", help="modelo de priors aprendido (openrouteai learn)")
    r.add_argument("--device", choices=["cpu", "gpu", "auto"], default="auto",
                   help="dónde calcular la rejilla de costes (auto: GPU solo en placas grandes)")
    r.add_argument("--engine", choices=["auto", "native", "python"], default="auto",
                   help="núcleo de búsqueda: nativo en C (rápido) o Python")
    r.add_argument("--no-cleanup", action="store_true", help="no depurar las pistas tras enrutar")
    r.add_argument("--via-min", type=float, default=0.0, help="diámetro de vía mínimo (mm)")
    r.add_argument("--via-max", type=float, default=0.0, help="diámetro de vía máximo (mm)")
    r.add_argument("--net-width", action="append", help="ancho de pista para una red: RED=ANCHO (repetible)")
    r.add_argument("--png")
    r.add_argument("--report")
    r.set_defaults(fn=cmd_route)

    d = sub.add_parser("drc", help="comprobar reglas de diseño")
    d.add_argument("input")
    d.add_argument("--max", type=int, default=50)
    d.set_defaults(fn=cmd_drc)

    an = sub.add_parser("analyze", help="analizar netlist/redes")
    an.add_argument("input")
    an.set_defaults(fn=cmd_analyze)

    le = sub.add_parser("learn", help="aprender de proyectos KiCad existentes")
    le.add_argument("folder")
    le.add_argument("-o", "--output", default="modelo_openrouteai.json")
    le.set_defaults(fn=cmd_learn)

    gr = sub.add_parser("graph", help="exportar la placa como grafo JSON (para GNN)")
    gr.add_argument("input")
    gr.add_argument("-o", "--output", default="grafo.json")
    gr.set_defaults(fn=cmd_graph)

    ge = sub.add_parser("gen-tests", help="generar placas de prueba")
    ge.add_argument("folder")
    ge.set_defaults(fn=cmd_gen)

    be = sub.add_parser("bench", help="medir velocidad CPU/GPU en este equipo")
    be.add_argument("input", nargs="?", help="proyecto a medir (por defecto una placa generada de 4 capas)")
    be.add_argument("--pitch", type=float, default=None)
    be.add_argument("--python", action="store_true", help="medir también el motor Python puro (lento)")
    be.set_defaults(fn=cmd_bench)

    inf = sub.add_parser("info", help="mostrar núcleo de búsqueda y GPU disponibles")
    inf.set_defaults(fn=cmd_info)

    gu = sub.add_parser("gui", help="abrir la interfaz gráfica")
    gu.add_argument("input", nargs="?")
    gu.set_defaults(fn=cmd_gui)

    a = ap.parse_args(argv)
    if not a.cmd:
        return cmd_gui(argparse.Namespace(input=None))
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
