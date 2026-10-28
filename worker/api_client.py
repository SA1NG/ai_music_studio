"""Client HTTP của worker tới API. Đây là kênh DUY NHẤT worker dùng để nói chuyện với API."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx


class ApiClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 60.0,
                 client: Optional[httpx.Client] = None):
        """`client`: inject httpx.Client có sẵn (ví dụ FastAPI TestClient trong test)."""
        headers = {"X-Token": token} if token else {}
        if client is not None:
            client.headers.update(headers)
            self.http = client
        else:
            self.http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def close(self) -> None:
        self.http.close()

    def _post(self, url: str, **kw) -> httpx.Response:
        r = self.http.post(url, **kw)
        r.raise_for_status()
        return r

    def register(self, info: dict[str, Any]) -> dict[str, Any]:
        return self._post("/worker/register", json=info).json()

    def heartbeat(self, worker_id: str) -> None:
        self._post("/worker/heartbeat", json={"worker_id": worker_id})

    def claim(self, worker_id: str, types: list[str]) -> Optional[dict[str, Any]]:
        r = self._post("/worker/claim", json={"worker_id": worker_id, "types": types})
        return None if r.status_code == 204 else r.json()

    def progress(self, job_id: int, progress: float, log: Optional[str] = None) -> None:
        self._post(f"/worker/jobs/{job_id}/progress",
                   json={"progress": max(0.0, min(1.0, progress)), "log": log})

    def complete(self, job_id: int, result: dict[str, Any], files: list[Path]) -> dict[str, Any]:
        handles = [open(f, "rb") for f in files]
        try:
            multipart = [("files", (Path(f).name, h, "application/octet-stream"))
                         for f, h in zip(files, handles)]
            return self._post(f"/worker/jobs/{job_id}/complete",
                              data={"result": json.dumps(result, ensure_ascii=False)},
                              files=multipart or None).json()
        finally:
            for h in handles:
                h.close()

    def fail(self, job_id: int, error: str) -> None:
        self._post(f"/worker/jobs/{job_id}/fail", json={"error": error[:4000]})

    def release(self, job_id: int) -> None:
        self._post(f"/worker/jobs/{job_id}/release")

    def download(self, rel_path: str, dest: Path) -> Path:
        with self.http.stream("GET", f"/files/{quote(rel_path)}") as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return dest
