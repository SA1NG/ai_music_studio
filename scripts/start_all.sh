#!/usr/bin/env bash
# Chạy API + worker + UI (mỗi cái một tiến trình). Ctrl+C tắt cả ba.
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/run_all.py
