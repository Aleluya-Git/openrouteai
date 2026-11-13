@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Ejecuta primero install.bat & pause & exit /b 1)
.venv\Scripts\python -m openrouteai gui %*
if errorlevel 1 pause
