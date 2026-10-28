#!/usr/bin/env bash
cd "$(dirname "$0")/.."
PYTHONUTF8=1 exec .venv/bin/python -m api.main
