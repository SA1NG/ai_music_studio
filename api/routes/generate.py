from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from shared.audio import AUDIO_EXTS, audio_duration
from shared.config import Settings
from shared.schemas import GenerateRequest, Generation, JobCreated, RatingRequest

from ..db import Database
from ..deps import get_db, get_settings_dep, get_storage, not_found
from ..engine_control import EngineControl, engine_ready
from ..storage import Storage, StorageError, safe_name
from .models import collect_models

router = APIRouter(tags=["generate"])


@router.post("/generate/audio")
def upload_generation_audio(file: UploadFile = File(...), storage: Storage = Depends(get_storage)):
    if Path(file.filename or "").suffix.lower() not in AUDIO_EXTS:
        raise HTTPException(400, "Hãy chọn file audio được hỗ trợ")
    target = storage.save_stream("uploads", f"{uuid.uuid4().hex}_{safe_name(file.filename or 'audio.wav')}", file.file)
    return {"path": storage.rel(target), "duration": audio_duration(target)}


@router.post("/generate", response_model=JobCreated)
def create_generate_job(req: GenerateRequest, db: Database = Depends(get_db),
                        settings: Settings = Depends(get_settings_dep),
                        engine: EngineControl = Depends(engine_ready),
                        storage: Storage = Depends(get_storage)):
    if engine.state["managed"] and req.dit_model != engine.state["dit"]:
        raise HTTPException(409, "Chọn cấu hình và bấm Áp dụng trước khi dùng model gốc khác.")
    models = collect_models(db, settings)
    known_dit = {m.name for m in models.dit_models}
    known_lm = {m.name for m in models.lm_models}
    if known_dit and req.dit_model not in known_dit:
        raise HTTPException(400, f"DiT model '{req.dit_model}' không có trong danh sách khả dụng")
    if known_lm and req.lm_model not in known_lm:
        raise HTTPException(400, f"LM model '{req.lm_model}' không có trong danh sách khả dụng")

    payload = req.model_dump()
    for field in ("source_audio", "reference_audio"):
        if payload.get(field):
            try:
                path = storage.resolve(payload[field])
                if not any(path.is_relative_to(storage.root / folder) for folder in ("uploads", "outputs")):
                    raise StorageError("Audio phải thuộc uploads hoặc outputs")
                if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
                    raise StorageError("Không tìm thấy audio hợp lệ")
                payload[field] = storage.rel(path)
            except StorageError as exc:
                raise HTTPException(400, str(exc)) from exc
    if req.task_type != "text2music":
        seconds = audio_duration(storage.resolve(payload["source_audio"]))
        if seconds is None or not 10 <= seconds <= 600:
            raise HTTPException(400, "Audio nguồn phải đọc được và dài từ 10 đến 600 giây")
        payload["duration"] = seconds
        if req.task_type == "repaint" and req.repainting_end > seconds + 0.05:
            raise HTTPException(400, f"Khoảng sửa vượt thời lượng audio ({seconds:.1f} giây)")
    payload["prompt_original"] = req.prompt_original or req.prompt
    if req.lora_id is not None:
        lora = db.get_lora(req.lora_id)
        if not lora:
            raise not_found("LoRA", req.lora_id)
        if lora["base_model"] != req.dit_model:
            raise HTTPException(400, f"LoRA '{lora['name']}' được train trên '{lora['base_model']}', "
                                     f"không dùng được với '{req.dit_model}'")
        payload["lora"] = {"id": lora["id"], "name": lora["name"], "path": lora["path"],
                           "base_model": lora["base_model"], "source": lora.get("source", "mock")}
    return JobCreated(job_id=db.create_job("generate", payload))


@router.get("/generations", response_model=list[Generation])
def list_generations(limit: int = 50, db: Database = Depends(get_db)):
    return db.list_generations(min(max(limit, 1), 500))


@router.get("/generations/{gen_id}", response_model=Generation)
def get_generation(gen_id: int, db: Database = Depends(get_db)):
    gen = db.get_generation(gen_id)
    if not gen:
        raise not_found("generation", gen_id)
    return gen


@router.post("/generations/{gen_id}/rating", response_model=Generation)
def rate_generation(gen_id: int, req: RatingRequest, db: Database = Depends(get_db)):
    if not db.get_generation(gen_id):
        raise not_found("generation", gen_id)
    db.set_generation_rating(gen_id, req.rating)
    return db.get_generation(gen_id)
