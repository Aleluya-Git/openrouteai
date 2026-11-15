@echo off
rem Crea un acceso directo "OpenRouteAI" en el escritorio con el icono del programa
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (echo Ejecuta primero install.bat & pause & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'OpenRouteAI.lnk')); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='-m openrouteai gui'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0openrouteai\gui\resources\icon.ico'; $s.Description='OpenRouteAI - autorouter de PCB para KiCad'; $s.Save()"
if errorlevel 1 (echo No se pudo crear el acceso directo & pause & exit /b 1)
echo Acceso directo creado en el escritorio.
timeout /t 3 >nul
