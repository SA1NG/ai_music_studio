@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Chua cai moi truong. Hay chay scripts\setup.bat truoc.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "scripts\desktop.py"
