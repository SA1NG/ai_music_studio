"""Kênh cập nhật online: đọc `latest.json` do scripts/make_release.py tạo và bạn đưa lên mạng.

latest.json:
    {"version": "1.0.1", "url": "https://…/MusicStudio-1.0.1.zip", "sha256": "…", "size": 221088,
     "notes": "…", "released_at": "…"}

Địa chỉ latest.json: biến UPDATE_URL trong .env, nếu trống thì lấy `update_url` trong release.json
của gói đã cài (make_release ghi sẵn). Cả hai trống = tắt kiểm tra cập nhật.
Tính toàn vẹn: gói tải về phải khớp SHA-256 trong latest.json; latest.json phải tải qua HTTPS.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from .version import VERSION_FILE, app_version

RELEASE_FILE = VERSION_FILE.parent / "release.json"
CHECK_CACHE_SECONDS = 3600

_cache: dict[str, tuple[float, Optional[dict]]] = {}


class UpdateError(RuntimeError):
    pass


def feed_url(configured: str = "") -> str:
    if configured:
        return configured
    try:
        return json.loads(RELEASE_FILE.read_text(encoding="utf-8")).get("update_url") or ""
    except (OSError, ValueError):
        return ""


def parse_version(value: str) -> Optional[tuple[int, ...]]:
    match = re.fullmatch(r"\s*v?(\d+(?:\.\d+)*)\s*", str(value or ""))
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return bool(new and old and new > old)


def _require_secure(url: str) -> None:
    address = urlparse(url)
    if address.scheme != "https" and address.hostname not in ("127.0.0.1", "localhost"):
        raise UpdateError("Địa chỉ cập nhật phải dùng https://")


def check_for_update(url: str, current: Optional[str] = None, timeout: float = 10.0,
                     use_cache: bool = False) -> Optional[dict]:
    """Trả manifest nếu có bản mới hơn bản đang cài, ngược lại None. Lỗi mạng -> UpdateError."""
    if not url:
        return None
    current = current or app_version()
    if use_cache and url in _cache and time.monotonic() - _cache[url][0] < CHECK_CACHE_SECONDS:
        manifest = _cache[url][1]
    else:
        _require_secure(url)
        try:
            response = httpx.get(url, timeout=timeout, follow_redirects=True,
                                 headers={"Cache-Control": "no-cache"})
            response.raise_for_status()
            manifest = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise UpdateError(f"Không kiểm tra được bản mới: {exc}") from exc
        if not isinstance(manifest, dict) or not all(manifest.get(k) for k in ("version", "url", "sha256")):
            raise UpdateError("latest.json thiếu version, url hoặc sha256")
        _cache[url] = (time.monotonic(), manifest)
    return manifest if manifest and is_newer(manifest["version"], current) else None


def download_update(manifest: dict, folder: Path,
                    progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """Tải gói về `folder`, kiểm tra SHA-256. File hỏng hoặc bị thay đổi thì xóa và báo lỗi."""
    url = manifest["url"]
    _require_secure(url)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"MusicStudio-{manifest['version']}.zip"
    partial = target.with_suffix(".download")
    digest, done = hashlib.sha256(), 0
    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or manifest.get("size") or 0)
            with partial.open("wb") as handle:
                for chunk in r.iter_bytes(1 << 20):
                    handle.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except httpx.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Tải gói cập nhật thất bại: {exc}") from exc
    if digest.hexdigest().lower() != str(manifest["sha256"]).lower():
        partial.unlink(missing_ok=True)
        raise UpdateError("Gói tải về không khớp SHA-256 trong latest.json, đã hủy. Báo cho người phát hành.")
    partial.replace(target)
    return target


def extract_package(archive: Path, folder: Path) -> Path:
    """Giải nén gói vào `folder` (xóa nội dung cũ), chặn đường dẫn thoát ra ngoài. Trả thư mục gốc của gói."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    root = folder.resolve()
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            destination = (root / name).resolve()
            if destination != root and not destination.is_relative_to(root):
                raise UpdateError(f"Gói chứa đường dẫn không hợp lệ: {name}")
        zf.extractall(root)
    packages = [p.parent for p in root.glob("*/release.json")] + ([root] if (root / "release.json").is_file() else [])
    if len(packages) != 1:
        raise UpdateError("Gói cập nhật không đúng cấu trúc (thiếu release.json).")
    return packages[0]
