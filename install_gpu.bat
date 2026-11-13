@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === OpenRouteAI: aceleracion por GPU (NVIDIA CUDA + CuPy) ===
echo.
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo No se encuentra nvidia-smi: no hay GPU NVIDIA o falta el driver.
  echo La GPU es opcional; OpenRouteAI funciona igual en CPU.
  pause & exit /b 1
)
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
echo.
if not exist .venv\Scripts\python.exe (echo Ejecuta primero install.bat & pause & exit /b 1)

rem CuPy publica ruedas para Python 3.10 - 3.14
.venv\Scripts\python -c "import sys; sys.exit(0 if sys.version_info[:2] <= (3, 14) else 1)"
if errorlevel 1 (
  echo El entorno usa una version de Python mas nueva que la que admite CuPy ^(max. 3.14^).
  py -3.14 --version >nul 2>nul
  if errorlevel 1 (
    echo Instala Python 3.14 desde https://www.python.org y vuelve a ejecutar este script.
    pause & exit /b 1
  )
  choice /M "Recrear .venv con Python 3.14 (se reinstalan las dependencias)"
  if errorlevel 2 (echo Cancelado. & pause & exit /b 1)
  rmdir /s /q .venv
  py -3.14 -m venv .venv || (echo Error creando el entorno & pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt || (echo Error instalando dependencias & pause & exit /b 1)
)

echo Instalando CuPy para CUDA 12 (incluye las librerias CUDA, no hace falta el CUDA Toolkit)...
.venv\Scripts\python -m pip install "cupy-cuda12x[ctk]"
if errorlevel 1 (
  echo Probando CuPy para CUDA 13...
  .venv\Scripts\python -m pip install cupy-cuda13x
)
echo.
.venv\Scripts\python -m openrouteai info
echo.
echo Para medir CPU frente a GPU en este equipo:  .venv\Scripts\python -m openrouteai bench
pause
