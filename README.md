# OpenRouteAI

Autorouter de PCB de código abierto para proyectos **KiCad** (formatos KiCad 6 a 10+).
Lee el proyecto (`.kicad_pro`, `.kicad_pcb`, `.kicad_sch`, netlist), enruta todas las
pistas respetando las reglas de diseño del proyecto y escribe un `.kicad_pcb` válido
que se abre directamente en KiCad.

Multiplataforma (Windows, Linux, macOS) · Python 3.10+ · NumPy/SciPy · Qt 6 (PySide6) · GPL-3.0-or-later

> Estado: **v0.6.0**. El enrutado de placas de 2 y 4 capas con SMD y THT funciona
> y se valida automáticamente con DRC propio y, si está instalado, con el DRC oficial de KiCad
> (`kicad-cli`). Lo que aún no está implementado se detalla en [ROADMAP.md](ROADMAP.md).

## Instalación

**Windows**: doble clic en `install.bat` (crea `.venv` e instala dependencias).
**Linux / macOS**: `./install.sh`

| Script | Qué hace |
|---|---|
| `run.bat` / `run.sh` | Abre la interfaz gráfica |
| `autoprueba.bat` / `autoprueba.sh` | Batería de pruebas → `autoprueba_resultado.txt` + imágenes en `autoprueba_salida/` |
| `enrutar.bat proyecto.kicad_pro` | Enruta desde la línea de comandos |
| `install_gpu.bat` / `install_gpu.sh` | (Opcional) instala CuPy para usar una GPU NVIDIA |

## Flujo de trabajo con KiCad

1. En KiCad, dibuja el esquema y en el editor de PCB usa **Herramientas → Actualizar PCB desde esquema (F8)**,
   coloca las huellas (o deja que OpenRouteAI las coloque) y dibuja el contorno en `Edge.Cuts`. Guarda.
2. Abre el `.kicad_pro` (o el `.kicad_pcb`) en OpenRouteAI, elige estrategia y pulsa **Enrutar todo**.
3. Revisa el resultado (vista 2D/3D, mapa de congestión, DRC, comparación de rutas) y **Guarda**.
   Se crea `nombre_enrutado.kicad_pcb` (y una copia del `.kicad_pro` con el mismo nombre para que KiCad
   use las mismas reglas). Los planos de masa se guardan ya rellenos (`filled_polygon`), así que se ven
   en KiCad sin pulsar **B**.

## Interfaz

- **Idioma**: menú *Idioma / Language* con español, inglés, alemán y francés (se guarda en
  `~/.openrouteai/config.json`; el cambio es inmediato y conserva el trabajo).
- **Paneles**: *Enrutado* (estrategia, pistas y vías, rendimiento, planos, visualización) a la derecha;
  *Información* (estadísticas, redes, DRC, comparación, registro) a la izquierda.
- **Seleccionar** (S, modo por defecto): clic en una pista o pad selecciona su red (se resaltan pads, pistas
  y vías); clic en una vía la selecciona; Ctrl+clic añade; cuadro para varias. La selección se sincroniza con
  la pestaña «Redes». La vista se desplaza con el botón central o el derecho y se acerca con la rueda.
- **Listas de anchos de pista y tamaños de vía** (+ / −), leídas y guardadas en el `.kicad_pro` como en KiCad:
  *Aplicar a redes selecc.* cambia el ancho de sus pistas y se usa al reenrutar; *Aplicar a vías selecc.*
  cambia las vías elegidas en la vista; *A redes selecc.* usa ese tamaño para todas las vías de esas redes.
- **Barra superior**: Abrir, Guardar, Enrutar todo (F5), Reenrutar selección, DRC, Borrar enrutado,
  Mover huellas (M), Girar 90°, Cambiar de cara, Editar pistas (E), Deshacer y Rellenar zonas (B), con selector de rejilla de colocación.
- **Mover huellas** (M): clic, Ctrl+clic o cuadro de selección (de izquierda a derecha: huellas enteras
  dentro; de derecha a izquierda: basta con tocarlas). Arrastra para mover el grupo; **R** gira 90°
  (Mayús+R al revés), **Esc** cancela. Las pistas siguen el movimiento: si todas las huellas de una
  conexión se mueven, la pista se mueve entera; si no, se estiran sus extremos (como «arrastrar» en KiCad).
  Las conexiones pendientes también siguen en directo. Se avisa del DRC y de courtyards solapados.
