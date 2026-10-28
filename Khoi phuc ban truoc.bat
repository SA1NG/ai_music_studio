@echo off
rem Tra code ve phien ban truoc lan cap nhat gan nhat. Du lieu (bai, nhan, dataset, LoRA) duoc giu nguyen.
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Chua cai moi truong. Hay chay scripts\setup.bat truoc.
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\update.py --target "%~dp0." --rollback
pause
