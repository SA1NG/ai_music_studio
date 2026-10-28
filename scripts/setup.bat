@echo off
rem Tao venv rieng cho du an, cai thu vien, tao .env tu .env.dev neu chua co.
rem Cua so luon dung lai o cuoi de doc ket qua; dat MUSIC_STUDIO_NO_PAUSE=1 khi chay tu script khac.
setlocal
cd /d "%~dp0.."

if not exist "requirements.txt" (
  echo Khong thay file cua ung dung canh setup.bat.
  echo Hay GIAI NEN file ZIP ra mot thu muc truoc ^(chuot phai - Extract All^), roi chay scripts\setup.bat trong thu muc do.
  goto fail
)

set "PY=%PYTHON%"
if "%PY%"=="" (
  py -3.11 -c "import sys" >nul 2>&1 && set "PY=py -3.11"
)
if "%PY%"=="" (
  python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>&1 && set "PY=python"
)
if "%PY%"=="" (
  echo Khong tim thay Python 3.11. Cai Python 3.11 tu python.org ^(tich "Add python.exe to PATH"^) roi chay lai.
  goto fail
)

echo [1/4] Tao moi truong ao .venv...
if not exist .venv (%PY% -m venv .venv || goto fail)
echo [2/4] Cai thu vien ^(lan dau mat vai phut^)...
.venv\Scripts\python.exe -m pip install --upgrade pip || goto fail
rem requirements.lock = dung phien ban thu vien da test khi dong goi; khong co thi dung requirements.txt.
set "REQ=requirements.txt"
if exist requirements.lock set "REQ=requirements.lock"
.venv\Scripts\python.exe -m pip install -r %REQ% || goto fail
.venv\Scripts\python.exe -c "import hashlib,pathlib; pathlib.Path('.venv/music_studio_requirements.sha256').write_text(hashlib.sha256(pathlib.Path('%REQ%').read_bytes()).hexdigest())"
echo [3/4] Cai Essentia...
.venv\Scripts\python.exe scripts\setup_essentia.py || echo Chua cai duoc Essentia - nhan AI se can kiem tra thu cong cho den khi cai xong.
if "%WITH_OPTIONAL%"=="1" (
  .venv\Scripts\python.exe -m pip install -r requirements-optional.txt || echo Cai thu vien tuy chon that bai - tinh nang tuong ung se tu tat.
)
echo [4/4] Tao file cau hinh...
if not exist .env (copy .env.dev .env >nul && echo Da tao .env tu .env.dev)
echo.
echo Xong. Sua file .env neu can, roi bam dup: Music Studio.bat
if not "%MUSIC_STUDIO_NO_PAUSE%"=="1" pause
exit /b 0

:fail
echo.
echo CAI DAT CHUA XONG. Doc thong bao loi o tren, sua roi chay lai setup.bat.
if not "%MUSIC_STUDIO_NO_PAUSE%"=="1" pause
exit /b 1