- **Editar pistas** (E): selecciona segmentos o vías (clic, Ctrl+clic o cuadro) y arrástralos; los
  segmentos vecinos se estiran y, si un extremo estaba en un pad, se añade un tramo de unión, de modo que
  nunca se rompe una conexión. **Supr** borra la selección. Solo se editan pistas generadas.
- **Cambiar de cara** (F en modo mover, o botón de la barra): pasa las huellas seleccionadas a la cara
  inferior (o de vuelta a la superior) en espejo horizontal sobre su origen, como «Voltear» de KiCad: pads
  SMD, serigrafía, fabricación y courtyard pasan a las capas `B.*` y los textos se guardan en espejo. Las
  pistas generadas de sus redes se borran para reenrutarlas.
- **Planos de masa** (panel *Planos y cosido*): al enrutar se crea la zona y se **rellena** con el
  clearance de su clase de red (grosor mínimo 0,25 mm, sin islas sueltas, conexión sólida a los pads). Se
  ve en las vistas 2D y 3D. Si el plano no llega a todos los pads de su red, esas conexiones se completan
  con pistas. **Rellenar zonas** (B) recalcula todos los rellenos (también las zonas del archivo); tras
  mover huellas o editar pistas el relleno se marca como desactualizado (más tenue) y se recalcula solo
  al guardar. El DRC comprueba el relleno frente al cobre de otras redes.
- **Modo oscuro / claro**: menú *Ver* (se recuerda en `~/.openrouteai/config.json`).
- **Deshacer** (Ctrl+Z): hasta 30 pasos de movimientos y ediciones.
- Al seleccionar redes en la pestaña «Redes» se resaltan sus pads, pistas y vías.
- **Pistas y vías**: tabla editable de clases de red (pista, vía, taladro, clearance), diámetro de vía
  mínimo y máximo, y ancho de pista para las redes seleccionadas en la pestaña «Redes».
- **Depurar pistas tras enrutar** (activado por defecto): sustituye cadenas de quiebros por la conexión
  recta + 45° más corta cuando respeta el clearance; no mueve vías ni puntos de unión y nunca alarga.
- Vistas 2D y 3D con capas, referencias, valores, números de pad, huellas y cuerpos (en 3D) activables.

## Rendimiento y GPU

- **Núcleo de búsqueda en C** (`openrouteai/routing/_native/`): A*, Dijkstra, Lee y Hadlock compilados.
  Se incluyen binarios para Windows x64 (`.dll`, sin dependencias) y Linux x86_64 (`.so`); en macOS se
  compila automáticamente la primera vez si hay `clang` (Xcode Command Line Tools). Si no hay binario
  ni compilador, se usa la versión Python (unas 20 veces más lenta). `python -m openrouteai info` dice cuál se usa.
- **Ocupación incremental**: cada ruta "estampa" su huella dilatada al confirmarse, en lugar de recalcular
  filtros sobre toda la ventana en cada búsqueda.
- **Modo de velocidad** (GUI → Rendimiento, o `heur_weight` en la API): A* ponderado. "Equilibrado" y
  "Rápido" son 1,5-3 veces más rápidos a cambio de ~1 % más de longitud y más vías.
- **GPU NVIDIA (opcional)**: `install_gpu.bat` instala CuPy. La GPU mantiene la rejilla de costes y calcula
  en paralelo los costes de cada ventana; la búsqueda del camino sigue en el núcleo C (es secuencial por
  naturaleza). Compensa en placas grandes (millones de celdas, 4+ capas, paso fino); en placas pequeñas no.
  El modo *Automático* solo la usa a partir de ~6 millones de celdas. Mide tu equipo con
  `python -m openrouteai bench`. CuPy requiere **Python ≤ 3.14**.

| Placa | v0.1.0 | v0.2.0 |
|---|---|---|
| 175×105 mm, 42 componentes, 91 conexiones (PathFinder) | 131 s | 3,0 s |
| prueba_densa (48 conexiones) | 52 s | 0,9 s |
| prueba_mcu (39 conexiones) | 5,7 s | 0,2 s |

(medido en el mismo equipo de 2 núcleos; mismo resultado y DRC sin errores)

