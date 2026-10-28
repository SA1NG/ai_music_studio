"""Client HTTP của UI. UI chỉ gọi API của chúng ta, không bao giờ gọi model trực tiếp."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx


class ApiError(RuntimeError):
    pass


class StudioClient:
    def __init__(self, base_url: str, token: str = "", cache_dir: Path = Path("data/ui_cache"),
                 client: Optional[httpx.Client] = None):
        headers = {"X-Token": token} if token else {}
        self.http = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=60.0)
        self.http.headers.update(headers)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _call(self, method: str, url: str, **kw) -> Any:
        try:
            r = self.http.request(method, url, **kw)
        except httpx.HTTPError as e:
            raise ApiError(f"Không kết nối được API ({e}). API đã chạy chưa?") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            raise ApiError(f"API lỗi {r.status_code}: {detail}")
        return r.json() if r.content else None

    # ---------- đọc ----------
    def models(self) -> dict: return self._call("GET", "/models")
    def engine(self) -> dict: return self._call("GET", "/engine")
    def apply_engine(self, dit: str, lm: str) -> dict:
        return self._call("POST", "/engine/apply", json={"dit": dit, "lm": lm})
    def system(self) -> dict: return self._call("GET", "/system")
    def job(self, job_id: int) -> dict: return self._call("GET", f"/jobs/{job_id}")
    def jobs(self, **params) -> list: return self._call("GET", "/jobs", params={k: v for k, v in params.items() if v})
    def generations(self, limit: int = 20) -> list: return self._call("GET", "/generations", params={"limit": limit})
    def generation(self, gen_id: int) -> dict: return self._call("GET", f"/generations/{gen_id}")
    def tracks(self) -> list: return self._call("GET", "/tracks")
    def track(self, track_id: int) -> dict: return self._call("GET", f"/tracks/{track_id}")
    def datasets(self) -> list: return self._call("GET", "/datasets")

    def loras(self, base_model: Optional[str] = None) -> list:
        return self._call("GET", "/loras", params={"base_model": base_model} if base_model else None)

    # ---------- ghi ----------
    def generate(self, body: dict) -> int: return self._call("POST", "/generate", json=body)["job_id"]

    def upload_generation_audio(self, path: str) -> dict:
        with open(path, "rb") as handle:
            return self._call("POST", "/generate/audio", files={"file": (Path(path).name, handle)}, timeout=300)

    def rate(self, gen_id: int, rating: int) -> dict:
        return self._call("POST", f"/generations/{gen_id}/rating", json={"rating": rating})

    def upload_tracks(self, paths: list[str], autolabel: bool = True, **options) -> list:
        handles = [open(p, "rb") for p in paths]
        try:
            files = [("files", (Path(p).name, h, "application/octet-stream")) for p, h in zip(paths, handles)]
            return self._call("POST", "/tracks", files=files, params={"autolabel": str(autolabel).lower(),
                              **{key: str(value).lower() if isinstance(value, bool) else value for key, value in options.items()}},
                              timeout=300.0)
        finally:
            for h in handles:
                h.close()

    def update_track(self, track_id: int, values: dict) -> dict:
        return self._call("PUT", f"/tracks/{track_id}", json=values)

    def autolabel(self, track_id: int, **options) -> int:
        return self._call("POST", f"/tracks/{track_id}/autolabel", json=options)["job_id"]

    def create_dataset(self, name: str, track_ids: Optional[list[int]] = None, base_dataset_id: Optional[int] = None,
                       cleanup_sources: bool = False) -> dict:
        body = {"name": name, "cleanup_sources": cleanup_sources}
        if track_ids is not None:
            body["track_ids"] = track_ids
        if base_dataset_id is not None:
            body["base_dataset_id"] = base_dataset_id
        return self._call("POST", "/datasets", json=body)

    def train(self, body: dict) -> int: return self._call("POST", "/train", json=body)["job_id"]

    # ---------- file ----------
    def fetch(self, rel_path: str, url: Optional[str] = None) -> str:
        """Tải file từ API về cache cục bộ cho Gradio phát/tải (hoạt động cả khi bật API_TOKEN)."""
        dest = self.cache_dir / rel_path.replace("/", "__")
        if not dest.exists():
            try:
                with self.http.stream("GET", url or f"/files/{quote(rel_path)}") as r:
                    if r.status_code >= 400:
                        raise ApiError(f"Không tải được {rel_path} (HTTP {r.status_code})")
                    with open(dest, "wb") as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)
            except httpx.HTTPError as e:
                raise ApiError(f"Không tải được {rel_path}: {e}") from e
        return str(dest)

    def fetch_dataset(self, ds: dict) -> str:
        name = ds["export_path"] if ds["export_path"].endswith('.zip') else ds["export_path"] + '.zip'
        return self.fetch(name, f"/datasets/{ds['id']}/download")

    def delete_track(self, track_id: int) -> dict:
        return self._call("DELETE", f"/tracks/{track_id}")

    def delete_dataset(self, dataset_id: int) -> dict:
        return self._call("DELETE", f"/datasets/{dataset_id}")
