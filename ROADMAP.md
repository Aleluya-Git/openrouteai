# OpenRouteAI — Estado y hoja de ruta

Leyenda: ✅ hecho y probado · 🟡 parcial · ⏳ planificado

## Fase 1 (v0.1.0) — esta entrega

| Requisito | Estado | Notas |
|---|---|---|
| Importar proyectos KiCad 6–10+ (.kicad_pro/.kicad_pcb) | ✅ | Redes numeradas `(net 3 "GND")` y por nombre `(net "GND")`; el export usa el mismo estilo |
| Analizar esquemas .kicad_sch | 🟡 | Símbolos, pines, cables, etiquetas y alimentación de una hoja; jerárquicos → usar netlist |
| Analizar netlists de KiCad | ✅ | Comparación pin a pin con la PCB |
| Leer PCB existentes | ✅ | Huellas, pads (todas las formas), pistas, arcos, vías, zonas de exclusión, contorno |
| Colocación automática (opcional) | 🟡 | Recocido simulado (HPWL + solapes + borde). Útil para prototipos; no entiende aún restricciones térmicas/mecánicas |
| Routing completo | ✅ | PathFinder, rip-up, A*, maze, Lee, Hadlock |
| Exportar a KiCad | ✅ | Reescribe el árbol original; validado reimportando y con `kicad-cli pcb drc` en la autoprueba |
| Multicapa | ✅ | 2, 4… capas con vías pasantes y dirección preferente alterna por capa |
| Planos de masa/alimentación | ✅ | Zona en la capa elegida + bajada a plano con vía corta; capas internas reservadas |
| Cosido de vías (via stitching) | ✅ | Con planos en dos capas de la misma red |
| Optimización de vías | 🟡 | Coste de vía en la negociación y en el optimizador; falta eliminación post-ruta |
| Pares diferenciales | 🟡 | Detección, prioridad e informe de desfase; **enrutado acoplado pendiente** |
| Señales de alta velocidad | 🟡 | Clasificación y prioridad; sin control de impedancia |
| Length matching | 🟡 | Medición y DRC de longitudes; **meandros pendientes** |
| Bus routing | ⏳ | |
| Vías ciegas/enterradas, microvías | 🟡 | Se leen, se dibujan, se exportan y el DRC las entiende; el router aún solo genera pasantes |
| DRC: cortos, abiertos, clearance, colisiones, longitudes, diferenciales | ✅ | Geometría continua + plugins |
| IA: aprender de proyectos | 🟡 | Extracción de características de miles de proyectos → priors del router; exportación de grafos para GNN |
| IA: GNN / Transformers / RL | ⏳ | Entrenamiento externo con los grafos exportados; integración como plugin de estrategia |
| Algoritmo genético / PSO | ✅ | Orden de redes + parámetros del router |
| GUI Qt 6: 2D, 3D, congestión, estadísticas, comparación, reenrutado parcial | ✅ | Vista 3D ligera (QPainter), sin modelos 3D de componentes |
| Sistema de plugins | ✅ | DRC, post-ruta, clasificadores de red y estrategias |

## Fase 2
- Enrutado acoplado de pares diferenciales (rejilla de pares, separación constante).
- Meandros para *length matching* y ajuste de desfase de pares.
- Generación de vías ciegas/enterradas/microvías cuando el stackup lo permita.
- Post-proceso: suavizado de pistas (pull-tight), eliminación de vías innecesarias, teardrops.
- Bus routing (enrutado en paralelo de grupos de señales).
- ~~Núcleo de búsqueda compilado~~ ✅ v0.2.0 (C, ctypes) · ~~GPU~~ ✅ v0.2.0 (CuPy, costes de rejilla).
- PathFinder paralelo en varios núcleos (probado en v0.2.0 con particionado espacial: sin ganancia en
  placas reales porque las ventanas de redes multipin se solapan; pendiente un esquema tipo Jacobi).
- Búsqueda de caminos en GPU por lotes de redes (solo tendría sentido en placas muy grandes).

## Fase 3
- Dataset a partir de miles de proyectos KiCad open-source (scripts de descarga + `openrouteai learn`).
- GNN para sugerir colocación/topología; RL para orden de enrutado y rip-up.
- Integración como plugin de acción de KiCad (botón dentro del editor de PCB).
