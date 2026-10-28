"""Flat, immutable dataset folders shared by the local API and worker."""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path


def flat_name(name):
    if not name or name in (".", "..") or Path(name).name != name or "/" in name or "\\" in name or ":" in name:
        raise ValueError("Dataset chứa tên file không hợp lệ")
    return name


def link_or_copy(source, target):
    if source.is_symlink() or not source.is_file():
        raise ValueError("Dataset chỉ nhận file thông thường")
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def read_manifest(path):
    if path.is_dir():
        manifest = path / "manifest.json"
        if manifest.is_symlink():
            raise ValueError("Manifest không được là liên kết ngoài")
        return json.loads(manifest.read_text(encoding="utf-8"))
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Dataset ZIP bị hỏng")
        return json.loads(archive.read("manifest.json"))


def copy_dataset(source, target, skip_manifest=False):
    target.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        for item in source.iterdir():
            name = flat_name(item.name)
            if skip_manifest and name == "manifest.json":
                continue
            link_or_copy(item, target / name)
    else:
        with zipfile.ZipFile(source) as archive:
            for item in archive.infolist():
                name = flat_name(item.filename)
                if item.is_dir() or ((item.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError("Dataset ZIP không được chứa thư mục hoặc symlink")
                if skip_manifest and name == "manifest.json":
                    continue
                with archive.open(item) as stream, (target / name).open("wb") as output:
                    shutil.copyfileobj(stream, output)


def validate_directory(path):
    for item in path.iterdir():
        flat_name(item.name)
        if item.is_symlink() or not item.is_file():
            raise ValueError("Dataset chứa liên kết hoặc thư mục không hợp lệ")
    manifest = read_manifest(path)
    for track in manifest["tracks"]:
        audio = path / flat_name(track["audio"])
        if audio.is_symlink() or not audio.is_file() or audio.stat().st_size == 0:
            raise ValueError("Dataset thiếu audio hợp lệ")
        stem = audio.stem
        for name in (f"{stem}.caption.txt", f"{stem}.json"):
            item = path / name
            if item.is_symlink() or not item.is_file():
                raise ValueError("Dataset thiếu nhãn")
        json.loads((path / f"{stem}.json").read_text(encoding="utf-8"))
        if not track.get("is_instrumental", True) and not (path / f"{stem}.lyrics.txt").is_file():
            raise ValueError("Dataset thiếu lời hát")
    return manifest


def local_dataset(payload, settings):
    root = (settings.storage_dir / "datasets").resolve()
    path = (settings.storage_dir / payload["dataset_file"]).resolve()
    if path == root or not path.is_relative_to(root) or not path.is_dir():
        raise ValueError("Không tìm thấy thư mục dataset trên máy này")
    validate_directory(path)
    return path


class FolderWriter:
    def __init__(self, path):
        self.path = path
        path.mkdir(parents=True, exist_ok=False)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            path = self.path.resolve()
            if path.parent.name == "datasets" and path.name.startswith(".building_") and not self.path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
        return False

    def write(self, source, name):
        link_or_copy(source, self.path / flat_name(name))

    def writestr(self, name, content):
        if isinstance(content, str):
            content = content.encode("utf-8")
        (self.path / flat_name(name)).write_bytes(content)
