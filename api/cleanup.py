"""Xóa dữ liệu mock (source='mock' / backend='mock') cùng file của nó, xóa theo dây chuyền:

    track mock            -> mọi dataset chứa nó
    dataset bị xóa        -> mọi LoRA train từ nó
    LoRA mock / bị xóa    -> mọi generation dùng LoRA đó
    generation mock       -> xóa

Job (lịch sử) được giữ lại. Đây là công cụ quản trị chạy trên máy có DB (không phải worker).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .db import Database
from .storage import Storage, StorageError


@dataclass
class CleanupPlan:
    tracks: list[dict] = field(default_factory=list)
    datasets: list[dict] = field(default_factory=list)
    loras: list[dict] = field(default_factory=list)
    generations: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.tracks or self.datasets or self.loras or self.generations)

    def files(self) -> list[str]:
        paths = [t["path"] for t in self.tracks] + [d["export_path"] for d in self.datasets]
        paths += [x["path"] for x in self.loras] + [g["audio_path"] for g in self.generations]
        return [p for p in paths if p]

    def summary(self) -> str:
        def line(title, rows, fmt):
            return [f"{title}: {len(rows)}"] + [f"  - {fmt(r)}" for r in rows]
        out = line("Track mock", self.tracks, lambda t: f"#{t['id']} {t['filename']}")
        out += line("Dataset (chứa track mock)", self.datasets, lambda d: f"#{d['id']} {d['name']} v{d['version']}")
        out += line("LoRA (mock hoặc train từ dataset bị xóa)", self.loras,
                    lambda x: f"#{x['id']} {x['name']} [{x['source']}] base={x['base_model']}")
        out += line("Generation (backend mock hoặc dùng LoRA bị xóa)", self.generations,
                    lambda g: f"#{g['id']} [{g['backend']}] {g['prompt_final'][:50]}")
        out.append(f"File sẽ xóa: {len(self.files())}")
        return "\n".join(out)


def plan_cleanup(db: Database) -> CleanupPlan:
    tracks = db._all("SELECT * FROM tracks WHERE source = 'mock' ORDER BY id")
    mock_track_ids = {t["id"] for t in tracks}
    datasets = [d for d in db.list_datasets() if mock_track_ids & set(d["track_ids"])]
    ds_ids = {d["id"] for d in datasets}
    loras = [x for x in db.list_loras() if x["source"] == "mock" or x["dataset_id"] in ds_ids]
    lora_ids = {x["id"] for x in loras}
    gens = [g for g in db._all("SELECT * FROM generations ORDER BY id")
            if g["backend"] == "mock" or g["lora_id"] in lora_ids]
    return CleanupPlan(sorted(tracks, key=lambda t: t["id"]), sorted(datasets, key=lambda d: d["id"]),
                       sorted(loras, key=lambda x: x["id"]), gens)


def execute_cleanup(db: Database, storage: Storage, plan: CleanupPlan, ui_cache: Path | None = None) -> int:
    """Xóa bản ghi (một transaction) rồi xóa file. Trả về số file đã xóa."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Thứ tự theo khóa ngoại: generations -> loras -> datasets -> tracks
            for table, rows in (("generations", plan.generations), ("loras", plan.loras),
                                ("datasets", plan.datasets), ("tracks", plan.tracks)):
                conn.executemany(f"DELETE FROM {table} WHERE id = ?", [(r["id"],) for r in rows])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    removed = 0
    for rel in plan.files():
        try:
            p = storage.resolve(rel)
        except StorageError:
            continue
        if p.parent.name.startswith("lora_job") and p.parent.is_dir():
            # Mỗi LoRA có thư mục riêng (loras/lora_jobNNNNNN/: weights + metadata.json) -> xóa cả thư mục
            removed += sum(1 for f in p.parent.rglob("*") if f.is_file())
            shutil.rmtree(p.parent)
        elif p.is_file():
            p.unlink()
            removed += 1
        if ui_cache is not None:
            cached = ui_cache / rel.replace("/", "__")
            if cached.is_file():
                cached.unlink()
    return removed
