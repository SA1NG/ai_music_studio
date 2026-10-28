#!/usr/bin/env bash
# Xóa dữ liệu mock (hỏi xác nhận trước). Tham số: --dry-run, --yes
cd "$(dirname "$0")/.."
PYTHONUTF8=1 exec .venv/bin/python scripts/clean_mock_data.py "$@"
