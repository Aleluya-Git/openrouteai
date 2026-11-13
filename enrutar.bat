@echo off
rem Uso: enrutar.bat ruta\al\proyecto.kicad_pro [opciones]
cd /d "%~dp0"
.venv\Scripts\python -m openrouteai route %*
pause
