"""Cập nhật bản cài Music Studio trên máy admin từ một gói phát hành đã giải nén.

Admin bấm "Cap nhat Music Studio.bat" trong thư mục gói; file đó gọi:
    <thư mục cài>\\.venv\\Scripts\\python.exe <gói>\\scripts\\update.py --target <thư mục cài>
Khôi phục bản trước ("Khoi phuc ban truoc.bat" trong thư mục cài):
    .venv\\Scripts\\python.exe scripts\\update.py --target . --rollback

Các bước: kiểm tra hệ thống đã dừng -> sao lưu code + app.db vào backups\\ -> thay code do gói quản lý
-> cài thư viện theo requirements.lock (nếu đổi) -> chạy migration và kiểm tra khởi động được.
Lỗi ở bất kỳ bước nào sau khi thay code thì tự khôi phục code, thư viện và app.db về như cũ.
Không đụng tới .env, .venv, dữ liệu (audio, dataset, LoRA), model ACE-Step.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

PACKAGE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE))

from scripts.release_layout import CODE_DIRS, IGNORED, ROOT_FILES  # noqa: E402

MANAGED_FILES = (*ROOT_FILES, "release.json")  # release.json do make_release tạo, mang update_url
KEEP_BACKUPS = 5
REQUIREMENTS_MARKER = "music_studio_requirements.sha256"  # nằm trong .venv: bản lock đã cài
SMOKE_CHECK = """
from shared.config import get_settings
from api.db import Database
import api.main, worker.worker, scripts.desktop
from ui import app
app.build_ui()
print("migration:", Database(get_settings().db_path).migrations_applied or "không có")
"""


class UpdateError(RuntimeError):
    pass


def say(message: str = "") -> None:
    print(message, flush=True)


def confirm(question: str) -> bool:
    try:
        return input(f"{question} (y/n): ").strip().lower() in ("y", "yes", "c", "co")
    except EOFError:
        return False


def read_version(folder: Path) -> str:
    try:
        return (folder / "VERSION").read_text(encoding="utf-8").strip() or "chưa rõ"
    except OSError:
        return "chưa rõ"


def env_values(target: Path) -> dict[str, str]:
    from dotenv import dotenv_values
    return {k: v or "" for k, v in dotenv_values(target / ".env").items()}


def data_dir(target: Path) -> Path:
    configured = Path(env_values(target).get("DATA_DIR") or "data")
    return configured if configured.is_absolute() else target / configured


def python_of(target: Path) -> Path:
    return target / ".venv" / "Scripts" / "python.exe"


def ignore_copy(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if any(fnmatch.fnmatch(n, pattern) for pattern in IGNORED)}


# ---------- kiểm tra trước ----------
def check_install(target: Path) -> None:
    if not (target / "Music Studio.bat").is_file() or not (target / "api").is_dir():
        raise UpdateError(f"{target} không phải thư mục cài Music Studio.")
    if not python_of(target).is_file():
        raise UpdateError("Thư mục cài chưa có .venv. Lần cài đầu tiên hãy chạy scripts\\setup.bat.")
    if not (target / ".env").is_file():
        raise UpdateError("Thư mục cài chưa có .env. Lần cài đầu tiên hãy chạy scripts\\setup.bat.")


def ensure_stopped(target: Path) -> None:
    lock = data_dir(target) / "logs" / "desktop.lock"
    if lock.is_file():
        import msvcrt
        with lock.open("r+b") as handle:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                raise UpdateError("Cửa sổ điều khiển Music Studio đang mở. Bấm 'Dừng tất cả', đóng cửa sổ rồi chạy lại.")
    env = env_values(target)
    urls = [f"http://127.0.0.1:{env.get('API_PORT') or 8000}", f"http://127.0.0.1:{env.get('UI_PORT') or 7860}",
            env.get("ACESTEP_URL") or "http://127.0.0.1:8001"]
    for url in urls:
        address = urlparse(url)
        if address.hostname not in ("127.0.0.1", "localhost"):
            continue
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex((address.hostname, address.port or 80)) == 0:
                raise UpdateError(f"Cổng {address.port} đang được dùng: Music Studio hoặc ACE-Step vẫn chạy. "
                                  "Dừng hết rồi chạy lại.")


# ---------- sao lưu / khôi phục ----------
def make_backup(target: Path, from_version: str, to_version: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target / "backups" / f"{stamp}_v{from_version}"
    backup.mkdir(parents=True)
    for folder in CODE_DIRS:
        if (target / folder).is_dir():
            shutil.copytree(target / folder, backup / folder, ignore=ignore_copy)
    for name in MANAGED_FILES:
        if (target / name).is_file():
            (backup / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target / name, backup / name)
    db = data_dir(target) / "app.db"
    if db.is_file():
        with closing(sqlite3.connect(db)) as source, closing(sqlite3.connect(backup / "app.db")) as dest:
            source.backup(dest)
    (backup / "backup.json").write_text(json.dumps({
        "from_version": from_version, "to_version": to_version, "created": stamp,
        "db": str(db) if db.is_file() else None}, ensure_ascii=False, indent=2), encoding="utf-8")
    old = sorted(p for p in (target / "backups").iterdir() if (p / "backup.json").is_file())
    for stale in old[:-KEEP_BACKUPS]:
        shutil.rmtree(stale, ignore_errors=True)
    return backup


def copy_code(source: Path, target: Path) -> None:
    """Thay toàn bộ thư mục code (xóa cả file đã bỏ ở bản mới) và ghi đè file gốc của gói."""
    for folder in CODE_DIRS:
        if not (source / folder).is_dir():
            continue
        if (target / folder).exists():
            shutil.rmtree(target / folder)
        shutil.copytree(source / folder, target / folder, ignore=ignore_copy)
    for name in MANAGED_FILES:
        if (source / name).is_file():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target / name)


def restore_db(backup: Path, target: Path) -> None:
    snapshot = backup / "app.db"
    if not snapshot.is_file():
        return
    db = data_dir(target) / "app.db"
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    shutil.copy2(snapshot, db)


def restore(backup: Path, target: Path, with_db: bool) -> None:
    copy_code(backup, target)
    if with_db:
        restore_db(backup, target)
    install_requirements(target)


# ---------- cài đặt ----------
def install_requirements(target: Path) -> None:
    lock = target / "requirements.lock"
    if not lock.is_file():
        lock = target / "requirements.txt"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    marker = target / ".venv" / REQUIREMENTS_MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest:
        say("  Thư viện không đổi.")
        return
    say(f"  Cài thư viện theo {lock.name} (có thể mất vài phút)…")
    result = subprocess.run([str(python_of(target)), "-m", "pip", "install", "--disable-pip-version-check",
                             "-q", "-r", str(lock)], cwd=target)
    if result.returncode != 0:
        raise UpdateError("Cài thư viện thất bại. Kiểm tra kết nối mạng rồi chạy lại.")
    marker.write_text(digest, encoding="utf-8")


def smoke_check(target: Path) -> None:
    result = subprocess.run([str(python_of(target)), "-c", SMOKE_CHECK], cwd=target, capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONUTF8": "1"})
    if result.returncode != 0:
        raise UpdateError("Bản mới không khởi động được:\n" + (result.stderr or result.stdout)[-2000:])
    say("  " + result.stdout.strip().splitlines()[-1])


def report_new_settings(target: Path) -> None:
    from dotenv import dotenv_values
    template = dotenv_values(target / ".env.dev")
    missing = sorted(set(template) - set(env_values(target)))
    if missing:
        say("Biến cấu hình mới có trong .env.dev nhưng chưa có trong .env (đang dùng giá trị mặc định):")
        for key in missing:
            say(f"  - {key}")
        say("Đọc CHANGELOG.md xem có cần thêm vào .env không.")


def ensure_essentia(target: Path) -> None:
    if (target / "tools" / "essentia" / "essentia_streaming_extractor_music.exe").is_file():
        return
    say("Cài Essentia…")
    if subprocess.run([str(python_of(target)), str(target / "scripts" / "setup_essentia.py")], cwd=target).returncode:
        say("  Chưa cài được Essentia; nhãn AI sẽ cần kiểm tra thủ công cho đến khi cài xong.")


def log(target: Path, line: str) -> None:
    folder = data_dir(target) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "update.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")


# ---------- luồng chính ----------
def update(target: Path, assume_yes: bool) -> None:
    if PACKAGE == target:
        raise UpdateError("Hãy giải nén gói cập nhật ra thư mục riêng, không giải nén đè lên thư mục cài.")
    if not (PACKAGE / "release.json").is_file():
        raise UpdateError("Không thấy release.json: đây không phải gói phát hành.")
    check_install(target)
    old, new = read_version(target), read_version(PACKAGE)
    say(f"Thư mục cài: {target}")
    say(f"Phiên bản:   {old}  ->  {new}")
    if not assume_yes and not confirm("Tiếp tục cập nhật?"):
        say("Đã hủy.")
        return
    ensure_stopped(target)
    say("1/4 Sao lưu code và database…")
    backup = make_backup(target, old, new)
    say(f"  {backup}")
    migrated = False
    try:
        say("2/4 Thay code…")
        copy_code(PACKAGE, target)
        say("3/4 Kiểm tra thư viện…")
        install_requirements(target)
        say("4/4 Chạy migration và kiểm tra khởi động…")
        migrated = True
        smoke_check(target)
    except BaseException as exc:
        say(f"\nCẬP NHẬT LỖI: {exc}")
        say("Đang khôi phục bản cũ…")
        try:
            restore(backup, target, with_db=migrated)
            # Đã dùng rồi: "Khôi phục bản trước" phải bỏ qua bản này (nó chính là bản đang chạy).
            backup.rename(backup.with_name(backup.name + "_restored"))
            say(f"Đã khôi phục về {old}. Hệ thống dùng được như trước khi cập nhật.")
        except Exception as restore_error:
            say(f"KHÔNG khôi phục tự động được ({restore_error}). Bản sao lưu nằm ở: {backup}")
        log(target, f"FAILED {old} -> {new}: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
        raise SystemExit(1)
    report_new_settings(target)
    ensure_essentia(target)
    log(target, f"OK {old} -> {new} backup={backup.name}")
    say(f"\nĐã cập nhật lên {new}. Mở Music Studio.bat để dùng. Xem thay đổi trong CHANGELOG.md.")


def rollback(target: Path, assume_yes: bool, with_db: bool) -> None:
    """Mặc định chỉ trả code + thư viện về bản trước. Migration chỉ THÊM cột/bảng nên code cũ vẫn đọc được
    database mới, và dữ liệu tạo sau lần cập nhật được giữ. --with-db trả cả database về lúc sao lưu."""
    check_install(target)
    backups = sorted(p for p in (target / "backups").glob("*")
                     if (p / "backup.json").is_file() and not p.name.endswith("_restored"))
    if not backups:
        raise UpdateError("Chưa có bản sao lưu nào để khôi phục.")
    backup = backups[-1]
    info = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
    say(f"Khôi phục {read_version(target)} -> {info['from_version']} (sao lưu lúc {info['created']}).")
    if with_db:
        say("Database cũng trở về thời điểm đó: bài, nhãn, dataset tạo SAU lần cập nhật sẽ mất khỏi danh sách.")
    if not assume_yes and not confirm("Tiếp tục khôi phục?"):
        say("Đã hủy.")
        return
    ensure_stopped(target)
    restore(backup, target, with_db=with_db)
    backup.rename(backup.with_name(backup.name + "_restored"))
    log(target, f"ROLLBACK -> {info['from_version']} from {backup.name}" + (" (with db)" if with_db else ""))
    say(f"Đã khôi phục về {info['from_version']}.")


def wait_for_exit(pid: int, timeout: float = 120) -> None:
    """Chờ cửa sổ điều khiển cũ thoát hẳn (nó đang giữ thư viện trong .venv và desktop.lock)."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return  # đã thoát
    try:
        if kernel32.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
            raise UpdateError("Cửa sổ điều khiển cũ chưa đóng. Đóng nó rồi chạy cập nhật lại.")
    finally:
        kernel32.CloseHandle(handle)


