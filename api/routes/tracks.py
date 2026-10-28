from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from shared.audio import AUDIO_EXTS, audio_duration
from shared.config import Settings
from shared.schemas import AutoLabelRequest, JobCreated, Track, TrackUpdate

from ..data_cleanup import data_lock, remove_track
from ..db import Database
from ..deps import get_db, get_settings_dep, get_storage, not_found
from ..engine_control import EngineControl, engine_ready, get_engine
from ..storage import Storage, StorageError, safe_name

router = APIRouter(tags=["tracks"])


def _enqueue_autolabel(db: Database, track: dict, options: dict | None = None) -> int:
    return db.create_job("autolabel", {"track_id": track["id"], "file": track["path"],
                                       "filename": track["filename"], "is_instrumental": bool(track.get("is_instrumental", True)),
                                       "lyrics": track.get("lyrics", ""), "vocal_language": track.get("vocal_language", "unknown"),
                                       **(options or {})})


def require_ai_worker(db: Database, settings: Settings, engine: EngineControl):
    workers = [w for w in db.list_workers(settings.worker_online_seconds)
               if w["online"] and (w.get("backends") or {}).get("autolabel") == "ace"]
    if not workers:
        raise HTTPException(409, "Chưa có worker gán nhãn ACE-Step. Khởi động lại Music Studio với AUTOLABEL_BACKEND=ace.")
    lm_ready = (engine.state.get("lm") not in (None, "none")) if engine.state["managed"] else any(
        m["name"] != "none" for w in workers for m in w.get("lm_models", []))
    if not lm_ready:
        raise HTTPException(409, "Gán nhãn AI cần DiT và LM. Vào Tạo nhạc → Model, chọn LM rồi bấm Áp dụng; hoặc chọn nhập nhãn thủ công.")


def upload_ready(autolabel: bool = True, engine: EngineControl = Depends(get_engine)):
    if autolabel:
        yield from engine_ready(engine)
    else:
        yield engine


@router.post("/tracks", response_model=list[Track], dependencies=[Depends(upload_ready)])
def upload_tracks(files: list[UploadFile] = File(...), autolabel: bool = True,
                  require_ai: bool = False, is_instrumental: bool = True,
                  db: Database = Depends(get_db), storage: Storage = Depends(get_storage),
                  settings: Settings = Depends(get_settings_dep), engine: EngineControl = Depends(get_engine)):
    """Upload nhiều file nhạc tham chiếu. autolabel=true -> tự xếp job gợi ý nhãn cho mỗi file."""
    bad = [f.filename for f in files if Path(f.filename or "").suffix.lower() not in AUDIO_EXTS]
    if bad:
        raise HTTPException(400, f"Định dạng không hỗ trợ: {bad}. Chấp nhận: {sorted(AUDIO_EXTS)}")
    if autolabel and require_ai:
        require_ai_worker(db, settings, engine)
    created = []
    for f in files:
        name = safe_name(f.filename or "track.wav", "track.wav")
        target = storage.save_stream("uploads", f"{uuid.uuid4().hex[:8]}_{name}", f.file)
        track_id = db.create_track(f.filename or name, storage.rel(target), audio_duration(target))
        if not is_instrumental:
            db.update_track(track_id, {"is_instrumental": False})
        track = db.get_track(track_id)
        if autolabel:
            track["autolabel_job_id"] = _enqueue_autolabel(db, track, {"require_ai": require_ai, "transcribe_lyrics": not is_instrumental})
        created.append(track)
    return created


@router.get("/tracks", response_model=list[Track])
def list_tracks(review_status: str | None = None, db: Database = Depends(get_db)):
    return db.list_tracks(review_status)


@router.get("/tracks/{track_id}", response_model=Track)
def get_track(track_id: int, db: Database = Depends(get_db)):
    track = db.get_track(track_id)
    if not track:
        raise not_found("track", track_id)
    return track


@router.put("/tracks/{track_id}", response_model=Track)
def update_track(track_id: int, req: TrackUpdate, db: Database = Depends(get_db)):
    if not db.get_track(track_id):
        raise not_found("track", track_id)
    db.update_track(track_id, req.model_dump(exclude_unset=True))
    return db.get_track(track_id)


@router.post("/tracks/{track_id}/autolabel", response_model=JobCreated, dependencies=[Depends(engine_ready)])
def autolabel_track(track_id: int, req: AutoLabelRequest = AutoLabelRequest(), db: Database = Depends(get_db),
                    settings: Settings = Depends(get_settings_dep), engine: EngineControl = Depends(get_engine)):
    track = db.get_track(track_id)
    if not track:
        raise not_found("track", track_id)
    if req.require_ai:
        require_ai_worker(db, settings, engine)
    return JobCreated(job_id=_enqueue_autolabel(db, track, req.model_dump(exclude_none=True)))


@router.delete("/tracks/{track_id}")
def delete_track(track_id: int, db: Database = Depends(get_db), storage: Storage = Depends(get_storage),
                 settings: Settings = Depends(get_settings_dep), engine: EngineControl = Depends(get_engine)):
    with data_lock, engine.lock:
        track = db.get_track(track_id)
        if track is None:
            raise not_found("track", track_id)
        try:
            warnings = remove_track(db, storage, settings, track)
        except (StorageError, OSError) as exc:
            raise HTTPException(409, f"Chưa xóa được bài: {exc}") from exc
        return {"deleted_id": track_id, "warnings": warnings}
