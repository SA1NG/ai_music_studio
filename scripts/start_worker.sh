#!/usr/bin/env bash
# Có thể chạy ở máy khác: API_URL=http://<ip-may-api>:8000 scripts/start_worker.sh
cd "$(dirname "$0")/.."
PYTHONUTF8=1 exec .venv/bin/python -m worker.worker
