"""Download/delete only DiT/LM folders inside the configured checkpoints directory."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from dotenv import dotenv_values

from scripts.process_tree import OwnedProcessTree


class ModelStore:
    def __init__(self, controller):
        self.controller = controller
        self.engine = controller.engine_dir().resolve()
        values = dotenv_values(self.engine / ".env")
        configured = values.get("ACESTEP_CHECKPOINTS_DIR")
        root = Path(configured).expanduser() if configured else self.engine / "checkpoints"
        self.root = (root if root.is_absolute() else self.engine / root).resolve()
        self.python = self.engine / ".venv/Scripts/python.exe"
        self.helper = Path(__file__).with_name("ace_models.py")
        self.models = []

    def environment(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("ACESTEP_")}
        env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", HF_HUB_DISABLE_SYMLINKS_WARNING="1")
        return env

    def target(self, name):
        if not re.fullmatch(r"acestep-[A-Za-z0-9._-]+", name):
            raise ValueError("Tên model không hợp lệ.")
        path = self.root / name
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError("Model nằm ngoài thư mục checkpoints hoặc là liên kết.")
        return path

    def refresh(self):
        if not self.python.is_file():
            raise RuntimeError("Chưa có môi trường ACE-Step. Cần cài ACE-Step trước khi quản lý model.")
        result = subprocess.run([str(self.python), str(self.helper), "catalog"], cwd=self.engine,
                                env=self.environment(), capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        lines = [line for line in result.stdout.splitlines() if line.startswith("MUSIC_STUDIO_CATALOG=")]
        if result.returncode or not lines:
            raise RuntimeError("Không đọc được danh sách model từ ACE-Step. Kiểm tra môi trường engine.")
        known = {item["name"]: item for item in json.loads(lines[-1].split("=", 1)[1])}
        for folder in self.root.glob("acestep-*"):
            if folder.is_dir():
                known.setdefault(folder.name, dict(name=folder.name,
                    kind="LM" if folder.name.startswith("acestep-5Hz-lm-") else "DiT", local_only=True))
        rows = []
        for name, entry in sorted(known.items()):
            path = self.target(name)
            complete = self.controller.checkpoint_complete(path)
            rows.append({**entry, "complete": complete, "exists": path.exists(), "bytes": self.size(path)})
        self.models = rows
        return rows

    @staticmethod
    def size(path):
        total = 0
        if path.is_dir():
            for directory, folders, files in os.walk(path, followlinks=False):
                for name in files:
                    file = Path(directory) / name
                    try:
                        if not file.is_symlink():
                            total += file.stat().st_size
                    except FileNotFoundError:
                        pass
        return total

    def assert_stopped(self):
        if self.controller.processes:
            raise RuntimeError("Hãy bấm Dừng tất cả trước khi tải hoặc xóa model.")
        s = self.controller.settings
        for url in [s.api_url, self.controller.ui_url, s.acestep_url]:
            self.controller.assert_port_free(url)

    def download(self, name, cancel, report):
        with self.controller.operation_lock:
            self.assert_stopped()
            if cancel.is_set():
                raise InterruptedError("Đã hủy tải trước khi bắt đầu.")
            model = next((m for m in self.models if m["name"] == name and not m.get("local_only")), None)
            if not model:
                raise ValueError("Model không có trong danh sách tải được hỗ trợ.")
            target = self.target(name)
            log_path = self.controller.logs / "model-download.log"
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen([str(self.python), str(self.helper), "download", "--name", name,
                                            "--dir", str(self.root)], cwd=self.engine, env=self.environment(),
                                           stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                tree = OwnedProcessTree(process.pid)
                self.controller.resource_extras["download"] = tree
                try:
                    while tree.is_running():
                        if cancel.wait(.5):
                            raise InterruptedError("Đã hủy tải. File tải dở được giữ để có thể tiếp tục.")
                        report(self.size(target))
                    process.wait(timeout=10)
                    if process.returncode or not self.controller.checkpoint_complete(target):
                        raise RuntimeError("Tải model chưa hoàn tất. Xem data/logs/model-download.log rồi thử tải tiếp.")
                finally:
                    try:
                        tree.stop()
                    finally:
                        self.controller.resource_extras.pop("download", None)
            return log_path

    def delete(self, name):
        with self.controller.operation_lock:
            self.assert_stopped()
            path = self.target(name)
            if not any(m["name"] == name for m in self.models):
                raise ValueError("Model không có trong danh sách đang quản lý.")
            # Resolved absolute target is checked before any recursive deletion.
            if path.resolve().parent != self.root or path.is_symlink():
                raise ValueError("Không xóa liên kết hoặc thư mục ngoài checkpoints.")
            if path.is_dir():
                shutil.rmtree(path)
