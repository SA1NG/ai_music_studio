"""Windows control panel. Own only processes started by this panel."""
from __future__ import annotations

import os
import json
import uuid
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
from dotenv import dotenv_values
from shared.config import get_settings, reload_settings
from shared.updates import UpdateError, check_for_update, download_update, extract_package, feed_url
from shared.version import app_version
from scripts.process_tree import OwnedProcessTree

DEFAULT_DIT, DEFAULT_LM = "acestep-v15-turbo", "acestep-5Hz-lm-0.6B"
UPDATE_CHECK_SECONDS = 6 * 3600


def engine_defaults(engine_env):
    """(dit, lm) mặc định theo .env của ACE-Step; lm = "none" khi ACESTEP_INIT_LLM tắt."""
    dit = (engine_env.get("ACESTEP_CONFIG_PATH") or DEFAULT_DIT).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    enabled = str(engine_env.get("ACESTEP_INIT_LLM", "false")).lower() in ("true", "1", "yes")
    lm = (engine_env.get("ACESTEP_LM_MODEL_PATH") or DEFAULT_LM) if enabled else "none"
    return dit, lm


class Controller:
    def __init__(self, report=lambda message: None):
        self.settings = get_settings()
        self.report = report
        self.processes: list[tuple[str, subprocess.Popen]] = []
        self.process_trees = {}
        self.resource_extras = {}
        self.cancel = threading.Event()
        self.ui_ready = False
        self.operation_lock = threading.RLock()
        self.engine_owner = uuid.uuid4().hex
        self.logs = self.settings.data_dir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    @property
    def ui_url(self):
        host = self.settings.ui_host
        return f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{self.settings.ui_port}"

    def engine_dir(self):
        configured = os.getenv("MUSIC_STUDIO_ENGINE_DIR")
        if configured:
            return Path(configured).resolve()
        sibling = ROOT.parent / "ACE-Step-1.5"
        if sibling.is_dir():
            return sibling
        return Path(self.settings.acestep_work_dir).parent

    def spawn(self, name, command, cwd, extra_env=None, isolate_engine=False):
        env = dict(os.environ)
        if isolate_engine:
            # Music Studio's ACESTEP_* client settings must not leak into the engine.
            env = {key: value for key, value in env.items() if not key.startswith("ACESTEP_")}
        env.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", **(extra_env or {})})
        if isolate_engine and not env.get("ACESTEP_API_KEY"):
            env.pop("ACESTEP_API_KEY", None)
        with (self.logs / f"{name}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                       stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        self.processes.append((name, process))
        self.process_trees[process.pid] = OwnedProcessTree(process.pid)

    def request(self, path, **params):
        r = httpx.get(self.settings.api_url + path, params=params,
                      headers={"X-Token": self.settings.api_token}, timeout=3)
        r.raise_for_status()
        return r.json()

    def wait(self, url, check, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise RuntimeError("Đã hủy khởi động.")
            self.check_processes()
            try:
                r = httpx.get(url, timeout=2)
                if r.status_code == 200 and check(r):
                    return
            except (httpx.HTTPError, ValueError):
                pass
            self.cancel.wait(1)
        raise RuntimeError("Chờ quá lâu. Mở nhật ký để xem lỗi hoặc tiến độ tải model.")

    def check_processes(self):
        for name, process in self.processes:
            tree = self.process_trees.get(process.pid)
            alive = tree.is_running() if tree else process.poll() is None
            if not alive:
                raise RuntimeError(f"Dịch vụ {name} đã dừng. Xem file {name}.log trong nhật ký.")

    def terminate_owned(self, process):
        tree = self.process_trees.get(process.pid)
        if tree is None:
            raise RuntimeError("Không có thông tin sở hữu tiến trình; không tự tắt tiến trình bên ngoài.")
        tree.stop()
        process.wait(timeout=15)
        self.process_trees.pop(process.pid, None)

    @staticmethod
    def assert_port_free(url):
        parsed = urlparse(url)
        with socket.socket() as sock:
            if sock.connect_ex((parsed.hostname, parsed.port or 80)) == 0:
                raise RuntimeError(f"Cổng {parsed.port} đang được dùng bởi phiên khác. "
                                   "Hãy dừng phiên cũ trước; ứng dụng không tự tắt tiến trình bên ngoài.")

    def start(self, on_ui_ready=None):
        with self.operation_lock:
            return self._start(on_ui_ready)

    def _start(self, on_ui_ready=None):
        if self.processes:
            self.check_processes()
            if on_ui_ready:
                on_ui_ready()
            return
        self.cancel.clear()
        self.settings = reload_settings()
        self.logs = self.settings.data_dir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        s = self.settings
        using_ace = "ace" in s.backends.values()
        urls = [s.api_url, self.ui_url] + ([s.acestep_url] if using_ace else [])
        for url in urls:
            if urlparse(url).hostname not in ("localhost", "127.0.0.1"):
                raise RuntimeError("Bộ điều khiển này chỉ dành cho các dịch vụ chạy trên máy hiện tại.")
            self.assert_port_free(url)
        try:
            self.report("Đang mở giao diện Music Studio…")
            self.spawn("api", [sys.executable, "-m", "api.main"], ROOT)
            self.wait(s.api_url + "/health", lambda r: r.json().get("ok"), 45)
            self.spawn("ui", [sys.executable, "-m", "ui.app"], ROOT,
                       {"MUSIC_STUDIO_SESSION_STARTED": str(time.time())})
            self.wait(self.ui_url, lambda r: True, 90)
            self.ui_ready = True
            if on_ui_ready:
                on_ui_ready()
            if using_ace:
                self.configure_engine()
            self.spawn("worker", [sys.executable, "-m", "worker.worker"], ROOT)
            def worker_ready(response):
                models = self.request("/models")
                return bool(models["workers_online"])
            self.wait(s.api_url + "/health", worker_ready, 60)
            if using_ace:
                self.start_engine_monitor()
                self.report("Giao diện đã mở. Vào Model, chọn DiT/LM và bấm Áp dụng để nạp model.")
            else:
                self.report("Sẵn sàng. Bạn có thể tạo nhạc hoặc huấn luyện phong cách.")
        except Exception:
            self.stop()
            raise

    def launch_engine(self, dit=None, lm=None):
        s = self.settings
        engine = self.engine_dir()
        executable = engine / ".venv/Scripts/acestep-api.exe"
        if not executable.is_file():
            raise RuntimeError("Không tìm thấy ACE-Step. Đặt thư mục ACE-Step-1.5 cạnh "
                               "ai_music_studio hoặc cấu hình MUSIC_STUDIO_ENGINE_DIR trong .env.")
        engine_env = dotenv_values(engine / ".env")
        if (engine_env.get("ACESTEP_API_KEY") or "") != s.acestep_api_key:
            raise RuntimeError("ACESTEP_API_KEY trong .env của Music Studio và ACE-Step chưa khớp.")
        default_dit, default_lm = engine_defaults(engine_env)
        model = dit or default_dit
        lm = default_lm if lm is None else lm
        lm_enabled = lm != "none"
        self.report("Giao diện đã mở. Đang nạp model AI ở nền…")
        address = urlparse(s.acestep_url)
        engine_vars = {key: value for key, value in engine_env.items() if value is not None}
        engine_vars.update(ACESTEP_CONFIG_PATH2="", ACESTEP_CONFIG_PATH3="")
        engine_vars.update(ACESTEP_CONFIG_PATH=model, ACESTEP_INIT_LLM=str(lm_enabled).lower())
        if lm_enabled:
            engine_vars["ACESTEP_LM_MODEL_PATH"] = lm
        self.spawn("acestep", [str(executable), "--host", address.hostname,
                               "--port", str(address.port or 80)], engine,
                   {**engine_vars, "ACESTEP_NO_INIT": "false"}, isolate_engine=True)
        def ready(response):
            data = response.json().get("data", {})
            if not data.get("models_initialized"):
                return False
            if data.get("loaded_model") != model:
                raise RuntimeError("ACE-Step không nạp đúng DiT đã chọn.")
            if lm_enabled and data.get("llm_initialized") and data.get("loaded_lm_model") != lm:
                raise RuntimeError("ACE-Step đã chọn LM khác (có thể do giới hạn VRAM). Hãy chọn LM nhỏ hơn.")
            return bool(data.get("llm_initialized")) if lm_enabled else not data.get("llm_initialized")
        self.wait(s.acestep_url + "/health", ready, 900)

    def engine_post(self, path, body):
        r = httpx.post(self.settings.api_url + "/engine" + path,
                       headers={"X-Token": self.settings.api_token},
                       json={**body, "owner": self.engine_owner}, timeout=5)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def checkpoint_complete(folder):
        if not (folder / "config.json").is_file():
            return False
        if (folder / "model.safetensors").is_file():
            return True
        index = folder / "model.safetensors.index.json"
        try:
            shards = set(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values())
            return bool(shards) and all((folder / name).resolve().parent == folder.resolve()
                                        and (folder / name).is_file() for name in shards)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def configure_engine(self):
        from scripts.model_store import ModelStore
        root = self.engine_dir()
        dit, lm = engine_defaults(dotenv_values(root / ".env"))
        folders = [p for p in ModelStore(self).root.glob("acestep-*") if self.checkpoint_complete(p)]
        dits = sorted({p.name for p in folders if not p.name.startswith("acestep-5Hz-lm-")} | {dit})
        lms = ["none"] + sorted({p.name for p in folders if p.name.startswith("acestep-5Hz-lm-")} | ({lm} - {"none"}))
        return self.engine_post("/setup", dict(dit=dit, lm=lm, dit_models=dits, lm_models=lms))

    def stop_engine(self):
        for entry in list(self.processes):
            if entry[0] == "acestep":
                self.terminate_owned(entry[1])
                self.processes.remove(entry)
        self.assert_port_free(self.settings.acestep_url)

    def restart_engine(self, selection):
        with self.operation_lock:
            if self.cancel.is_set():
                return
            try:
                self.report("Đang khởi động lại engine theo cấu hình đã áp dụng…")
                self.stop_engine()
                self.launch_engine(selection["dit"], selection["lm"])
                def registered(response):
                    models = self.request("/models")
                    names = {m["name"] for m in models["lm_models"]}
                    return (models["workers_online"] and not models.get("warnings")
                            and any(m["name"] == selection["dit"] for m in models["dit_models"])
                            and names == {"none", selection["lm"]})
                self.wait(self.settings.api_url + "/health", registered, 90)
                self.engine_post("/result", {"revision": selection["revision"], "status": "ready"})
                self.report("Engine sẵn sàng với cấu hình mới.")
            except Exception as exc:
                try:
                    self.stop_engine()
                except Exception as stop_error:
                    exc = RuntimeError(f"{exc}; chưa dừng được engine lỗi: {stop_error}")
                self.engine_post("/result", {"revision": selection["revision"], "status": "error", "error": str(exc)})
                self.report(f"Nạp model thất bại: {exc}. Hãy chọn cấu hình khác và bấm Áp dụng.")

    def start_engine_monitor(self):
        def monitor():
            while not self.cancel.wait(1):
                try:
                    selection = self.engine_post("/poll", {})
                    if selection:
                        self.restart_engine(selection)
                except (httpx.HTTPError, RuntimeError) as exc:
                    self.report(f"Không áp dụng được cấu hình engine: {exc}")
        threading.Thread(target=monitor, daemon=True).start()

    def active_jobs(self):
        if not self.processes:
            return []
        return self.request("/jobs", status="running") + self.request("/jobs", status="queued")

    def stop(self):
        self.cancel.set()
        with self.operation_lock:
            return self._stop()

    def _stop(self):
        self.cancel.set()
        self.ui_ready = False
        failures = []
        # Reverse launch order: UI/worker before API/model. Include Windows venv child processes.
        for name, process in reversed(self.processes):
            try:
                self.terminate_owned(process)
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                failures.append((name, process))
        self.processes = list(reversed(failures))
        if failures:
            raise RuntimeError("Chưa dừng được một số dịch vụ. Xem nhật ký và thử dừng lại.")


def main():
    import msvcrt
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    controller = Controller()
    lock = (controller.logs / "desktop.lock").open("a+b")
    lock.seek(0)
    if not lock.read(1):
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    window = tk.Tk()
    window.withdraw()
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        messagebox.showinfo("Music Studio", "Bộ điều khiển đã mở. Hãy dùng cửa sổ Music Studio hiện có.")
        window.destroy()
        lock.close()
        return
    window.title(f"Music Studio {app_version()} — Điều khiển")
    window.geometry("880x740")
    window.minsize(820, 680)
    window.resizable(True, True)
    events = queue.Queue()
    controller.report = lambda text: events.put(("status", text))
    busy = False
    starting = False
    closing = False
    model_manager = None
    status = tk.StringVar(value="Đã dừng. Bấm Khởi động để bắt đầu.")
    frame = ttk.Frame(window, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Music Studio", font=("Segoe UI", 22, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Tạo nhạc • Chuẩn bị dữ liệu • Huấn luyện phong cách").pack(anchor="w", pady=(4, 20))
    ttk.Label(frame, textvariable=status, wraplength=565).pack(anchor="w", pady=(0, 18))
    update_bar = ttk.Frame(frame)  # chỉ hiện khi có bản mới
    update_text = tk.StringVar()
    ttk.Label(update_bar, textvariable=update_text, foreground="#b45309", wraplength=520).pack(side="left")
    update_button = ttk.Button(update_bar, text="Cập nhật")
    update_button.pack(side="left", padx=(12, 0))
    available_update = None
    stop_checks = threading.Event()
    bar = ttk.Frame(frame)
    bar.pack(fill="x")

    def run(action):
        nonlocal busy, starting
        if busy:
            return
        if model_manager and model_manager.busy:
            messagebox.showinfo("Quản lý model", "Đang quản lý model. Hãy chờ hoàn tất hoặc hủy tải trước.")
            return
        busy = True
        starting = action is start
        start_button.config(state="disabled")
        stop_button.config(state="normal" if starting else "disabled",
                           text="Hủy khởi động" if starting else "■ Dừng tất cả")
        def work():
            try:
                action()
            except Exception as exc:
                events.put(("error", str(exc)))
            finally:
                events.put(("done", None))
        threading.Thread(target=work, daemon=True).start()

    def start():
        controller.start(on_ui_ready=lambda: webbrowser.open(controller.ui_url))

    def stop():
        try:
            jobs = controller.active_jobs()
        except httpx.HTTPError:
            events.put(("confirm_stop", None))
            return
        if jobs:
            raise RuntimeError("Còn tác vụ đang chạy hoặc chờ. Hãy đợi hoàn tất trước khi dừng để giữ kết quả train.")
        controller.stop()
        controller.report("Đã dừng tất cả dịch vụ do ứng dụng khởi động.")

    def stop_clicked():
        if busy and starting:
            controller.cancel.set()
            status.set("Đang hủy khởi động và dừng các dịch vụ…")
            stop_button.config(state="disabled")
        else:
            run(stop)

    def backup():
        if busy or controller.processes:
            messagebox.showinfo("Sao lưu", "Hãy dừng tất cả dịch vụ trước khi sao lưu.")
            return
        destination = filedialog.asksaveasfilename(defaultextension=".zip", initialfile="MusicStudio-backup.zip")
        if not destination:
            return
        def save():
            import sqlite3
            import tempfile
            import zipfile
            from contextlib import closing
            target = Path(destination).resolve()
            if target.is_relative_to(controller.settings.data_dir.resolve()):
                raise RuntimeError("Hãy lưu bản sao lưu bên ngoài thư mục data.")
            Controller.assert_port_free(controller.settings.api_url)
            controller.report("Đang sao lưu nhạc, dataset và LoRA…")
            with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                db = controller.settings.db_path
                if db.exists():
                    snapshot = Path(tmp) / "app.db"
                    with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(snapshot)) as dest:
                        source.backup(dest)
                    archive.write(snapshot, "data/app.db")
                storage = controller.settings.storage_dir
                for path in storage.rglob("*"):
                    if path.is_file():
                        archive.write(path, "data/storage/" + path.relative_to(storage).as_posix())
            controller.report("Đã sao lưu dữ liệu. Model gốc có thể tải lại nên không đưa vào bản sao lưu.")
        run(save)

    start_button = ttk.Button(bar, text="▶ Khởi động", command=lambda: run(start))
    start_button.pack(side="left", padx=(0, 10))
    stop_button = ttk.Button(bar, text="■ Dừng tất cả", command=stop_clicked)
    stop_button.pack(side="left", padx=(0, 10))
    ttk.Button(bar, text="Sao lưu dữ liệu", command=backup).pack(side="left")
    def open_models():
        nonlocal model_manager
        from scripts.model_manager import ModelManager
        if model_manager and model_manager.window.winfo_exists():
            model_manager.window.lift()
            return
        model_manager = ModelManager(window, controller, parent_busy=lambda: busy)
    ttk.Button(frame, text="Quản lý model DiT / LM", command=open_models).pack(anchor="w", pady=(14, 0))
    tools = ttk.Frame(frame)
    tools.pack(fill="x", pady=16)
    open_link = ttk.Label(tools, text="Mở lại giao diện", cursor="hand2", foreground="#477dc0")
    open_link.pack(side="left", padx=(0, 20))
    open_link.bind("<Button-1>", lambda _: webbrowser.open(controller.ui_url) if controller.ui_ready else None)
    log_link = ttk.Label(tools, text="Xem nhật ký khi cần hỗ trợ", cursor="hand2", foreground="#477dc0")
    log_link.pack(side="left")
    log_link.bind("<Button-1>", lambda _: os.startfile(controller.logs))
    check_link = ttk.Label(tools, text="Kiểm tra cập nhật", cursor="hand2", foreground="#477dc0")
    check_link.pack(side="left", padx=(20, 0))

    # ---------- cập nhật online ----------
    def check_updates(manual=False):
        url = feed_url(controller.settings.update_url)
        if not url:
            if manual:
                events.put(("update_info", "Chưa cấu hình địa chỉ cập nhật (UPDATE_URL)."))
            return
        try:
            events.put(("update", check_for_update(url)))
            if manual:
                events.put(("update_checked", None))
        except UpdateError as exc:
            if manual:
                events.put(("update_info", str(exc)))

    def update_loop():
        while not stop_checks.is_set():
            check_updates()
            stop_checks.wait(UPDATE_CHECK_SECONDS)

    check_link.bind("<Button-1>", lambda _: threading.Thread(target=check_updates, args=(True,), daemon=True).start())

    def apply_update():
        manifest = available_update
        if controller.processes:
            stop()  # từ chối nếu còn tác vụ, giống nút Dừng tất cả
            if controller.processes:
                return
        folder = controller.settings.data_dir / "updates"
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)  # gói của lần trước
        controller.report(f"Đang tải bản {manifest['version']}…")
        archive = download_update(manifest, folder, lambda done, total: controller.report(
            f"Đang tải bản {manifest['version']}… {done / 1e6:.1f}"
            + (f"/{total / 1e6:.1f}" if total else "") + " MB"))
        package = extract_package(archive, folder / "package")
        events.put(("launch_update", package))

    def update_clicked():
        if busy or not available_update:
            return
        notes = (available_update.get("notes") or "").strip()
        message = (f"Cập nhật từ {app_version()} lên {available_update['version']}?\n\n"
                   + (notes[:1500] + "\n\n" if notes else "")
                   + "Các dịch vụ sẽ dừng (phải không còn tác vụ đang chạy), ứng dụng tự sao lưu, cập nhật "
                     "rồi mở lại. Nếu lỗi sẽ tự quay về bản hiện tại.")
        if messagebox.askyesno("Cập nhật Music Studio", message):
            run(apply_update)

    update_button.config(command=update_clicked)

    def launch_updater(package):
        python = Path(sys.executable).with_name("python.exe")
        subprocess.Popen([str(python), str(package / "scripts" / "update.py"), "--target", str(ROOT), "--yes",
                          "--wait-pid", str(os.getpid()), "--restart"],
                         cwd=package, creationflags=subprocess.CREATE_NEW_CONSOLE,
                         env={**os.environ, "PYTHONUTF8": "1"})
        stop_checks.set()
        resource_monitor.stop()
        lock.close()
        window.destroy()
    ttk.Label(frame, text="Giữ cửa sổ này mở khi sử dụng. Đóng trình duyệt không dừng hệ thống.",
              wraplength=565).pack(anchor="w", pady=8)
    from scripts.resources import ResourceMonitor, ResourcePanel
    resource_panel = ResourcePanel(frame)
    resource_monitor = ResourceMonitor(controller)
    resource_monitor.start(lambda sample: events.put(("resources", sample)))

    def close():
        nonlocal closing
        if model_manager and model_manager.busy:
            messagebox.showinfo("Quản lý model", "Hãy hủy tải và chờ hoàn tất trước khi đóng ứng dụng.")
            return
        if busy:
            messagebox.showinfo("Music Studio", "Đang xử lý. Vui lòng đợi rồi đóng cửa sổ.")
            return
        if controller.processes:
            closing = True
            run(stop)
        else:
            window.destroy()

    def tick():
        nonlocal busy, closing, starting, available_update
        while not events.empty():
            kind, value = events.get()
            if kind == "error":
                closing = False
                status.set(value)
                messagebox.showerror("Music Studio", value)
            elif kind == "status":
                status.set(value)
            elif kind == "resources":
                resource_panel.update(value)
            elif kind == "update":
                available_update = value
                if value:
                    update_text.set(f"Có bản mới {value['version']} (đang dùng {app_version()}).")
                    update_bar.pack(anchor="w", fill="x", pady=(0, 14), before=bar)
                else:
                    update_bar.pack_forget()
            elif kind == "update_checked":
                if not available_update:
                    messagebox.showinfo("Cập nhật", f"Đang dùng bản mới nhất ({app_version()}).")
            elif kind == "update_info":
                messagebox.showinfo("Cập nhật", value)
            elif kind == "launch_update":
                launch_updater(value)
                return
            elif kind == "done":
                busy = False
                starting = False
                start_button.config(state="normal")
                stop_button.config(state="normal", text="■ Dừng tất cả")
                if closing:
                    window.destroy()
                    return
            elif kind == "confirm_stop":
                closing = False
                if messagebox.askyesno("Không kiểm tra được tác vụ", "API không phản hồi nên không xác nhận được tiến độ. "
                                       "Dừng cưỡng bức có thể mất kết quả tác vụ đang chạy. Bạn có muốn dừng không?"):
                    # Queue after the current action's completion, without blocking Tk.
                    def force_stop():
                        if busy:
                            window.after(100, force_stop)
                        else:
                            def cleanup():
                                controller.stop()
                                controller.report("Đã dừng tất cả dịch vụ do ứng dụng khởi động.")
                            run(cleanup)
                    window.after(100, force_stop)
        if not busy and controller.processes:
            try:
                controller.check_processes()
            except RuntimeError as exc:
                status.set(str(exc) + " Bấm Dừng tất cả trước khi khởi động lại.")
        window.after(500, tick)

    window.protocol("WM_DELETE_WINDOW", close)
    window.deiconify()
    threading.Thread(target=update_loop, daemon=True).start()
    tick()
    if "--start" in sys.argv:
        window.after(500, lambda: run(start))
    try:
        window.mainloop()
    finally:
        stop_checks.set()
        resource_monitor.stop()
        lock.close()


if __name__ == "__main__":
    main()
