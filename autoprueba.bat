@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Ejecuta primero install.bat & pause & exit /b 1)
.venv\Scripts\python autoprueba.py %*
echo.
echo Resultado en autoprueba_resultado.txt  (imagenes en autoprueba_salida\)
pause
