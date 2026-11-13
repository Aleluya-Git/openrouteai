"""API interna de alto nivel (la usan la CLI, la GUI, la autoprueba y los plugins)."""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .analysis.netlist_analyzer import Analysis, analyze
from .core.model import Board
from .drc.drc import DRCReport, run_drc
from .kicad.pcb_export import export_board
from .kicad.pcb_import import load_board
from .i18n import tr
from .routing.router import RouteResult, Router, RouterConfig


@dataclass
class Project:
    board: Board
    pcb_path: str
    pro_path: Optional[str] = None
    sch_path: Optional[str] = None
    net_path: Optional[str] = None
    analysis: Optional[Analysis] = None
    notes: List[str] = field(default_factory=list)


def resolve_paths(path: str) -> Dict[str, Optional[str]]:
    """Acepta .kicad_pro, .kicad_pcb, .kicad_sch o una carpeta de proyecto."""
    if os.path.isdir(path):
        pros = glob.glob(os.path.join(path, "*.kicad_pro"))
        base = os.path.splitext(pros[0])[0] if pros else None
        if base is None:
            pcbs = glob.glob(os.path.join(path, "*.kicad_pcb"))
            base = os.path.splitext(pcbs[0])[0] if pcbs else os.path.join(path, "x")
    else:
        base = os.path.splitext(path)[0]

    def ex(ext):
        p = base + ext
        return p if os.path.exists(p) else None
    return {"pcb": ex(".kicad_pcb"), "pro": ex(".kicad_pro"), "sch": ex(".kicad_sch"), "net": ex(".net")}


def open_project(path: str) -> Project:
    paths = resolve_paths(path)
    if not paths["pcb"]:
        raise FileNotFoundError(
            "No se encontró el .kicad_pcb. En KiCad abre el editor de PCB y usa "
            "'Herramientas > Actualizar PCB desde esquema' (F8) para colocar las huellas, guarda y vuelve a abrir.")
    b = load_board(paths["pcb"], paths["pro"])
    pr = Project(board=b, pcb_path=paths["pcb"], pro_path=paths["pro"], sch_path=paths["sch"], net_path=paths["net"])
    pr.analysis = analyze(b)
    # validación cruzada con esquema / netlist
    try:
        if paths["net"]:
            from .kicad.netlist import compare_with_board, load_netlist
            issues = compare_with_board(load_netlist(paths["net"]), b)
            pr.notes += [f"Netlist: {i}" for i in issues[:50]] or [tr("Netlist: coincide con la PCB")]
        elif paths["sch"]:
            from .kicad.sch_import import compare_connectivity, load_schematic
            sch = load_schematic(paths["sch"])
            ok, total, diffs = compare_connectivity(sch.netlist, b)
            pr.notes += sch.warnings
            pr.notes.append(tr("Esquema: {ok}/{total} redes coinciden con la PCB", ok=ok, total=total))
            pr.notes += [f"Esquema≠PCB: {d}" for d in diffs[:20]]
    except Exception as exc:
        pr.notes.append(tr("No se pudo comparar con el esquema: {e}", e=exc))
    return pr


def route(project: Project, cfg: RouterConfig) -> RouteResult:
    res = Router(project.board, cfg, project.analysis).run()
    # los planos generados forman parte de la placa (conectividad en el DRC)
    have = {(z.net, tuple(z.layers)) for z in project.board.zones}
    project.board.zones += [z for z in res.zones if (z.net, tuple(z.layers)) not in have]
    from .plugins import load_plugins
    for name, fn in load_plugins().post_route.items():
        try:
            fn(project.board, res)
        except Exception as exc:
            res.log.append(f"Plugin {name}: {exc}")
    return res


def drc(project: Project) -> DRCReport:
    rep = run_drc(project.board)
    from .plugins import run_plugin_drc
    rep.violations += run_plugin_drc(project.board)
    return rep


def routed_items(board: Board):
    """Pistas y vías generadas por OpenRouteAI (no las que venían en el archivo)."""
    tracks = [t for t in board.tracks if not t.fixed or getattr(t, "_was_routed", False)]
    vias = [v for v in board.vias if not v.fixed or getattr(v, "_was_routed", False)]
    return tracks, vias


def save(project: Project, res: Optional[RouteResult], out_path: str, keep_existing: bool = True,
         placement_changed: bool = False, zones=None) -> None:
    tracks, vias = routed_items(project.board)
    if zones is None:
        zones = res.zones if res else []
    export_board(project.board, out_path, tracks, vias, zones,
                 keep_existing=keep_existing, placement_changed=placement_changed)
    # KiCad lee las reglas/clases de red del .kicad_pro con el mismo nombre
    if project.pro_path:
        target = os.path.splitext(out_path)[0] + ".kicad_pro"
        if not os.path.exists(target):
            import shutil
            shutil.copy2(project.pro_path, target)
