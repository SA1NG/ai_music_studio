"""Nội dung một gói phát hành, dùng chung cho make_release.py (đóng gói) và update.py (cài đặt).

Chỉ những đường dẫn liệt kê ở đây là "code do gói quản lý": khi cập nhật, thư mục trong CODE_DIRS bị
thay toàn bộ và file trong ROOT_FILES bị ghi đè. Mọi thứ khác trên máy admin được giữ nguyên:
.env, .venv, data/ (hoặc DATA_DIR), backups/, extractor Essentia đã tải.
"""
from __future__ import annotations

CODE_DIRS = ("api", "worker", "ui", "shared", "scripts", "prompt_refiner", "tests")
ROOT_FILES = (
    "VERSION", "CHANGELOG.md", "Music Studio.bat", "Cap nhat Music Studio.bat", "Khoi phuc ban truoc.bat",
    "requirements.txt", "requirements-optional.txt", "requirements.lock",
    ".env.dev", ".env.gpu.example", ".gitignore",
    "README.md", "HUONG_DAN.md", "HE_THONG_MUSIC_STUDIO.md", "DEPLOY.md",
    "tools/essentia/README.md",
)
IGNORED = ("__pycache__", "*.pyc", ".pytest_cache")
