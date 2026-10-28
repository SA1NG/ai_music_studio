"""Danh sách model khả dụng: tổng hợp từ các worker đã đăng ký (không viết cứng)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from shared.config import Settings
from shared.schemas import ModelInfo, ModelList

from ..db import Database
from ..deps import get_db, get_settings_dep

router = APIRouter(tags=["models"])


def collect_models(db: Database, settings: Settings) -> ModelList:
    workers = db.list_workers(settings.worker_online_seconds)
    online = [w for w in workers if w["online"]]
    # Không worker nào online -> vẫn dùng danh sách báo cáo gần nhất, để người dùng
    # có thể xếp job vào hàng đợi trong lúc worker tắt.
    source = online or workers
    dit: dict[str, ModelInfo] = {}
    lm: dict[str, ModelInfo] = {}
    by_type: dict[str, set[str]] = {}
    warnings = list(dict.fromkeys(wn for w in online for wn in (w.get("info") or {}).get("warnings", [])))
    for w in source:
        for m in w["dit_models"]:
            dit.setdefault(m["name"], ModelInfo(**m))
        for m in w["lm_models"]:
            lm.setdefault(m["name"], ModelInfo(**m))
        for job_type, name in (w.get("backends") or {}).items():
            by_type.setdefault(job_type, set()).add(name)
    return ModelList(dit_models=list(dit.values()), lm_models=list(lm.values()),
                     backends=sorted(by_type.get("generate", set())),
                     backends_by_type={k: sorted(v) for k, v in sorted(by_type.items())},
                     workers_online=len(online), warnings=warnings)


@router.get("/models", response_model=ModelList)
def list_models(db: Database = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    return collect_models(db, settings)
