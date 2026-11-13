#!/usr/bin/env bash
# Aceleración por GPU (NVIDIA CUDA + CuPy). CuPy admite Python 3.10 - 3.14.
cd "$(dirname "$0")"
command -v nvidia-smi >/dev/null || { echo "No hay GPU NVIDIA / driver (nvidia-smi)"; exit 1; }
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
.venv/bin/python -c "import sys; sys.exit(0 if sys.version_info[:2] <= (3,14) else 1)" || {
  echo "CuPy no admite esta versión de Python (máx. 3.14). Crea .venv con python3.14."; exit 1; }
.venv/bin/python -m pip install "cupy-cuda12x[ctk]" || .venv/bin/python -m pip install cupy-cuda13x
.venv/bin/python -m openrouteai info
