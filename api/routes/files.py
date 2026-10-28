from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..deps import get_storage
from ..storage import Storage, StorageError

router = APIRouter(tags=["files"])


@router.get("/files/{path:path}")
def get_file(path: str, storage: Storage = Depends(get_storage)):
    """Phục vụ file trong storage. Chặn path traversal: mọi đường dẫn phải nằm trong storage root."""
    try:
        target = storage.resolve(path)
    except StorageError as e:
        raise HTTPException(400, str(e))
    if not target.is_file():
        raise HTTPException(404, "Không tìm thấy file")
    return FileResponse(target, filename=target.name)
