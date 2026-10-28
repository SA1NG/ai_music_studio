from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from shared.config import Settings
from shared.dataset_files import read_manifest, validate_directory
from shared.schemas import Dataset, DatasetCreate

from ..data_cleanup import data_lock, remove_cache, remove_track, require_unused
from ..db import Database
from ..deps import get_db, get_settings_dep, get_storage, not_found
from ..engine_control import EngineControl, get_engine
from ..storage import Storage, StorageError, safe_name

router = APIRouter(tags=["datasets"])


@router.post("/datasets", response_model=Dataset)
def create_dataset(req: DatasetCreate, db: Database = Depends(get_db),
                   storage: Storage = Depends(get_storage), settings: Settings = Depends(get_settings_dep),
                   engine: EngineControl = Depends(get_engine)):
    """Tạo dataset tuần tự (data_lock) để hai lần bấm cùng lúc không ghi đè cùng một phiên bản."""
    with data_lock:
        result = _create_dataset(req, db, storage)
        source_track_ids = result.pop("_source_track_ids")
        result["removed_track_ids"], result["cleanup_warnings"] = [], []
        if req.cleanup_sources:
            with engine.lock:
                for track_id in source_track_ids:
                    track = db.get_track(track_id)
                    if track is None:
                        continue
                    try:
                        warnings = remove_track(db, storage, settings, track)
                        result["removed_track_ids"].append(track_id)
                        result["cleanup_warnings"].extend(warnings)
                    except (HTTPException, StorageError, OSError) as exc:
                        result["cleanup_warnings"].append(f"Chưa dọn bài #{track_id}: {getattr(exc, 'detail', str(exc))}")
        return result


def _create_dataset(req: DatasetCreate, db: Database, storage: Storage):
    base = None
    base_path = None
    old_ids = []
    name = req.name.strip()
    if req.base_dataset_id is not None:
        base = db.get_dataset(req.base_dataset_id)
        if not base:
            raise not_found("dataset", req.base_dataset_id)
        name = base["name"]
        if base["version"] != db.latest_active_dataset_version(name):
            raise HTTPException(409, "Dataset đã có phiên bản mới hơn. Làm mới và chọn phiên bản mới nhất để thêm nhạc.")
        old_ids = list(dict.fromkeys(base["track_ids"]))
        base_path = storage.resolve(base["export_path"])
        if not base_path.exists():
            raise HTTPException(410, "Dataset gốc không còn trên đĩa. Khôi phục dữ liệu trước khi thêm nhạc.")
        try:
            manifest = validate_directory(base_path) if base_path.is_dir() else read_manifest(base_path)
            if [t["track_id"] for t in manifest["tracks"]] != old_ids:
                raise ValueError("Danh sách bài không khớp dataset")
        except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise HTTPException(400, "Không đọc được dataset gốc: " + str(exc)) from exc
    approved = {t["id"]: t for t in db.list_tracks("approved")}
    if req.track_ids is not None:
        new_ids = [i for i in dict.fromkeys(req.track_ids) if i not in old_ids]
        not_ok = [i for i in new_ids if i not in approved]
        if not_ok:
            raise HTTPException(400, f"Các track chưa được duyệt hoặc không tồn tại: {not_ok}")
        tracks = [approved[i] for i in new_ids]
    else:
        tracks = [t for key, t in approved.items() if key not in old_ids]
    if not tracks:
        raise HTTPException(400, "Các bài đã có trong dataset; hãy chọn thêm bài mới." if base else "Chưa có track nào được duyệt (approved)")
    no_caption = [t["id"] for t in tracks if not (t.get("caption") or "").strip()]
    if no_caption:
        # ACE-Step chỉ tiền xử lý/train mẫu có caption
        raise HTTPException(400, f"Các track đã duyệt nhưng chưa có caption: {no_caption}")
    no_lyrics = [t["id"] for t in tracks if not t.get("is_instrumental", True) and not (t.get("lyrics") or "").strip()]
    if no_lyrics:
        raise HTTPException(400, f"Các bài có lời chưa được nhập lời hát: {no_lyrics}")
    version = db.next_dataset_version(name)
    export_path = storage.export_dataset(name, version, tracks, base=base_path, directory=True)
    try:
        ds_id = db.create_dataset(name, version, old_ids + [t["id"] for t in tracks], storage.rel(export_path))
    except Exception:
        storage.remove_dataset(storage.rel(export_path))
        raise
    result = db.get_dataset(ds_id)
    result["_source_track_ids"] = [t["id"] for t in tracks]
    return result


@router.get("/datasets", response_model=list[Dataset])
def list_datasets(db: Database = Depends(get_db)):
    return db.list_datasets()


@router.get("/datasets/{dataset_id}/download")
def download_dataset(dataset_id: int, db: Database = Depends(get_db),
                     storage: Storage = Depends(get_storage)):
    ds = db.get_dataset(dataset_id)
    if not ds:
        raise not_found("dataset", dataset_id)
    path = storage.resolve(ds["export_path"])
    if not path.exists():
        raise HTTPException(410, "Dataset đã bị xóa khỏi storage")
    if path.is_file():
        return FileResponse(path, media_type="application/zip", filename=path.name)
    with data_lock:
        try:
            validate_directory(path)
            temporary = tempfile.TemporaryDirectory(prefix="music-studio-export-")
            archive_path = Path(temporary.name) / f"{safe_name(ds['name'])}_v{ds['version']}.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in path.iterdir():
                    archive.write(item, item.name)
            return FileResponse(archive_path, media_type="application/zip", filename=archive_path.name,
                                background=BackgroundTask(temporary.cleanup))
        except (ValueError, OSError, KeyError) as exc:
            if 'temporary' in locals():
                temporary.cleanup()
            raise HTTPException(400, str(exc)) from exc


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: int, db: Database = Depends(get_db), storage: Storage = Depends(get_storage),
                   settings: Settings = Depends(get_settings_dep), engine: EngineControl = Depends(get_engine)):
    with data_lock, engine.lock:
        ds = db.get_dataset(dataset_id)
        if ds is None:
            raise not_found("dataset", dataset_id)
        require_unused(db, ds["export_path"], dataset_id=dataset_id)
        try:
            storage.remove_dataset(ds["export_path"])
        except (StorageError, OSError) as exc:
            raise HTTPException(409, f"Chưa xóa được dataset: {exc}") from exc
        db.mark_dataset_deleted(dataset_id)
        warnings = remove_cache(settings, ds["export_path"]) + remove_cache(settings, ds["export_path"] + ".zip")
        return {"deleted_id": dataset_id, "warnings": warnings}
