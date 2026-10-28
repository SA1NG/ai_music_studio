@echo off
rem Co the chay o may khac: set API_URL=http://<ip-may-api>:8000 roi chay script nay
cd /d "%~dp0.."
set PYTHONUTF8=1
.venv\Scripts\python.exe -m worker.worker
