@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Executez d'abord installer.ps1 pour installer Collecte CFE.
  pause
  exit /b 1
)
start "Collecte CFE" ".venv\Scripts\pythonw.exe" -m collecte_cfe
