@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === OpenRouteAI: instalacion ===
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version || (echo No se encontro Python 3.10+. Instalalo desde https://www.python.org & pause & exit /b 1)
if not exist .venv (
  %PY% -m venv .venv || (echo Error creando el entorno virtual & pause & exit /b 1)
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt || (echo Error instalando dependencias & pause & exit /b 1)
echo.
echo Instalacion completada. Ejecuta run.bat para abrir la aplicacion o autoprueba.bat para probarla.
pause
