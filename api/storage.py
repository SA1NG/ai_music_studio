"""Quản lý file trong data/storage. Mọi đường dẫn lưu trong DB là tương đối so với storage root
(dạng posix, ví dụ `outputs/12.wav`) để chuyển máy không bị hỏng."""
from __future__ import annotations

import json
import hashlib
import re
import shutil
import zipfile
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from shared.dataset_files import FolderWriter, flat_name, read_manifest, validate_directory

SUBDIRS = ("uploads", "outputs", "loras", "datasets")


class StorageError(ValueError):
    pass


def safe_name(name: str, default: str = "file") -> str:
    """Tên file an toàn: bỏ thư mục, ký tự lạ."""
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE).strip("._")
    return name or default


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        for sub in SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def resolve(self, rel_path: str) -> Path:
        """Đường dẫn tương đối -> tuyệt đối, chặn path traversal (../, đường dẫn tuyệt đối...)."""
        if not rel_path or "\x00" in rel_path:
            raise StorageError("đường dẫn không hợp lệ")
        p = (self.root / rel_path.replace("\\", "/")).resolve()
        if not p.is_relative_to(self.root) or p == self.root:
            raise StorageError("đường dẫn nằm ngoài storage")
        return p

    def rel(self, abs_path: Path) -> str:
        return Path(abs_path).resolve().relative_to(self.root).as_posix()

    def save_stream(self, subdir: str, filename: str, stream: BinaryIO) -> Path:
        target = self.resolve(f"{subdir}/{filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as f:
            shutil.copyfileobj(stream, f)
        return target

    def remove_dataset(self, rel_path: str) -> None:
        raw = self.root / rel_path
        target = self.resolve(rel_path)
        relative = target.relative_to(self.root)
        if len(relative.parts) != 2 or relative.parts[0] != "datasets" or raw.is_symlink():
            raise StorageError("Chỉ xóa một phiên bản nằm trực tiếp trong datasets")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)

    # ---------- dataset export ----------
    def export_dataset(self, name: str, version: int, tracks: Iterable[dict[str, Any]], base: Path | None = None,
                       directory: bool = False) -> Path:
        """Lưu snapshot thư mục (directory=True), hoặc ZIP cho tương thích dữ liệu cũ.
        Định dạng mà `/v1/dataset/scan` của ACE-Step 1.5 đọc:
        (đã xác minh trong acestep/training/dataset_builder_modules/scan.py):
            <stem>.<ext>          audio
            <stem>.caption.txt    caption (có caption thì mẫu mới "labeled" và được tiền xử lý)
            <stem>.json           {caption, bpm, keyscale, timesignature}
        Nhạc không lời: KHÔNG tạo <stem>.lyrics.txt và KHÔNG ghi "language" — cả hai đều làm ACE-Step
        coi bài là có lời (is_instrumental=False); không có thì scan tự gán lời "[Instrumental]".
        Nhạc có lời: ghi thêm <stem>.lyrics.txt và language trong JSON.
        Kèm manifest.json chứa đủ metadata của chúng ta (tags, track_id...)."""
        folder = f"{safe_name(name, 'dataset')}_{hashlib.sha256(name.encode()).hexdigest()[:8]}_v{version}"
        target = self.resolve(f"datasets/{folder}" + ("" if directory else ".zip"))
        if target.exists():
            raise StorageError("Phiên bản dataset đã tồn tại")
        # Thư mục được ghi vào chỗ tạm rồi đổi tên khi đã kiểm tra xong, để không bao giờ lộ snapshot dở dang.
        writing = self.resolve(f"datasets/.building_{uuid.uuid4().hex}") if directory else target
        manifest: dict[str, Any] = {"name": name, "version": version, "format": "acestep-1.5-flat",
                                    "tracks": []}
        with (FolderWriter(writing) if directory else zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED)) as zf:
            if base is not None:
                manifest["tracks"] = read_manifest(base)["tracks"]
                if base.is_dir():
                    for item in base.iterdir():
                        if item.name != "manifest.json":
                            zf.write(item, item.name)
                else:
                    with zipfile.ZipFile(base) as previous:
                        for item in previous.infolist():
                            if item.filename != "manifest.json":
                                zf.writestr(flat_name(item.filename), previous.read(item))
            for t in tracks:
                src = self.resolve(t["path"])
                stem = f"{t['id']:04d}_{safe_name(Path(t['filename']).stem, 'track')}"
                audio_name = stem + src.suffix.lower()
                zf.write(src, audio_name)
                caption = t.get("caption") or ""
                zf.writestr(f"{stem}.caption.txt", caption)
                meta: dict[str, Any] = {"caption": caption}
                validation = (t.get("auto_labels") or {}).get("audio_validation")
                if validation:
                    meta["audio_validation"] = {**validation, 'admin_accepted': t.get('review_status') == 'approved' and validation.get('status') != 'verified'}
                if not t.get("is_instrumental", True):
                    zf.writestr(f"{stem}.lyrics.txt", t.get("lyrics") or "")
                    meta["language"] = t.get("vocal_language") or "unknown"
                if t.get("bpm"):
                    meta["bpm"] = int(round(float(t["bpm"])))
                if t.get("key_scale"):
                    meta["keyscale"] = t["key_scale"]
                if t.get("time_signature"):
                    meta["timesignature"] = str(t["time_signature"]).split("/")[0]
                zf.writestr(f"{stem}.json", json.dumps(meta, ensure_ascii=False, indent=2))
                manifest["tracks"].append({
                    "track_id": t["id"], "audio": audio_name, "original_filename": t["filename"],
                    "duration": t.get("duration"), "tags": t.get("tags") or [], **meta,
                    "is_instrumental": bool(t.get("is_instrumental", True)), "lyrics": t.get("lyrics") or "",
                })
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            if directory:
                try:
                    validate_directory(writing)
                except Exception:
                    self.remove_dataset(self.rel(writing))
                    raise
        if directory:
            try:
                writing.rename(target)
            except Exception:
                self.remove_dataset(self.rel(writing))
                raise
        return target
