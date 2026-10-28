"""Deletion shared by upload and dataset routes. Call under engine.lock for job guards."""
from __future__ import annotations

import threading

from fastapi import HTTPException

from .storage import StorageError

data_lock = threading.RLock()


def require_unused(db, path, *, track_id=None, dataset_id=None):
    for job in db.active_jobs():
        payload = job["payload"]
        paths = [payload.get(key) for key in ("file", "dataset_file", "source_audio", "reference_audio")]
        if path in paths or (track_id is not None and payload.get("track_id") == track_id) or (
            dataset_id is not None and payload.get("dataset_id") == dataset_id
        ):
            raise HTTPException(409, f"File đang được tác vụ #{job['id']} sử dụng hoặc chờ xử lý. Hãy đợi hoàn tất rồi xóa.")


def remove_file(storage, path, category):
    target = storage.resolve(path)
    relative = target.relative_to(storage.root)
    if len(relative.parts) != 2 or relative.parts[0] != category or target.is_dir() or (storage.root / path).is_symlink():
        raise StorageError(f"Chỉ xóa file trong thư mục {category}")
    target.unlink(missing_ok=True)


def remove_cache(settings, path):
    warnings = []
    data = settings.data_dir.resolve()
    cache = (data / "ui_cache").resolve()
    target = (cache / path.replace("/", "__")).resolve()
    if not cache.is_relative_to(data) or not target.is_relative_to(cache):
        return ["Bỏ qua cache có đường dẫn nằm ngoài thư mục dữ liệu."]
    try:
        if target.is_file():
            target.unlink()
    except OSError:
        warnings.append("Bản gốc đã xóa; cache đang được mở nên chưa xóa được.")
    return warnings


def remove_track(db, storage, settings, track):
    require_unused(db, track["path"], track_id=track["id"])
    remove_file(storage, track["path"], "uploads")
    db.delete_track(track["id"])
    return remove_cache(settings, track["path"])
