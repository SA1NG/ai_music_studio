"""Endpoint cho worker. Worker không truy cập DB: nhận job, báo tiến độ và gửi kết quả đều qua đây."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from shared.config import Settings
from shared.schemas import ClaimRequest, FailRequest, Job, ProgressUpdate, WorkerRegister

from ..db import Database
from ..deps import get_db, get_settings_dep, get_storage, not_found
from ..storage import Storage, safe_name
from ..engine_control import EngineControl, get_engine

router = APIRouter(prefix="/worker", tags=["worker"])


@router.post("/register")
def register(req: WorkerRegister, db: Database = Depends(get_db)):
    db.upsert_worker(req.model_dump(exclude={"fresh_start"}))
    # Cùng worker_id khởi động lại -> job nó đang giữ chắc chắn đã mất, đưa lại hàng đợi.
    # Cập nhật định kỳ (fresh_start=False) thì không đụng tới job đang chạy.
    requeued = db.requeue_worker_jobs(req.worker_id) if req.fresh_start else 0
    return {"ok": True, "requeued": requeued}


@router.post("/heartbeat")
def heartbeat(req: ClaimRequest, db: Database = Depends(get_db)):
    db.touch_worker(req.worker_id)
    return {"ok": True}


@router.post("/claim", response_model=Job, responses={204: {"description": "Không có job"}})
def claim(req: ClaimRequest, db: Database = Depends(get_db),
          settings: Settings = Depends(get_settings_dep), engine: EngineControl = Depends(get_engine)):
    db.touch_worker(req.worker_id)
    db.requeue_stale_jobs(settings.job_stale_seconds)
    with engine.lock:
        if engine.state["managed"] and engine.state["status"] != "ready":
            return Response(status_code=204)
        job = db.claim_job(req.worker_id, list(req.types))
    if job is None:
        return Response(status_code=204)
    return job


def _running_job(db: Database, job_id: int) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise not_found("job", job_id)
    if job["status"] != "running":
        raise HTTPException(409, f"Job {job_id} đang ở trạng thái '{job['status']}', không phải 'running'")
    return job


@router.post("/jobs/{job_id}/progress")
def progress(job_id: int, req: ProgressUpdate, db: Database = Depends(get_db)):
    job = _running_job(db, job_id)
    db.update_job_progress(job_id, req.progress, req.log)
    if job["worker_id"]:
        db.touch_worker(job["worker_id"])
    return {"ok": True}


@router.post("/jobs/{job_id}/fail")
def fail(job_id: int, req: FailRequest, db: Database = Depends(get_db)):
    _running_job(db, job_id)
    db.finish_job(job_id, "failed", error=req.error)
    return {"ok": True}


@router.post("/jobs/{job_id}/release")
def release(job_id: int, db: Database = Depends(get_db)):
    """Worker tắt có kiểm soát (Ctrl+C) -> trả job về hàng đợi ngay, không chờ timeout."""
    _running_job(db, job_id)
    db.release_job(job_id)
    return {"ok": True}


@router.post("/jobs/{job_id}/complete", response_model=Job)
def complete(job_id: int, result: str = Form("{}"), files: list[UploadFile] = File(default=[]),
             db: Database = Depends(get_db), storage: Storage = Depends(get_storage)):
    """multipart: `result` (chuỗi JSON) + `files` (audio cho generate, .safetensors + metadata cho train)."""
    job = _running_job(db, job_id)
    try:
        res: dict[str, Any] = json.loads(result or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"result không phải JSON hợp lệ: {e}")
    handler = {"generate": _complete_generate, "autolabel": _complete_autolabel,
               "train": _complete_train}[job["type"]]
    final = handler(job, res, files, db, storage)
    db.finish_job(job_id, "done", result=final)
    return db.get_job(job_id)


def _save_files(files: list[UploadFile], storage: Storage, subdir: str) -> list[Path]:
    return [storage.save_stream(subdir, safe_name(f.filename or f"file{i}"), f.file)
            for i, f in enumerate(files)]


def _complete_generate(job, res, files, db: Database, storage: Storage) -> dict[str, Any]:
    if not files:
        raise HTTPException(400, "Job generate phải gửi kèm file audio")
    p = job["payload"]
    f = files[0]
    ext = Path(f.filename or "").suffix.lower() or ".wav"
    audio = storage.save_stream("outputs", f"gen_{job['id']:06d}{ext}", f.file)
    params = {
        "title": p.get("title") or "",
        "duration": p.get("duration"), "bpm": p.get("bpm"), "key_scale": p.get("key_scale"),
        "time_signature": p.get("time_signature"), "seed": res.get("seed", p.get("seed")),
        "seed_requested": p.get("seed"), "inference_steps": p.get("inference_steps"),
        "audio_format": p.get("audio_format"), "backend": res.get("backend"),
        "metas": res.get("metas", {}),
    }
    for key in ("task_type", "instrumental", "lyrics", "vocal_language", "source_audio", "reference_audio",
                "audio_cover_strength", "repainting_start", "repainting_end", "repaint_mode",
                "thinking", "use_cot_caption", "guidance_scale", "lm_temperature", "lm_negative_prompt"):
        if key in p:
            params[key] = p[key]
    gen_id = db.create_generation({
        "job_id": job["id"], "prompt_original": p.get("prompt_original") or p["prompt"],
        "prompt_final": p["prompt"],
        # Ghi model THỰC SỰ được dùng (backend báo lại), lùi về giá trị yêu cầu.
        "dit_model": res.get("dit_model") or p["dit_model"],
        "lm_model": res.get("lm_model") or p.get("lm_model") or "none",
        "lora_id": p.get("lora_id"), "lora_scale": p.get("lora_scale", 1.0),
        "params": params, "audio_path": storage.rel(audio),
        "backend": res.get("backend") or "mock", "seed": params["seed"],
    })
    return {**res, "generation_id": gen_id, "audio_path": storage.rel(audio)}


def _complete_autolabel(job, res, files, db: Database, storage: Storage) -> dict[str, Any]:
    track_id = job["payload"]["track_id"]
    labels = res.get("auto_labels") or {}
    track = db.save_track_auto_labels(track_id, labels)
    if not track:
        raise not_found("track", track_id)
    return {"track_id": track_id, "auto_labels": labels, "dataset_ready": track["review_status"] == "approved"}


def _complete_train(job, res, files, db: Database, storage: Storage) -> dict[str, Any]:
    p = job["payload"]
    saved = _save_files(files, storage, f"loras/lora_job{job['id']:06d}")
    weights = [s for s in saved if s.suffix == ".safetensors"]
    if not weights:
        raise HTTPException(400, "Job train phải gửi kèm file .safetensors")
    lora_id = db.create_lora({
        "name": p["name"], "style_name": p["style_name"],
        "base_model": res.get("base_model") or p["base_model"],
        "dataset_id": p["dataset_id"], "params": {**p.get("params", {}), **res.get("metrics", {})},
        "path": storage.rel(weights[0]), "source": res.get("backend") or "mock",
    })
    return {**res, "lora_id": lora_id, "files": [storage.rel(s) for s in saved]}
