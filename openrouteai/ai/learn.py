"""Módulo de IA (fase 1): aprendizaje a partir de diseños KiCad existentes.

Implementado:
  - ``scan_projects``: recorre carpetas con proyectos KiCad (p. ej. miles de
    repositorios open-source clonados) y extrae características de colocación
    y enrutado de cada placa ya enrutada.
  - ``learn_priors``: condensa el dataset en un "modelo" de priors (JSON) que
    ajusta el router: coste de vía, preferencia de dirección por capa,
    penalización de giro y separación típica entre componentes conectados.
  - ``export_graph``: exporta la placa como grafo (nodos = pads con posición,
    capas, restricciones, prioridad y clase de red; aristas = conexiones y
    proximidad espacial) en JSON, listo para entrenar GNN / Transformers sobre
    grafos fuera de la aplicación.

Previsto (ver ROADMAP.md): GNN para sugerir topologías y RL para el orden de
enrutado, entrenados con los grafos exportados.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

from ..analysis.netlist_analyzer import analyze
from ..core.model import Board


@dataclass
class BoardFeatures:
    path: str
    layers: int
    components: int
    nets: int
    tracks: int
    vias: int
    track_length: float
    via_per_connection: float
    detour_ratio: float                       # longitud real / Manhattan
    dir_fraction: Dict[str, Dict[str, float]] = field(default_factory=dict)  # capa -> {H,V,D}
    width_by_kind: Dict[str, float] = field(default_factory=dict)
    neighbor_distance: float = 0.0            # distancia media entre componentes conectados


def board_features(b: Board) -> Optional[BoardFeatures]:
    if not b.tracks:
        return None
    an = analyze(b)
    dirs: Dict[str, Dict[str, float]] = defaultdict(lambda: {"H": 0.0, "V": 0.0, "D": 0.0})
    for t in b.tracks:
        dx, dy = abs(t.end[0] - t.start[0]), abs(t.end[1] - t.start[1])
        k = "H" if dy < 0.05 * max(dx, 1e-9) else ("V" if dx < 0.05 * max(dy, 1e-9) else "D")
        dirs[t.layer][k] += t.length
    for l, d in dirs.items():
        tot = sum(d.values()) or 1
        for k in d:
            d[k] = round(d[k] / tot, 3)
    man = sum(abs(c.a.x - c.b.x) + abs(c.a.y - c.b.y) for cs in an.connections.values() for c in cs)
    L = sum(t.length for t in b.tracks)
    widths: Dict[str, List[float]] = defaultdict(list)
    for t in b.tracks:
        n = b.nets.get(t.net)
        if n:
            widths[n.kind].append(t.width)
    nconn = max(1, an.stats.get("conexiones", 1))
    pos = {fp.ref: (fp.x, fp.y) for fp in b.footprints}
    nd = [math.dist(pos[a], pos[c]) for (a, c) in an.component_graph if a in pos and c in pos]
    return BoardFeatures(
        path=b.path, layers=len(b.copper_layers), components=len(b.footprints),
        nets=an.stats.get("redes_enrutables", 0), tracks=len(b.tracks), vias=len(b.vias),
        track_length=round(L, 2), via_per_connection=round(len(b.vias) / nconn, 3),
        detour_ratio=round(L / man, 3) if man else 1.0, dir_fraction=dict(dirs),
        width_by_kind={k: round(sum(v) / len(v), 3) for k, v in widths.items()},
        neighbor_distance=round(sum(nd) / len(nd), 2) if nd else 0.0)


def scan_projects(folder: str, progress: Optional[Callable[[str, float], None]] = None,
                  limit: int = 100000) -> List[BoardFeatures]:
    from ..kicad.pcb_import import load_board
    files = []
    for root, _, names in os.walk(folder):
        for n in names:
            if n.endswith(".kicad_pcb") and not n.startswith("_autosave"):
                files.append(os.path.join(root, n))
    files = files[:limit]
    out = []
    for i, f in enumerate(files):
        try:
            feat = board_features(load_board(f))
            if feat:
                out.append(feat)
        except Exception as exc:  # proyectos antiguos/corruptos: se ignoran
            if progress:
                progress(f"Ignorado {os.path.basename(f)}: {exc}", i / max(1, len(files)))
            continue
        if progress:
            progress(f"{i + 1}/{len(files)} {os.path.basename(f)}", (i + 1) / max(1, len(files)))
    return out


def learn_priors(dataset: List[BoardFeatures]) -> Dict:
    """Convierte el dataset en parámetros por defecto para el router."""
    if not dataset:
        return {}
    vpc = sorted(f.via_per_connection for f in dataset)
    med_vpc = vpc[len(vpc) // 2]
    # más vías por conexión en los diseños de referencia -> vías más baratas
    via_cost = max(0.5, min(4.0, 2.0 / (0.3 + med_vpc)))
    # direccionalidad: cuánto domina H/V por capa en placas de 2 capas
    dir_scores = []
    for f in dataset:
        for layer, d in f.dir_fraction.items():
            dir_scores.append(abs(d.get("H", 0) - d.get("V", 0)))
    directional = sum(dir_scores) / len(dir_scores) if dir_scores else 0.3
    diag = sum(d.get("D", 0) for f in dataset for d in f.dir_fraction.values()) / \
        max(1, sum(len(f.dir_fraction) for f in dataset))
    return {
        "via_cost_mm": round(via_cost, 3),
        "dir_penalty": round(min(1.0, directional * 0.8), 3),
        "turn_penalty": round(0.1 + 0.5 * (1 - diag), 3),
        "neighbor_distance_mm": round(sum(f.neighbor_distance for f in dataset) / len(dataset), 2),
        "detour_ratio": round(sum(f.detour_ratio for f in dataset) / len(dataset), 3),
        "boards": len(dataset),
    }


def save_model(path: str, dataset: List[BoardFeatures], priors: Dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"priors": priors, "dataset": [asdict(f) for f in dataset]}, fh, indent=1)


def load_priors(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh).get("priors", {})


def apply_priors(cfg, priors: Dict) -> None:
    for k in ("via_cost_mm", "dir_penalty", "turn_penalty"):
        if k in priors:
            setattr(cfg, k, float(priors[k]))


def export_graph(b: Board, spatial_radius: float = 5.0) -> Dict:
    """Grafo de componentes + conexiones + espacial con atributos por nodo."""
    an = analyze(b)
    nodes = []
    index = {}
    for i, p in enumerate(b.pads):
        index[id(p)] = i
        nc = b.netclass_of(p.net) if p.net else None
        net = b.nets.get(p.net)
        nodes.append({
            "id": i, "ref": p.ref, "x": p.x, "y": p.y,
            "layers": [l for l in b.copper_layers if l in p.layers or "*.Cu" in p.layers],
            "constraints": {"clearance": nc.clearance if nc else None, "track_width": nc.track_width if nc else None},
            "priority": net.priority if net else 0,
            "netclass": net.netclass if net else "", "net": p.net, "kind": net.kind if net else "",
            "w": p.w, "h": p.h, "smd": p.kind == "smd",
        })
    conn_edges = [{"a": index[id(c.a)], "b": index[id(c.b)], "net": c.net, "type": "connection"}
                  for cs in an.connections.values() for c in cs]
    spatial = []
    pads = b.pads
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            if abs(pads[i].x - pads[j].x) < spatial_radius and abs(pads[i].y - pads[j].y) < spatial_radius:
                d = math.hypot(pads[i].x - pads[j].x, pads[i].y - pads[j].y)
                if d < spatial_radius:
                    spatial.append({"a": i, "b": j, "d": round(d, 3), "type": "spatial"})
    comp_edges = [{"a": a, "b": c, "w": w, "type": "component"} for (a, c), w in an.component_graph.items()]
    return {"nodes": nodes, "edges": conn_edges + spatial, "component_graph": comp_edges,
            "layers": b.copper_layers, "stats": an.stats}