## Línea de comandos

```
python -m openrouteai route proyecto.kicad_pro -o salida.kicad_pcb
python -m openrouteai route placa.kicad_pcb --strategy ripup --planes B.Cu=GND --png vista.png
python -m openrouteai route placa.kicad_pcb --nets "SDA,SCL"          # reenrutado parcial
python -m openrouteai route placa.kicad_pcb --place                   # colocación automática previa
python -m openrouteai route placa.kicad_pcb --optimize genetic        # optimiza parámetros y orden
python -m openrouteai drc placa.kicad_pcb
python -m openrouteai analyze carpeta_del_proyecto/
python -m openrouteai learn carpeta_con_muchos_proyectos/ -o modelo.json
python -m openrouteai route placa.kicad_pcb --model modelo.json       # usar lo aprendido
python -m openrouteai graph placa.kicad_pcb -o grafo.json             # dataset para GNN
python -m openrouteai gen-tests carpeta/                              # placas de ejemplo
python -m openrouteai route placa.kicad_pcb --device gpu              # usar la GPU
python -m openrouteai route placa.kicad_pcb --via-min 0.6 --via-max 0.8 --net-width GND=0.8
python -m openrouteai route placa.kicad_pcb --no-cleanup              # sin depurar pistas
python -m openrouteai info                                            # núcleo nativo y GPU disponibles
python -m openrouteai bench [placa.kicad_pcb]                         # medir CPU frente a GPU
```

## Estrategias de enrutado

| Estrategia | Descripción |
|---|---|
| `negotiated` (por defecto) | **PathFinder / Negotiated Congestion Routing**: todas las redes negocian el espacio con costes de congestión presentes e históricos hasta que no queda ningún conflicto. Mejor calidad. |
| `ripup` | A* secuencial con **Rip-up and Retry**: si una red no cabe, levanta las que la bloquean y las reenruta. Rápido. |
| `astar` | **A\*** secuencial sin rip-up, como referencia (heurística octil, penalización de giros y dirección preferente por capa). |
| `maze` | **Maze routing** ponderado (Dijkstra) + rip-up and retry. |
| `lee` | **Algoritmo de Lee** (expansión de onda, coste unitario) + rip-up and retry. |
| `hadlock` | **Algoritmo de Hadlock** (búsqueda por *detour number*) + rip-up and retry. |

Todas trabajan en una rejilla 3D (capa × fila × columna) con **campos de distancia exactos** al cobre
fijo, de modo que el clearance se respeta con la geometría real de pads y pistas (no con píxeles).
Cada red se construye como árbol de Steiner incremental. Las pistas salen a 0°/45°/90°.

## Arquitectura

```
openrouteai/
  core/          modelo de datos, S-expressions KiCad, geometría, render PNG
  kicad/         KiCad Import Engine (pcb, pro, sch, netlist) y Export Engine
  analysis/      Netlist Analyzer (clasificación de redes, grafos, pares diferenciales, prioridades)
  placement/     Component Placement Engine (recocido simulado)
  routing/       Routing Engine (rejilla, A*/Lee/Hadlock/Maze, PathFinder, rip-up, planos, cosido)
  drc/           DRC Validation Engine (geometría continua)
  optimization/  Optimization Engine (algoritmo genético, PSO; métricas ponderadas)
  ai/            IA: aprendizaje de priors desde proyectos KiCad, exportación de grafos
  plugins/       sistema de plugins (DRC, post-proceso, clasificadores, estrategias)
  gui/           interfaz Qt 6: vista 2D, vista 3D, congestión, estadísticas, comparación, DRC
  pipeline.py    API interna de alto nivel
  cli.py         línea de comandos
plugins/         plugins de usuario (ejemplo incluido)
```

Detalle en [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md).

## Reglas de diseño soportadas

Se leen del `.kicad_pro`: clearance, ancho de pista, diámetro/taladro de vía por clase de red,
asignación de clases por patrón (`netclass_patterns`), distancia cobre-borde, mínimos globales,
y anchos/separación de pares diferenciales (se usan para el informe; el enrutado acoplado es fase 2).
Las zonas de exclusión (*rule areas*) de la placa se respetan como obstáculos.

## Licencia

GPL-3.0-or-later. Ver [LICENSE](LICENSE).
