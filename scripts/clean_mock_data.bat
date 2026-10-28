@echo off
rem Xoa du lieu mock (hoi xac nhan truoc). Tham so: --dry-run, --yes
cd /d "%~dp0.."
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts\clean_mock_data.py %*
