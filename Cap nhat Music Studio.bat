@echo off
rem Cap nhat ban cai Music Studio tu goi phat hanh nay. Bam dup trong thu muc GOI da giai nen.
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "PACKAGE=%~dp0"
set "TARGET=%MUSIC_STUDIO_HOME%"
if "%TARGET%"=="" set "TARGET=D:\ai_music_studio"
set /p "INPUT=Thu muc cai Music Studio [%TARGET%]: "
for /f "tokens=* delims= " %%A in ("%INPUT%") do set "INPUT=%%A"
if "%INPUT%"==" " set "INPUT="
if not "%INPUT%"=="" set "TARGET=%INPUT%"
if "%TARGET:~-1%"=="\" set "TARGET=%TARGET:~0,-1%"
if not exist "%TARGET%\.venv\Scripts\python.exe" (
  echo Khong thay ban cai tai %TARGET%.
  echo Lan cai dau tien: giai nen goi vao thu muc cai roi chay scripts\setup.bat. Xem DEPLOY.md.
  pause
  exit /b 1
)
"%TARGET%\.venv\Scripts\python.exe" "%PACKAGE%scripts\update.py" --target "%TARGET%"
pause
