"""Chạy API, worker, UI — mỗi cái một tiến trình riêng; Ctrl+C tắt cả ba.
Dùng bởi start_all.sh / start_all.bat (chạy được trên Windows, Linux, macOS)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.config import get_settings  # noqa: E402

SERVICES = [("api", "api.main"), ("worker", "worker.worker"), ("ui", "ui.app")]


def wait_for_api(url: str, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def _interrupt(*_):
    raise KeyboardInterrupt


def check_acestep(s) -> None:
    """Có backend = ace mà ACE-Step không phản hồi /health -> cảnh báo (vẫn chạy tiếp; job ace sẽ báo lỗi rõ)."""
    using = [t for t, b in s.backends.items() if b == "ace"]
    if not using:
        return
    try:
        with urllib.request.urlopen(f"{s.acestep_url}/health", timeout=3) as r:
            if r.status == 200:
                print(f"[start_all] ACE-Step OK tại {s.acestep_url} (dùng cho: {', '.join(using)})", flush=True)
                return
    except OSError:
        pass
    print(f"[start_all] ⚠️ CẢNH BÁO: {', '.join(using)} dùng backend ace nhưng ACE-Step không phản hồi "
          f"{s.acestep_url}/health.\n[start_all]    Hãy mở terminal khác: cd <thư mục ACE-Step-1.5> && "
          "uv run acestep-api   (job sẽ báo 'ACE-Step chưa bật' cho tới khi bật)", flush=True)


def main() -> int:
    if os.name != "nt":  # kill <pid> từ ngoài -> dừng gọn như Ctrl+C
        signal.signal(signal.SIGTERM, _interrupt)
    s = get_settings()
    check_acestep(s)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, module in SERVICES:
            procs.append((name, subprocess.Popen([sys.executable, "-m", module], cwd=ROOT, env=env)))
            print(f"[start_all] đã chạy {name} (pid {procs[-1][1].pid})", flush=True)
            if name == "api" and not wait_for_api(s.api_url):
                print(f"[start_all] API không lên tại {s.api_url}", flush=True)
                return 1
        print(f"[start_all] UI: http://{s.ui_host}:{s.ui_port}  ·  API docs: {s.api_url}/docs  ·  Ctrl+C để dừng",
              flush=True)
        while True:
            for name, p in procs:
                if p.poll() is not None:
                    print(f"[start_all] {name} đã thoát (mã {p.returncode}) -> dừng tất cả", flush=True)
                    return p.returncode or 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[start_all] đang dừng...", flush=True)
        return 0
    finally:
        stop_all(procs)


def stop_all(procs: list[tuple[str, subprocess.Popen]]) -> None:
    # Gửi SIGINT (Ctrl+C) trước để worker kịp trả job đang chạy về hàng đợi; trên Windows các tiến trình
    # con cùng console đã tự nhận Ctrl+C. Sau 8 giây ai còn sống thì terminate/kill.
    for _, p in reversed(procs):  # worker, ui trước; API sau cùng
        if p.poll() is None and os.name != "nt":
            p.send_signal(signal.SIGINT)
    deadline = time.time() + 8
    for _, p in reversed(procs):
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