def relaunch_desktop(target: Path) -> None:
    pythonw = target / ".venv" / "Scripts" / "pythonw.exe"
    subprocess.Popen([str(pythonw), str(target / "scripts" / "desktop.py")], cwd=target,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cập nhật hoặc khôi phục Music Studio")
    parser.add_argument("--target", required=True, help="thư mục cài Music Studio")
    parser.add_argument("--rollback", action="store_true", help="khôi phục code của bản sao lưu gần nhất")
    parser.add_argument("--with-db", action="store_true", help="khi khôi phục: trả cả database về lúc sao lưu")
    parser.add_argument("--yes", action="store_true", help="không hỏi xác nhận")
    parser.add_argument("--wait-pid", type=int, help="chờ tiến trình này thoát trước (cập nhật từ cửa sổ điều khiển)")
    parser.add_argument("--restart", action="store_true", help="mở lại cửa sổ điều khiển khi xong, kể cả khi lỗi")
    args = parser.parse_args()
    target = Path(args.target).resolve()
    code = 0
    try:
        if args.wait_pid:
            say("Chờ cửa sổ điều khiển cũ đóng…")
            wait_for_exit(args.wait_pid)
        if args.rollback:
            rollback(target, args.yes, args.with_db)
        else:
            update(target, args.yes)
    except UpdateError as exc:
        say(f"\nKHÔNG CẬP NHẬT: {exc}")
        code = 1
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    if args.restart:
        relaunch_desktop(target)
        say("\nĐã mở lại cửa sổ điều khiển Music Studio.")
        confirm("Đóng cửa sổ này?")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
