"""Explicit, idle-only engine restarts requested by the admin."""
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .db import Database
from .deps import get_db

router = APIRouter(prefix="/engine", tags=["engine"])


class EngineControl:
    def __init__(self):
        self.lock = threading.Lock()
        self.owner = None
        self.claimed = True
        self.state = dict(managed=False, status="unmanaged", dit_models=[], lm_models=["none"],
                          dit=None, lm="none", revision=0, error=None)


def get_engine(request: Request) -> EngineControl:
    return request.app.state.engine


def engine_ready(engine: EngineControl = Depends(get_engine)):
    with engine.lock:
        if engine.state["managed"] and engine.state["status"] != "ready":
            raise HTTPException(409, "Engine chưa sẵn sàng. Hãy chờ nạp xong hoặc áp dụng lại cấu hình.")
        yield engine


class Selection(BaseModel):
    dit: str
    lm: str = "none"


class Owner(BaseModel):
    owner: str


class Setup(Selection, Owner):
    dit_models: list[str]
    lm_models: list[str]


class Result(Owner):
    revision: int
    status: Literal["ready", "error"]
    error: str | None = None


def verify_owner(engine, owner):
    if engine.owner is None or engine.owner != owner:
        raise HTTPException(403, "Bộ điều khiển không sở hữu engine này.")


@router.get("")
def info(engine: EngineControl = Depends(get_engine)):
    with engine.lock:
        return dict(engine.state)


@router.post("/setup")
def setup(req: Setup, engine: EngineControl = Depends(get_engine)):
    with engine.lock:
        if engine.state["managed"]:
            raise HTTPException(409, "Engine đã có bộ điều khiển.")
        if req.dit not in req.dit_models or req.lm not in req.lm_models:
            raise HTTPException(400, "Cấu hình khởi động không hợp lệ.")
        engine.owner = req.owner
        engine.state.update(managed=True, status="idle", dit_models=req.dit_models,
                            lm_models=req.lm_models, dit=req.dit, lm=req.lm, revision=1)
        return dict(engine.state)


@router.post("/apply")
def apply(req: Selection, engine: EngineControl = Depends(get_engine), db: Database = Depends(get_db)):
    with engine.lock:
        state = engine.state
        if not state["managed"]:
            raise HTTPException(409, "Mở bằng Music Studio.bat để áp dụng cấu hình engine.")
        if state["status"] == "loading":
            raise HTTPException(409, "Engine đang nạp. Vui lòng chờ.")
        if req.dit not in state["dit_models"] or req.lm not in state["lm_models"]:
            raise HTTPException(400, "Model chưa được tải đầy đủ trên máy.")
        jobs = db.active_jobs()
        if jobs:
            # On first load, compatible queued work from a previous session must be able to resume.
            compatible = state["status"] == "idle" and all(
                job["status"] == "queued" and
                (job["type"] == "autolabel" or
                 (job["payload"].get("dit_model", job["payload"].get("base_model")) == req.dit and
                  job["payload"].get("lm_model", "none") in ("none", req.lm)))
                for job in jobs)
            if not compatible:
                raise HTTPException(409, "Còn tác vụ đang chạy hoặc chờ. Đợi hoàn tất; nếu vừa mở ứng dụng, hãy nạp đúng DiT/LM của các job đang chờ.")
        if state["status"] == "ready" and (req.dit, req.lm) == (state["dit"], state["lm"]):
            return dict(state)
        state.update(status="loading", dit=req.dit, lm=req.lm, revision=state["revision"] + 1, error=None)
        engine.claimed = False
        return dict(state)


@router.post("/poll")
def poll(req: Owner, engine: EngineControl = Depends(get_engine)):
    with engine.lock:
        verify_owner(engine, req.owner)
        if engine.claimed or engine.state["status"] != "loading":
            return None
        engine.claimed = True
        return dict(engine.state)


@router.post("/result")
def result(req: Result, engine: EngineControl = Depends(get_engine)):
    with engine.lock:
        verify_owner(engine, req.owner)
        if req.revision != engine.state["revision"] or engine.state["status"] != "loading":
            raise HTTPException(409, "Cấu hình đã thay đổi.")
        engine.state.update(status=req.status, error=req.error)
        return dict(engine.state)
