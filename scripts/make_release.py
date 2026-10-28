"""Đóng gói một bản phát hành và tạo latest.json cho kênh cập nhật online (chạy trên máy phát triển).

    .venv\\Scripts\\python.exe scripts\\make_release.py             # chạy test, tạo dist\\MusicStudio-<ver>.zip + latest.json
    .venv\\Scripts\\python.exe scripts\\make_release.py --freeze    # cập nhật requirements.lock từ .venv trước

Trước khi chạy: tăng số trong VERSION và thêm mục "## <ver>" vào CHANGELOG.md.
Địa chỉ đăng bản đọc từ release.config.json (chỉ ở máy phát triển, không vào gói).
GitHub Releases (thêm --publish để đăng luôn bằng GitHub CLI):
    {"github_repo": "tai-khoan/music-studio-releases"}
Nơi lưu trữ khác:
    {"feed_url": "https://…/latest.json",
     "download_url": "https://…/MusicStudio-{version}.zip"}
feed_url là địa chỉ CỐ ĐỊNH các máy hỏi để biết bản mới (được ghi vào gói); download_url là nơi chứa file ZIP
của từng bản ({version} được thay bằng số phiên bản). Sau khi chạy, đưa cả ZIP và latest.json lên đúng hai địa chỉ đó.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.release_layout import CODE_DIRS, IGNORED, ROOT_FILES  # noqa: E402

CONFIG_FILE = ROOT / "release.config.json"


def fail(message: str) -> None:
    print(f"LỖI: {message}")
    raise SystemExit(1)


def ignored(path: Path) -> bool:
    return any(fnmatch.fnmatch(part, pattern) for part in path.parts for pattern in IGNORED)


def package_files() -> list[Path]:
    files = []
    for name in ROOT_FILES:
        if not (ROOT / name).is_file():
            fail(f"thiếu file {name}")
        files.append(ROOT / name)
    for folder in CODE_DIRS:
        files += [p for p in sorted((ROOT / folder).rglob("*")) if p.is_file() and not ignored(p.relative_to(ROOT))]
    return files


def changelog_section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def load_config(args) -> dict:
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.is_file() else {}
    repo = config.get("github_repo", "")
    if repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        fail(f"github_repo phải có dạng 'tài-khoản/repo': {repo}")
    feed = args.feed_url or config.get("feed_url", "")
    download = args.download_url or config.get("download_url", "")
    if repo and not feed:
        # releases/latest luôn trỏ tới bản phát hành mới nhất -> địa chỉ latest.json cố định.
        feed = f"https://github.com/{repo}/releases/latest/download/latest.json"
        download = download or f"https://github.com/{repo}/releases/download/v{{version}}/MusicStudio-{{version}}.zip"
    if feed and not download:
        download = feed.rsplit("/", 1)[0] + "/MusicStudio-{version}.zip"
    for url in (feed, download):
        if url and not url.startswith("https://"):
            fail(f"địa chỉ phải dùng https://: {url}")
    return {"feed_url": feed, "download_url": download, "github_repo": repo}


def gh_executable() -> str:
    found = shutil.which("gh") or next((str(p) for p in (Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                                                          / "GitHub CLI" / "gh.exe",) if p.is_file()), None)
    if not found:
        fail("chưa cài GitHub CLI. Chạy: winget install --id GitHub.cli  rồi mở lại terminal và chạy: gh auth login")
    return found


def publish_github(repo: str, version: str, notes: str, files: list[Path], feed_url: str) -> None:
    """Đăng bản lên GitHub Releases (ZIP + latest.json trong cùng một release) rồi kiểm tra kênh cập nhật."""
    gh, tag = gh_executable(), f"v{version}"
    if subprocess.run([gh, "auth", "status"], capture_output=True).returncode != 0:
        fail("GitHub CLI chưa đăng nhập. Chạy: gh auth login")
    if subprocess.run([gh, "release", "view", tag, "--repo", repo], capture_output=True).returncode == 0:
        fail(f"Release {tag} đã có trên {repo}. Tăng VERSION cho bản mới.")
    notes_file = ROOT / "dist" / f"notes-{version}.md"
    notes_file.write_text(notes, encoding="utf-8")
    print(f"Đang đăng {tag} lên https://github.com/{repo}/releases …", flush=True)
    result = subprocess.run([gh, "release", "create", tag, *map(str, files), "--repo", repo,
                             "--title", f"Music Studio {version}", "--notes-file", str(notes_file), "--latest"])
    if result.returncode != 0:
        fail("đăng release thất bại (xem lỗi của gh ở trên)")
    import httpx
    for _ in range(10):
        try:
            if httpx.get(feed_url, follow_redirects=True, timeout=15).json().get("version") == version:
                print(f"Đã đăng. Các máy sẽ thấy bản {version} ở lần kiểm tra tới (hoặc bấm 'Kiểm tra cập nhật').")
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(3)
    print(f"Đã tạo release nhưng chưa đọc được {version} từ {feed_url}. Mở link đó trong trình duyệt để kiểm tra.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--freeze", action="store_true", help="ghi lại requirements.lock từ .venv hiện tại")
    parser.add_argument("--skip-tests", action="store_true", help="không chạy pytest (không khuyến nghị)")
    parser.add_argument("--force", action="store_true", help="ghi đè gói cùng phiên bản đã có trong dist")
    parser.add_argument("--feed-url", help="ghi đè feed_url trong release.config.json")
    parser.add_argument("--download-url", help="ghi đè download_url trong release.config.json")
    parser.add_argument("--publish", action="store_true", help="đăng lên GitHub Releases (cần github_repo và gh)")
    args = parser.parse_args()

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        fail("VERSION đang trống")
    notes = changelog_section(version)
    if not notes:
        fail(f"CHANGELOG.md chưa có mục '## {version}'. Ghi lại thay đổi để admin biết bản này có gì.")
    config = load_config(args)
    if args.freeze:
        frozen = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
        (ROOT / "requirements.lock").write_text(frozen.stdout, encoding="utf-8")
        print("Đã cập nhật requirements.lock")
    if not args.skip_tests:
        print("Đang chạy test…", flush=True)
        if subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT).returncode != 0:
            fail("test chưa qua, chưa đóng gói")

    dist = ROOT / "dist"
    target = dist / f"MusicStudio-{version}.zip"
    if target.exists() and not args.force:
        fail(f"{target.name} đã tồn tại. Tăng VERSION cho bản mới, hoặc dùng --force để ghi đè.")
    dist.mkdir(exist_ok=True)
    top = f"MusicStudio-{version}"
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    release = {"version": version, "built_at": built_at, "update_url": config["feed_url"],
               "code_dirs": list(CODE_DIRS), "root_files": list(ROOT_FILES)}
    files = package_files()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{top}/{path.relative_to(ROOT).as_posix()}")
        archive.writestr(f"{top}/release.json", json.dumps(release, ensure_ascii=False, indent=2))
    print(f"Đã tạo {target} ({len(files)} file, {target.stat().st_size / 1e6:.1f} MB)")

    if not config["feed_url"]:
        print("Chưa cấu hình kênh cập nhật online (release.config.json): gói này chỉ cập nhật thủ công.")
        return
    download_url = config["download_url"].replace("{version}", version)
    manifest = {"version": version, "url": download_url,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "size": target.stat().st_size,
                "notes": notes, "released_at": built_at}
    (dist / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.publish:
        if not config["github_repo"]:
            fail("--publish cần 'github_repo' trong release.config.json")
        publish_github(config["github_repo"], version, notes, [target, dist / "latest.json"], config["feed_url"])
        return
    print("Đã tạo dist\\latest.json. Đăng bản theo thứ tự:")
    print(f"  1. Đưa {target.name}  lên  {download_url}")
    print(f"  2. Đưa latest.json  lên  {config['feed_url']}  (ghi đè bản cũ)")
    print("Đưa ZIP lên trước: các máy chỉ thấy bản mới khi latest.json đổi.")


if __name__ == "__main__":
    main()
