"""Phiên bản ứng dụng, đọc từ file VERSION ở thư mục gốc (gói phát hành ghi file này)."""
from __future__ import annotations

from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def app_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"
