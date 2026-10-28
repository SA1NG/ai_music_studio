"""Worker: vòng lặp poll API, nhận job, chạy backend, upload kết quả.

Không truy cập DB; file đầu vào/kết quả đi qua API. Ngoại lệ: train dataset dạng thư mục đọc thẳng
từ storage cục bộ (shared.dataset_files.local_dataset), nên worker train phải chạy cùng máy với API.
Chạy: python -m worker.worker   (hoặc scripts/start_worker)
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

import httpx

from shared.config import Settings, get_settings

from .api_client import ApiClient
from .backends import Backend, BackendResult, make_backends
from .backends.base import BackendError

log = logging.getLogger("worker")


class HttpJobContext:
    """JobContext dùng API qua HTTP."""

    def __init__(self, api: ApiClient, job: dict[str, Any], workdir: Path):
        self.api = api
        self.job_id: int = job["id"]
        self.payload: dict[str, Any] = job["payload"]
        self.workdir = workdir

    def progress(self, value: float, log_line: Optional[str] = None) -> None:
        try:
            self.api.progress(self.job_id, value, log_line)
        except httpx.HTTPError as e:  # báo tiến độ lỗi không được làm hỏng job
            log.warning("không gửi được progress: %s", e)
        if log_line:
            log.info("job %s %.0f%% %s", self.job_id, value * 100, log_line)

    def download(self, rel_path: str) -> Path:
        dest = self.workdir / "inputs" / Path(rel_path).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        return self.api.download(rel_path, dest)


class Worker:
    """Xử lý TUẦN TỰ, mỗi lần đúng một job (vòng lặp đơn luồng): sinh nhạc và train không bao giờ
    chạy song song trên cùng một GPU. Muốn chạy song song thì chạy thêm worker ở máy khác."""

    def __init__(self, settings: Settings, backends: dict[str, Backend] | Backend, api: ApiClient):
        self.settings = settings
        if isinstance(backends, Backend):  # một backend cho mọi loại job
            backends = {t: backends for t in ("generate", "autolabel", "train")}
        self.backends = backends
        self.api = api
        self.worker_id = settings.worker_id
        self.types = [t for t in settings.worker_types
                      if t in backends and t in backends[t].capabilities]
        self._stop = threading.Event()
        self.current_job: Optional[int] = None

    @property
    def backend_summary(self) -> str:
        return ",".join(f"{t}={self.backends[t].name}" for t in self.types)

    def warnings(self) -> list[str]:
        """Vấn đề của từng backend (ví dụ 'ACE-Step chưa bật'), mỗi backend kiểm tra một lần."""
        out, checked = [], {}
        for t in self.types:
            b = self.backends[t]
            if id(b) not in checked:
                checked[id(b)] = b.status()
            if checked[id(b)]:
                out.append(f"{t} ({b.name}): {checked[id(b)]}")
        return out

    def registration(self, fresh_start: bool = True) -> dict[str, Any]:
        # Model báo lên API là model của backend SINH NHẠC (model dùng để tạo bài và gắn LoRA).
        if "generate" in self.types:
            source = self.backends["generate"]
        elif self.types:
            source = self.backends[self.types[0]]
        else:
            source = None
        dit, lm = source.list_models_or_last() if source else ([], [])
        return {"worker_id": self.worker_id, "backend": self.backend_summary,
                "backends": {t: self.backends[t].name for t in self.types},
                "dit_models": [m.model_dump() for m in dit], "lm_models": [m.model_dump() for m in lm],
                "capabilities": self.types, "fresh_start": fresh_start,
                "info": {"api_url": self.settings.api_url, "warnings": self.warnings()}}

    def register_with_retry(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                info = self.registration()
                res = self.api.register(info)
                log.info("đã đăng ký worker=%s backends=%s dit=%s lm=%s (requeued=%s)",
                         self.worker_id, self.backend_summary, [m["name"] for m in info["dit_models"]],
                         [m["name"] for m in info["lm_models"]], res.get("requeued"))
                for w in info["info"]["warnings"]:
                    log.warning("%s", w)
                return
            except Exception as e:
                log.warning("chưa đăng ký được (%s). Thử lại sau %.0fs", e, delay)
                self._stop.wait(delay)
                delay = min(delay * 2, 15)

    def _heartbeat_loop(self) -> None:
        """Heartbeat mỗi 10s; mỗi 30s gửi lại trạng thái (model, cảnh báo) để UI biết ACE-Step tắt/bật."""
        tick, last_warnings = 0, None
        while not self._stop.wait(10):
            tick += 1
            try:
                if tick % 3 == 0:
                    info = self.registration(fresh_start=False)
                    self.api.register(info)
                    warnings = info["info"]["warnings"]
                    if warnings != last_warnings:
                        for w in warnings:
                            log.warning("%s", w)
                        if last_warnings and not warnings:
                            log.info("backend đã sẵn sàng trở lại")
                        last_warnings = warnings
                else:
                    self.api.heartbeat(self.worker_id)
            except Exception as e:
                log.debug("heartbeat lỗi: %s", e)

    def run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        self.current_job = job_id
        workdir = Path(tempfile.mkdtemp(prefix=f"job{job_id}_"))
        backend = self.backends[job["type"]]
        log.info("nhận job %s (%s, backend=%s)", job_id, job["type"], backend.name)
        try:
            if job["payload"].get("require_ai") and backend.name != "ace":
                raise BackendError("Tác vụ này cần ACE-Step thật; worker hiện tại chưa dùng backend ace để gán nhãn.")
            ctx = HttpJobContext(self.api, job, workdir)
            res: BackendResult = backend.run(job["type"], ctx)
            self.api.complete(job_id, res.result, res.files)
            log.info("job %s xong", job_id)
        except KeyboardInterrupt:
            try:
                self.api.release(job_id)
                log.info("đã trả job %s về hàng đợi", job_id)
            except Exception:
                pass  # không release được thì API tự requeue sau JOB_STALE_SECONDS
            raise
        except NotImplementedError as e:
            log.error("job %s: chưa hỗ trợ: %s", job_id, e)
            self.api.fail(job_id, f"Chưa hỗ trợ trên backend '{backend.name}': {e}")
        except BackendError as e:
            log.error("job %s lỗi: %s", job_id, e)
            self.api.fail(job_id, str(e))
        except Exception as e:
            log.error("job %s lỗi: %s", job_id, e)
            self.api.fail(job_id, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}")
        finally:
            self.current_job = None
            shutil.rmtree(workdir, ignore_errors=True)

    def run_once(self) -> bool:
        """Nhận và chạy tối đa một job. Trả True nếu có job."""
        job = self.api.claim(self.worker_id, self.types)
        if job is None:
            return False
        self.run_job(job)
        return True

    def run_forever(self) -> None:
        self.register_with_retry()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        log.info("bắt đầu poll %s mỗi %.1fs", self.settings.api_url, self.settings.worker_poll_interval)
        try:
            while not self._stop.is_set():
                try:
                    if not self.run_once():
                        self._stop.wait(self.settings.worker_poll_interval)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 401:
                        log.error("API từ chối token (401). Kiểm tra API_TOKEN.")
                    else:
                        log.warning("lỗi API: %s", e)
                    self._stop.wait(5)
                except httpx.HTTPError as e:
                    log.warning("mất kết nối API (%s). Thử lại...", e)
                    self._stop.wait(5)
                    self.register_with_retry()  # API có thể đã khởi động lại
        except KeyboardInterrupt:
            log.info("dừng worker")
        finally:
            self._stop.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # không log từng lần poll
    settings = get_settings()
    api = ApiClient(settings.api_url, settings.api_token)
    Worker(settings, make_backends(settings), api).run_forever()


if __name__ == "__main__":
    main()
