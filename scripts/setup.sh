#!/usr/bin/env bash
# Tạo venv riêng cho dự án, cài thư viện, tạo .env từ .env.dev nếu chưa có.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2]==(3,11) else 1)'; then
      PY="$c"; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "Không tìm thấy Python 3.11. Cài Python 3.11 hoặc đặt PYTHON=/đường/dẫn/python3.11" >&2
  exit 1
fi

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
if [ "${WITH_OPTIONAL:-0}" = "1" ]; then
  .venv/bin/python -m pip install -r requirements-optional.txt || echo "Cài thư viện tùy chọn thất bại — các tính năng tương ứng sẽ tự tắt."
fi
[ -f .env ] || { cp .env.dev .env; echo "Đã tạo .env từ .env.dev"; }
echo "Xong. Chạy: scripts/start_all.sh"
