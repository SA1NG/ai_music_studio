from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from shared.config import Settings
from shared.schemas import JobCreated, Lora, TrainRequest

from ..db import Database
from ..deps import get_db, get_settings_dep, get_storage, not_found
from ..engine_control import EngineControl, engine_ready
from ..storage import Storage
from .models import collect_models

router = APIRouter(tags=["train"])


@router.post("/train", response_model=JobCreated)
def create_train_job(req: TrainRequest, db: Database = Depends(get_db),
                     settings: Settings = Depends(get_settings_dep),
                     engine: EngineControl = Depends(engine_ready), storage: Storage = Depends(get_storage)):
    if engine.state["managed"] and req.base_model != engine.state["dit"]:
        raise HTTPException(409, "Base model chưa được áp dụng cho engine.")
    ds = db.get_dataset(req.dataset_id)
    if not ds:
        raise not_found("dataset", req.dataset_id)
    known = {m.name for m in collect_models(db, settings).dit_models}
    if known and req.base_model not in known:
        raise HTTPException(400, f"base_model '{req.base_model}' không có trong danh sách khả dụng")
    name = req.name or f"{req.style_name}-{req.base_model}-{ds['name']}-v{ds['version']}"
    payload = {
        "dataset_id": ds["id"], "dataset_name": ds["name"], "dataset_version": ds["version"],
        "dataset_file": ds["export_path"], "base_model": req.base_model,
        "style_name": req.style_name, "name": name, "params": req.params.model_dump(),
    }
    if storage.resolve(ds["export_path"]).is_dir():
        payload["dataset_format"] = "directory"
    return JobCreated(job_id=db.create_job("train", payload))


@router.get("/loras", response_model=list[Lora])
def list_loras(base_model: Optional[str] = None, style_name: Optional[str] = None,
               db: Database = Depends(get_db)):
    loras = db.list_loras(base_model)
    if style_name:
        loras = [x for x in loras if x["style_name"] == style_name]
    return loras
