from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException, Request

from shared.config import Settings

from .db import Database
from .storage import Storage


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def verify_token(request: Request, x_token: Optional[str] = Header(None)) -> None:
    """Nếu .env có API_TOKEN thì mọi request phải gửi header X-Token đúng."""
    expected = request.app.state.settings.api_token
    if expected and not hmac.compare_digest(x_token or "", expected):
        raise HTTPException(status_code=401, detail="X-Token không hợp lệ hoặc bị thiếu")


def not_found(what: str, obj_id: int | str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Không tìm thấy {what} {obj_id}")
