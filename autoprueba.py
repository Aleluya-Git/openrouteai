"""Autoprueba de OpenRouteAI.

Ejecuta una batería completa de pruebas y escribe ``autoprueba_resultado.txt``
más imágenes PNG en ``autoprueba_salida/``. Si encuentra ``kicad-cli`` (KiCad
8/9/10), valida además cada placa enrutada con el DRC oficial de KiCad.

Si colocas proyectos tuyos (carpetas con .kicad_pro/.kicad_pcb) dentro de
``mis_proyectos/`` también se enrutan y se comprueban.

Uso:  python autoprueba.py [--rapida]
"""
from __future__ import annotations

import glob
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "autoprueba_salida")
REPORT = os.path.join(HERE, "autoprueba_resultado.txt")

lines = []
results = {"ok": 0, "fallo": 0, "aviso": 0}


def log(msg=""):
    print(msg, flush=True)
    lines.append(msg)


def check(name, cond, detail=""):
    tag = "OK   " if cond else "FALLO"
    results["ok" if cond else "fallo"] += 1
    log(f"[{tag}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def warn(name, detail=""):
    results["aviso"] += 1
    log(f"[AVISO] {name}" + (f" — {detail}" if detail else ""))


def section(t):
    log("")
    log("=" * 78)
    log(t)
    log("=" * 78)


# ----------------------------------------------------------------- kicad-cli
COPPER_TYPES = {"clearance", "shorting_items", "tracks_crossing", "copper_edge_clearance", "hole_clearance",
                "track_width", "via_diameter", "annular_width", "drill_out_of_range", "hole_to_hole",
                "holes_co_located", "track_dangling", "via_dangling", "connection_width", "items_not_allowed"}


def find_kicad_cli():
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    cands = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            cands += glob.glob(os.path.join(base, "KiCad", "*", "bin", "kicad-cli.exe"))
        cands += glob.glob(os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs", "KiCad", "*", "bin", "kicad-cli.exe"))
    elif sys.platform == "darwin":
        cands += glob.glob("/Applications/KiCad*/KiCad.app/Contents/MacOS/kicad-cli")
    else:
        cands += ["/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli"]
    cands = [c for c in cands if os.path.exists(c)]
    return sorted(cands)[-1] if cands else None


def kicad_drc(cli, pcb):
    rep = os.path.splitext(pcb)[0] + "_drc_kicad.json"
    base = [cli, "pcb", "drc", "--format", "json", "--severity-all", "-o", rep]
    for extra in (["--refill-zones"], []):
        try:
            p = subprocess.run(base + extra + [pcb], capture_output=True, text=True, timeout=300)
        except Exception as exc:
            return None, str(exc)
        if os.path.exists(rep):
            break
    if not os.path.exists(rep):
        return None, (p.stderr or p.stdout)[-400:]
    with open(rep, encoding="utf-8") as fh:
        data = json.load(fh)
    counts = {}
    for v in data.get("violations", []):
        counts[v.get("type", "?")] = counts.get(v.get("type", "?"), 0) + 1
    counts["unconnected_items"] = len(data.get("unconnected_items", []))
    return counts, data.get("kicad_version", "")


# ----------------------------------------------------------------- pruebas
def main():
    rapida = "--rapida" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    t_start = time.time()
    section("OpenRouteAI — AUTOPRUEBA")
    log(f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Sistema: {platform.platform()}  Python {sys.version.split()[0]}  CPU: {os.cpu_count()} hilos")

    section("1. Dependencias")
    try:
        import numpy, scipy
        check("numpy / scipy", True, f"numpy {numpy.__version__}, scipy {scipy.__version__}")
    except Exception as exc:
        check("numpy / scipy", False, str(exc))
        return finish(t_start)
    try:
        import matplotlib
        check("matplotlib (imágenes de la autoprueba)", True, matplotlib.__version__)
    except Exception:
        warn("matplotlib no instalado", "no se generarán PNG")
    gui_ok = False
    try:
        import PySide6
        gui_ok = check("PySide6 (interfaz gráfica)", True, PySide6.__version__)
    except Exception as exc:
        check("PySide6 (interfaz gráfica)", False, str(exc))
    import openrouteai
    log(f"OpenRouteAI {openrouteai.__version__}")

    section("2. Núcleo: S-expressions, geometría y modelo")
    from openrouteai.core import sexpr as S
    from openrouteai.core.geometry import poly_dist, rect_poly, rotate
    src = '(kicad_pcb (version 20240108) (net 1 "GND") (footprint "A:B" (at 1.5 2 90) (pad "1" smd rect (at 0 0) (size 1 1))))'
    tree = S.parse(src)
    check("Parser S-expr ida y vuelta", S.parse(S.dumps(tree)) == tree)
    check("Cadenas con escapes", S.parse('(a "x \\"y\\" z")')[1] == 'x "y" z')
    x, y = rotate(1, 0, 90)
    check("Rotación KiCad (90° antihorario, Y abajo)", abs(x) < 1e-9 and abs(y + 1) < 1e-9, f"({x:.3f}, {y:.3f})")
    d = poly_dist(rect_poly(0, 0, 2, 2, 0), rect_poly(5, 0, 2, 2, 0))
    check("Distancia entre polígonos", abs(d - 3) < 1e-9, f"{d}")

    section("3. Generación e importación de placas de prueba")
    from openrouteai.testing.board_gen import generate_all
    from openrouteai.kicad.pcb_import import load_board
    from openrouteai.analysis.netlist_analyzer import analyze
    files = generate_all(os.path.join(OUT, "placas"))
    boards = {}
    for f in files:
        try:
            b = load_board(f)
            an = analyze(b)
            boards[f] = b
            check(f"Importar {os.path.basename(f)}", len(b.footprints) > 0 and len(b.routable_nets()) > 0,
                  f"{len(b.footprints)} huellas, {an.stats['redes_enrutables']} redes, "
                  f"{an.stats['conexiones']} conexiones, capas {len(b.copper_layers)}, "
                  f"clases {sorted(b.netclasses)}")
        except Exception:
            check(f"Importar {os.path.basename(f)}", False, traceback.format_exc(limit=3))
    mcu = os.path.join(OUT, "placas", "prueba_mcu.kicad_pcb")
    b = load_board(mcu)
    an = analyze(b)
    check("Detección de par diferencial USB_D+/USB_D-", ("USB_D+", "USB_D-") in an.diff_pairs, str(an.diff_pairs))
    check("Clase de red por patrón (GND → Power)", b.nets["GND"].netclass == "Power")
    check("Clasificación masa/alimentación", b.nets["GND"].kind == "ground" and b.nets["+3V3"].kind == "power")

    section("4. Estrategias de enrutado (placa simple)")
    from openrouteai.routing.router import STRATEGIES, Router, RouterConfig
    from openrouteai.drc.drc import run_drc
    from openrouteai.kicad.pcb_export import export_board
    from openrouteai.core.render import render_png
    simple = os.path.join(OUT, "placas", "prueba_simple.kicad_pcb")
    table = []
    for strat in STRATEGIES:
        try:
            b = load_board(simple)
            r = Router(b, RouterConfig(strategy=strat, time_limit=300)).run()
            rep = run_drc(b)
            m = r.metrics()
            errs = rep.count() - rep.count("diff_skew")
            table.append((strat, m))
            check(f"Estrategia {strat}", r.completion >= 99.9 and errs == 0,
                  f"{m['completado_%']}%  {m['longitud_total_mm']} mm  {m['vias']} vías  {m['tiempo_s']} s  DRC={rep.summary() or 'ok'}")
            render_png(b, os.path.join(OUT, f"simple_{strat}.png"), f"{strat}: {m['completado_%']}%")
        except Exception:
            check(f"Estrategia {strat}", False, traceback.format_exc(limit=4))

    section("4b. Rendimiento: núcleo nativo (C) y GPU")
    from openrouteai.routing import native as _native, gpu as _gpu
    from openrouteai.routing.selftest import compare
    nat = _native.available()
    check("Núcleo nativo de búsqueda cargado", nat, _native.status())
    if nat:
        ok_n, tot_n, det = compare(40)
        check("Núcleo nativo = Python (coste óptimo en 40 rejillas aleatorias)", ok_n == tot_n, f"{ok_n}/{tot_n}, {det}")
    dense = os.path.join(OUT, "placas", "prueba_mcu.kicad_pcb")   # pequeña: el motor Python es lento
    tiempos = {}
    for eng in (["auto", "python"] if nat else ["python"]):
        b = load_board(dense)
        t = time.time()
        r = Router(b, RouterConfig(engine=eng, time_limit=600)).run()
        tiempos[eng] = time.time() - t
        rep = run_drc(b)
        check(f"Placa MCU con motor {r.grid_info['engine']}", r.completion >= 99.9 and rep.count() == rep.count("diff_skew"),
              f"{tiempos[eng]:.2f} s  {r.metrics()['completado_%']}%  DRC={rep.summary() or 'ok'}")
    if "python" in tiempos and "auto" in tiempos:
        log(f"         aceleración del núcleo nativo: x{tiempos['python'] / max(tiempos['auto'], 1e-9):.1f}")
    # misma ruta de código que la GPU, ejecutada con NumPy: debe dar el mismo resultado que la CPU
    res_dev = {}
    for dev in ("cpu", "sim"):
        b = load_board(os.path.join(OUT, "placas", "prueba_mcu_4capas.kicad_pcb"))
        r = Router(b, RouterConfig(device=dev, planes={"In1.Cu": "GND"})).run()
        res_dev[dev] = (r.metrics()["longitud_total_mm"], r.via_count, r.completion)
    check("Ruta de código GPU (simulada con NumPy) = CPU", res_dev["cpu"] == res_dev["sim"], f"{res_dev}")
    if _gpu.available():
        log(f"GPU detectada: {_gpu.device_name()}")
        for name in ("prueba_densa", "prueba_mcu_4capas"):
            out = {}
            for dev in ("cpu", "gpu"):
                b = load_board(os.path.join(OUT, "placas", name + ".kicad_pcb"))
                t = time.time()
                r = Router(b, RouterConfig(device=dev)).run()
                el = time.time() - t
                rep = run_drc(b)
                out[dev] = (round(r.total_length, 2), r.via_count, r.completion, rep.count() - rep.count("diff_skew"), el)
            same = out["cpu"][:4] == out["gpu"][:4]
            check(f"GPU real en {name}: mismo resultado que CPU", same and out["gpu"][3] == 0,
                  f"CPU {out['cpu'][4]:.2f} s, GPU {out['gpu'][4]:.2f} s, {out['gpu'][:4]}")
    else:
        warn("GPU no disponible", _gpu.device_name() + " — opcional: install_gpu.bat")

    section("4c. Depuración de pistas, tamaños de pista/vía y movimiento de huellas")
    for name in ("prueba_mcu", "prueba_densa"):
        res_cl = {}
        for cl in (False, True):
            b = load_board(os.path.join(OUT, "placas", name + ".kicad_pcb"))
            r = Router(b, RouterConfig(cleanup=cl)).run()
            rep = run_drc(b)
            res_cl[cl] = (r.metrics()["codos"], r.total_length, r.completion, rep.count() - rep.count("diff_skew"))
        (b0, l0, c0, e0), (b1, l1, c1, e1) = res_cl[False], res_cl[True]
        check(f"Depurar pistas en {name}", b1 < b0 and l1 <= l0 + 1e-6 and c1 >= 99.9 and e1 == 0,
              f"codos {b0} → {b1}, longitud {l0:.1f} → {l1:.1f} mm, DRC errores {e1}")
    b = load_board(os.path.join(OUT, "placas", "prueba_mcu.kicad_pcb"))
    r = Router(b, RouterConfig(via_min=0.7, via_max=0.8, net_widths={"PA0": 0.4})).run()
    rep = run_drc(b)
    vd = sorted({v.diameter for v in r.vias})
    wpa0 = sorted({t.width for t in r.tracks if t.net == "PA0"})
    check("Vía Ø mín./máx. y ancho de pista por red", vd == [0.7] and wpa0 == [0.4] and r.completion >= 99.9
          and rep.count() == rep.count("diff_skew"), f"vías {vd}, PA0 {wpa0}, DRC={rep.summary() or 'ok'}")
    b = load_board(os.path.join(OUT, "placas", "prueba_simple.kicad_pcb"))
    fp = next(f for f in b.footprints if f.ref == "R3")
    fp.set_pose(fp.x, fp.y + 3.0, fp.angle + 90)
    r = Router(b, RouterConfig()).run()
    out = os.path.join(OUT, "prueba_simple_movida.kicad_pcb")
    export_board(b, out, r.tracks, r.vias, r.zones, placement_changed=True)
    b2 = load_board(out)
    f2 = next(f for f in b2.footprints if f.ref == "R3")
    rep2 = run_drc(b2)
    check("Mover/girar huella, enrutar, guardar y reabrir",
          abs(f2.y - fp.y) < 1e-6 and abs(f2.angle - fp.angle) < 1e-6 and rep2.count() == 0,
          f"R3 en ({f2.x:.2f}, {f2.y:.2f}) {f2.angle:g}°, DRC={rep2.summary() or 'ok'}")

    section("4d. Edición: mover grupos de huellas con sus pistas y arrastrar pistas/vías")
    import copy as _copy
    import random as _random
    from openrouteai import editing as E
    from openrouteai.drc.drc import unconnected
    for name in ("prueba_mcu", "prueba_densa"):
        b = load_board(os.path.join(OUT, "placas", name + ".kicad_pcb"))
        Router(b, RouterConfig()).run()
        T0 = [_copy.copy(t) for t in b.tracks]
        V0 = [_copy.copy(v) for v in b.vias]
        F0 = [(f.x, f.y, f.angle) for f in b.footprints]
        rnd = _random.Random(4)
        broken_fp = broken_tr = 0
        for i in range(30):
            b.tracks = [_copy.copy(t) for t in T0]
            b.vias = [_copy.copy(v) for v in V0]
            for f, (x, y, a) in zip(b.footprints, F0):
                f.set_pose(x, y, a)
            grp = rnd.sample(b.footprints, rnd.randint(1, 4))
            plan = E.plan_footprint_move(b, grp)
            if i % 2:
                cx, cy = E.selection_center(grp)
                E.apply_footprint_plan(plan, E.rotation(cx, cy, 90), 90)
            else:
                E.apply_footprint_plan(plan, E.translation(rnd.uniform(-4, 4), rnd.uniform(-4, 4)))
            broken_fp += bool(unconnected(b))
            b.tracks = [_copy.copy(t) for t in T0]
            b.vias = [_copy.copy(v) for v in V0]
            for f, (x, y, a) in zip(b.footprints, F0):
                f.set_pose(x, y, a)
            gen = [t for t in b.tracks if E.is_generated(t)]
            tp = E.plan_track_move(b, rnd.sample(gen, rnd.randint(1, 3)), rnd.sample(b.vias, 1) if i % 3 == 0 and b.vias else [])
            E.apply_track_plan(b, tp, rnd.choice([-0.5, 0.5, 1.0]), rnd.choice([-0.5, 0.0, 0.5]))
            broken_tr += bool(unconnected(b))
        check(f"Mover/girar grupos de huellas con sus pistas ({name})", broken_fp == 0, f"30 casos, conexiones rotas en {broken_fp}")
        check(f"Arrastrar segmentos y vías sin romper conexiones ({name})", broken_tr == 0, f"30 casos, rotas en {broken_tr}")

    section("4e. Idiomas (español, inglés, alemán, francés)")
    from openrouteai.i18n_check import check as _i18n_check, source_keys
    from openrouteai.i18n import set_language, tr
    missing, bad = _i18n_check()
    check("Traducciones completas y marcadores coherentes", not missing and not bad,
          f"{len(source_keys())} cadenas; sin traducir: {missing[:5] or 0}; marcadores distintos: {bad[:3] or 0}")
    samples = []
    for lang in ("es", "en", "de", "fr"):
        set_language(lang)
        samples.append(tr("Enrutar todo"))
    set_language("es")
    log("         " + " / ".join(samples))

    section("4f. Planos rellenos y huellas en la cara inferior")
    try:
        from openrouteai import pipeline
        from openrouteai.zones_fill import fill_violations, zone_clearance
        from openrouteai.kicad.pcb_export import export_board
        src = os.path.join(OUT, "placas", "prueba_mcu.kicad_pcb")
        pr = pipeline.open_project(src)
        t0 = time.time()
        r = pipeline.route(pr, RouterConfig(strategy="negotiated", time_limit=600, planes={"B.Cu": "GND"}))
        zs = [z for z in pr.board.zones if z.net == "GND"]
        filled = all(z.filled for z in zs) and zs
        viol = sum(len(fill_violations(pr.board, z)) for z in zs)
        rep = pipeline.drc(pr)
        check("Plano de masa creado y relleno respetando el clearance", bool(filled) and viol == 0 and r.completion >= 99.9,
              f"{len(zs)} zona(s), {sum(len(z.filled) for z in zs)} isla(s), "
              f"{sum(len(z.filled[0][1]) for z in zs if z.filled)} agujeros, clearance {zone_clearance(pr.board, 'GND')} mm, "
              f"{r.completion}%  DRC={rep.summary() or 'ok'}  {time.time() - t0:.1f} s")
        out = os.path.join(OUT, "prueba_mcu_plano_relleno.kicad_pcb")
        pipeline.save(pr, r, out)
        b2 = load_board(out)
        z2 = [z for z in b2.zones if z.net == "GND"]
        rep2 = run_drc(b2)
        check("  Relleno guardado (filled_polygon) y releído", z2 and all(z.filled for z in z2) and
              not (rep2.count() - rep2.count("diff_skew")),
              f"{sum(len(z.filled) for z in z2)} polígono(s) rellenos, DRC={rep2.summary() or 'ok'}")
        # huellas a la cara inferior: espejo horizontal, ida y vuelta por el archivo
        b = load_board(os.path.join(OUT, "placas", "prueba_simple.kicad_pcb"))
        bad = 0
        expect = {}
        for fp in [f for f in b.footprints if f.node is not None][:6]:
            before = [(p.x, p.y) for p in fp.pads]
            fp.flip()
            bad += sum(abs((2 * fp.x - x) - p.x) > 1e-6 or abs(y - p.y) > 1e-6 for (x, y), p in zip(before, fp.pads))
            bad += any(not any(l.startswith("B.") for l in p.layers) for p in fp.pads if p.kind == "smd")
            expect[fp.ref] = [(round(p.x, 4), round(p.y, 4), tuple(sorted(p.layers))) for p in fp.pads]
        out = os.path.join(OUT, "prueba_simple_cara_inferior.kicad_pcb")
        export_board(b, out, [], [], [], placement_changed=True)
        export_board(b, out, [], [], [], placement_changed=True)
        b3 = load_board(out)
        for fp in b3.footprints:
            if fp.ref in expect:
                bad += fp.layer != "B.Cu"
                bad += [(round(p.x, 4), round(p.y, 4), tuple(sorted(p.layers))) for p in fp.pads] != expect[fp.ref]
        check("Cambiar huellas a la cara inferior (espejo) y guardarlas para KiCad", bad == 0,
              f"{len(expect)} huellas: {', '.join(expect)} — {os.path.basename(out)}")
    except Exception:
        check("Planos rellenos / cara inferior", False, traceback.format_exc(limit=5))

    section("5. Placas completas (PathFinder) + exportación + reimportación")
    cli = find_kicad_cli()
    if cli:
        log(f"kicad-cli encontrado: {cli}")
    else:
        warn("kicad-cli no encontrado", "no se puede validar con el DRC oficial de KiCad")
    cases = [("prueba_simple", {}), ("prueba_mcu", {}), ("prueba_mcu_4capas", {"planes": {"In1.Cu": "GND", "In2.Cu": "GND"}, "stitch_pitch": 6.0}),
             ("prueba_mcu", {"planes": {"B.Cu": "GND"}, "_suffix": "_plano"})]
    if not rapida:
        cases.append(("prueba_densa", {}))
    for name, opts in cases:
        opts = dict(opts)
        suffix = opts.pop("_suffix", "")
        src = os.path.join(OUT, "placas", name + ".kicad_pcb")
        try:
            from openrouteai import pipeline
            pr = pipeline.open_project(src)
            cfg = RouterConfig(strategy="negotiated", time_limit=600, **opts)
            r = pipeline.route(pr, cfg)
            rep = pipeline.drc(pr)
            m = r.metrics()
            errs = rep.count() - rep.count("diff_skew") - rep.count("fabricacion")
            check(f"Enrutar {name}{suffix}", r.completion >= 99.9 and errs == 0,
                  f"{m['completado_%']}% ({m['conexiones']})  {m['longitud_total_mm']} mm  {m['vias']} vías  "
                  f"{m['iteraciones']} it  {m['tiempo_s']} s  DRC={rep.summary() or 'ok'}")
            if r.failed_nets:
                log(f"         redes sin completar: {', '.join(r.failed_nets)}")
            out = os.path.join(OUT, f"{name}{suffix}_enrutado.kicad_pcb")
            pipeline.save(pr, r, out)
            render_png(pr.board, os.path.join(OUT, f"{name}{suffix}_enrutado.png"),
                       f"{name}{suffix}: {m['completado_%']}%  {m['vias']} vías")
            b2 = load_board(out)
            rep2 = run_drc(b2)
            errs2 = rep2.count() - rep2.count("diff_skew")
            check(f"  Reimportar {os.path.basename(out)} y DRC", errs2 == 0 and len(b2.tracks) == len(pr.board.tracks),
                  f"{len(b2.tracks)} pistas, {len(b2.vias)} vías, {len(b2.zones)} zonas, DRC={rep2.summary() or 'ok'}")
            if cli:
                counts, ver = kicad_drc(cli, out)
                if counts is None:
                    warn(f"  DRC de KiCad en {os.path.basename(out)}", f"no se pudo ejecutar: {ver}")
                else:
                    copper = {k: v for k, v in counts.items() if k in COPPER_TYPES or k == "unconnected_items"}
                    bad = {k: v for k, v in copper.items() if v}
                    other = {k: v for k, v in counts.items() if k not in copper and v}
                    check(f"  DRC oficial KiCad {ver}", not bad,
                          f"cobre: {bad or 'sin errores'}; otros (no de enrutado): {other or 'ninguno'}")
        except Exception:
            check(f"Enrutar {name}{suffix}", False, traceback.format_exc(limit=5))

    section("6. Colocación automática, optimizador e IA")
    try:
        from openrouteai.placement.placer import Placer, PlacerConfig
        b = load_board(simple)
        st = Placer(b, PlacerConfig(iterations=8000)).run()
        r = Router(b, RouterConfig()).run()
        rep = run_drc(b)
        check("Colocación (recocido simulado) + enrutado", st["despues"] < st["antes"] and st["solape_residual"] == 0
              and r.completion >= 99.9, f"{st}  → {r.metrics()['completado_%']}%  DRC={rep.summary() or 'ok'}")
        out = os.path.join(OUT, "prueba_simple_colocada.kicad_pcb")
        export_board(b, out, r.tracks, r.vias, r.zones, placement_changed=True)
        b2 = load_board(out)
        rep2 = run_drc(b2)
        check("  Exportar colocación y reimportar", rep2.count() == 0, str(rep2.summary() or "ok"))
        render_png(b, os.path.join(OUT, "prueba_simple_colocada.png"), "colocación automática")
    except Exception:
        check("Colocación", False, traceback.format_exc(limit=4))
    try:
        from openrouteai.optimization.optimizer import OptConfig, Optimizer
        b = load_board(simple)
        best, r = Optimizer(b, RouterConfig(), OptConfig(population=3, generations=1)).run()
        check("Optimizador genético", r.completion >= 99.9,
              f"parámetros {({k: round(v, 2) for k, v in best.items() if k != 'order'})}")
    except Exception:
        check("Optimizador genético", False, traceback.format_exc(limit=4))
    try:
        from openrouteai.ai.learn import export_graph, learn_priors, scan_projects
        ds = scan_projects(OUT)
        pri = learn_priors(ds)
        check("IA: aprender priors de placas enrutadas", bool(pri), f"{len(ds)} placas → {pri}")
        g = export_graph(load_board(mcu))
        check("IA: exportar grafo (nodos con posición/capas/restricciones/prioridad/clase)",
              len(g["nodes"]) > 0 and all(k in g["nodes"][0] for k in ("x", "layers", "constraints", "priority", "netclass")),
              f"{len(g['nodes'])} nodos, {len(g['edges'])} aristas")
    except Exception:
        check("IA", False, traceback.format_exc(limit=4))
    try:
        from openrouteai.plugins import load_plugins
        api = load_plugins()
        check("Plugins", not api.errors, f"cargados: {api.loaded}")
    except Exception:
        check("Plugins", False, traceback.format_exc(limit=3))

    section("7. Interfaz gráfica (modo sin pantalla)")
    if gui_ok:
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtWidgets import QApplication
            from openrouteai.gui.main_window import MainWindow
            app = QApplication.instance() or QApplication([])
            w = MainWindow()
            w.resize(1400, 900)
            w.show()
            w.open_project(os.path.join(OUT, "prueba_mcu_enrutado.kicad_pcb"))
            app.processEvents()
            w.chk_cong.setChecked(True)
            w.refresh()
            app.processEvents()
            w.grab().save(os.path.join(OUT, "gui_2d.png"))
            w.tabs.setCurrentIndex(1)
            app.processEvents()
            w.grab().save(os.path.join(OUT, "gui_3d.png"))
            w.run_drc()
            check("GUI: ventana, vista 2D, vista 3D y DRC", True, "capturas gui_2d.png y gui_3d.png")
            from openrouteai.gui.icons import save_preview
            save_preview(os.path.join(OUT, "gui_iconos.png"))
            check("GUI: barra de herramientas con iconos", w.act_route.icon().isNull() is False,
                  "gui_iconos.png")
            some = [n for n in w.project.board.nets if n][:2]
            w.view.set_selected_nets(set(some), emit=True)
            app.processEvents()
            w.grab().save(os.path.join(OUT, "gui_redes_resaltadas.png"))
            check("GUI: resaltar y seleccionar redes en la vista", w.view.sel_nets == set(some) and
                  len(w.view._orig_style) > 0, f"{some} — gui_redes_resaltadas.png")
            for lang in ("en", "de", "fr"):
                w.switch_language(lang)
                app.processEvents()
                w = [x for x in __import__("openrouteai.gui.main_window", fromlist=["x"])._WINDOWS][-1]
                w.grab().save(os.path.join(OUT, f"gui_idioma_{lang}.png"))
            from openrouteai.gui.theme import apply_theme
            apply_theme("dark", app)
            app.processEvents()
            w.grab().save(os.path.join(OUT, "gui_tema_oscuro.png"))
            apply_theme("light", app)
            check("GUI: tema oscuro / claro", True, "gui_tema_oscuro.png")
            check("GUI: cambio de idioma conservando el proyecto", w.project is not None,
                  "gui_idioma_en.png, gui_idioma_de.png, gui_idioma_fr.png")
            w.switch_language("es")
            w = __import__("openrouteai.gui.main_window", fromlist=["x"])._WINDOWS[-1]

            # visibilidad de referencias / valores / números de pad / huellas
            mine = sorted(glob.glob(os.path.join(HERE, "mis_proyectos", "**", "*_enrutado.kicad_pcb"), recursive=True))
            demo = mine[0] if mine else os.path.join(OUT, "prueba_simple_colocada.kicad_pcb")
            w.open_project(demo)
            w.tabs.setCurrentIndex(0)
            for cat, cb in w.chk_cat2d.items():
                cb.setChecked(True)
            app.processEvents()
            w.grab().save(os.path.join(OUT, "gui_2d_textos_huellas.png"))
            counts = {c: len(v) for c, v in w.view.cat_items.items()}
            visible_on = all(it.isVisible() for v in w.view.cat_items.values() for it in v)
            for cb in w.chk_cat2d.values():
                cb.setChecked(False)
            app.processEvents()
            hidden_off = not any(it.isVisible() for v in w.view.cat_items.values() for it in v)
            w.grab().save(os.path.join(OUT, "gui_2d_sin_textos.png"))
            check("GUI 2D: mostrar/ocultar referencias, valores, nº de pad y huellas",
                  visible_on and hidden_off and counts["ref"] > 0 and counts["fp"] > 0, f"{counts}")
            w.tabs.setCurrentIndex(1)
            for cb in w.chk_show3d.values():
                cb.setChecked(True)
            app.processEvents()
            w.grab().save(os.path.join(OUT, "gui_3d_todo.png"))
            last = w.project.board.copper_layers[-1]
            for i in range(w.l3d_layers.count()):
                cbl = w.l3d_layers.itemAt(i).widget()
                if cbl is not None and cbl.text() == last:
                    cbl.setChecked(False)
            w.chk_show3d["bodies"].setChecked(False)
            app.processEvents()
            w.grab().save(os.path.join(OUT, f"gui_3d_sin_{last.replace('.', '')}_sin_cuerpos.png"))
            check("GUI 3D: mostrar/ocultar capas de cobre, textos, huellas y cuerpos",
                  w.view3d.layer_visible.get(last) is False and not w.view3d.show["bodies"],
                  f"capturas gui_3d_todo.png y gui_3d_sin_*.png ({os.path.basename(demo)})")
            # plano relleno visible y cambio de cara desde la interfaz
            w.open_project(os.path.join(OUT, "prueba_mcu_plano_relleno.kicad_pcb"))
            w.tabs.setCurrentIndex(0)
            app.processEvents()
            zb = w.project.board
            w.fill_zones()
            app.processEvents()
            w.grab().save(os.path.join(OUT, "gui_plano_relleno.png"))
            ok_fill = any(z.filled for z in zb.zones)
            smd = [f for f in zb.footprints if f.pads and all(p.kind == "smd" for p in f.pads)][:1]
            w._set_mode("fp")
            w.view.sel_fps = list(smd)
            w.view.flip_selection()
            flipped = bool(smd) and smd[0].layer == "B.Cu"
            stale = any(z.stale for z in zb.zones)
            w.tabs.setCurrentIndex(1)
            app.processEvents()
            w.grab().save(os.path.join(OUT, "gui_3d_plano_y_cara_inferior.png"))
            w.undo()
            check("GUI: plano relleno en 2D/3D, cambio de cara (F) y deshacer",
                  ok_fill and flipped and stale and smd[0].layer == "F.Cu",
                  "gui_plano_relleno.png, gui_3d_plano_y_cara_inferior.png")
            w.close()
        except Exception:
            check("GUI", False, traceback.format_exc(limit=6))
    else:
        warn("GUI no probada", "instala PySide6 (install.bat)")

    section("8. Proyectos del usuario (carpeta mis_proyectos/)")
    mine = os.path.join(HERE, "mis_proyectos")
    pcbs = [p for p in glob.glob(os.path.join(mine, "**", "*.kicad_pcb"), recursive=True)
            if not p.endswith("_enrutado.kicad_pcb") and "-backups" not in p]
    if not pcbs:
        log("(vacía — copia aquí proyectos KiCad para probarlos)")
    for p in pcbs:
        try:
            from openrouteai import pipeline
            pr = pipeline.open_project(p)
            for n in pr.notes[:5]:
                log("    · " + n)
            r = pipeline.route(pr, RouterConfig(time_limit=900))
            rep = pipeline.drc(pr)
            m = r.metrics()
            out = os.path.splitext(p)[0] + "_enrutado.kicad_pcb"
            pipeline.save(pr, r, out)
            render_png(pr.board, os.path.splitext(out)[0] + ".png", os.path.basename(p))
            log(f"[INFO] {os.path.relpath(p, HERE)}: {m['completado_%']}% {m['conexiones']} "
                f"{m['vias']} vías {m['tiempo_s']} s DRC={rep.summary() or 'ok'}")
            if r.failed_nets:
                log(f"       sin completar: {', '.join(r.failed_nets[:20])}")
            if cli:
                counts, ver = kicad_drc(cli, out)
                log(f"       DRC KiCad: {counts}")
        except Exception:
            warn(f"Proyecto {os.path.basename(p)}", traceback.format_exc(limit=4))

    if table:
        section("Comparación de estrategias (placa simple)")
        log(f"{'estrategia':<12}{'compl.%':>9}{'long. mm':>10}{'vías':>6}{'codos':>7}{'tiempo s':>10}")
        for s, m in table:
            log(f"{s:<12}{m['completado_%']:>9}{m['longitud_total_mm']:>10}{m['vias']:>6}{m['codos']:>7}{m['tiempo_s']:>10}")
    return finish(t_start)


def finish(t0):
    section("RESUMEN")
    log(f"OK: {results['ok']}   FALLOS: {results['fallo']}   AVISOS: {results['aviso']}   "
        f"Tiempo total: {time.time() - t0:.0f} s")
    log(f"Imágenes y placas en: {OUT}")
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\nInforme guardado en {REPORT}")
    return 1 if results["fallo"] else 0


if __name__ == "__main__":
    sys.exit(main())
