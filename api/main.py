"""API FastAPI: nguồn sự thật duy nhất (DB, file, hàng đợi job).

Chạy: python -m api.main   (hoặc scripts/start_api)
Docs: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI

from prompt_refiner import get_refiner
from shared.config import Settings, get_settings
from shared.version import app_version

from .db import Database
from .deps import get_db, get_settings_dep, verify_token
from .engine_control import EngineControl, router as engine_router
from .routes import datasets, files, generate, jobs, models, prompt, tracks, train, worker
from .storage import Storage

system_router = APIRouter(tags=["system"])


def health():
    return {"ok": True}


@system_router.get("/system")
def system_info(db: Database = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    """Thông tin cho tab Hệ thống: worker, backend, thống kê hàng đợi."""
    workers = db.list_workers(settings.worker_online_seconds)
    return {"version": app_version(), "workers": workers, "queue": db.job_counts(),
            "prompt_refiner": settings.prompt_refiner,
            "auth_enabled": bool(settings.api_token)}


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Music Studio API", version=app_version(),
                  description="Tạo nhạc có lời/không lời, Remix, Sửa đoạn và huấn luyện LoRA.")
    app.state.settings = settings
    app.state.engine = EngineControl()
    app.state.db = Database(settings.db_path)
    app.state.storage = Storage(settings.storage_dir)
    app.state.refiner = get_refiner(settings)

    app.add_api_route("/health", health, tags=["system"])  # công khai, để script kiểm tra API đã lên
    protected = [Depends(verify_token)]
    app.include_router(system_router, dependencies=protected)
    app.include_router(engine_router, dependencies=protected)
    for r in (models, prompt, generate, jobs, tracks, datasets, train, files, worker):
        app.include_router(r.router, dependencies=protected)
    return app


class _QuietPolling(logging.Filter):
    """Bỏ access log của các request poll định kỳ (worker claim/heartbeat, UI hỏi tiến độ job,
    tab Hệ thống tự làm mới)."""
    NOISY = ("/worker/claim", "/worker/heartbeat", "/worker/jobs/", "/jobs", "/system", "/models")

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:  # (client, method, path, http_version, status)
            path, status = str(args[2]), int(args[4])
            if status < 400 and path.startswith(self.NOISY):
                return False
        return True


def main() -> None:
    import uvicorn
    logging.getLogger("uvicorn.access").addFilter(_QuietPolling())
    s = get_settings()
    uvicorn.run(create_app(s), host=s.api_host, port=s.api_port, log_level="info")


if __name__ == "__main__":
    main()
