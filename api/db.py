"""Truy cập SQLite. Mỗi lần gọi mở một connection riêng (an toàn đa luồng/đa tiến trình).

Connection dùng isolation_level=None (autocommit) để tự quản lý transaction,
đặc biệt `BEGIN IMMEDIATE` khi worker nhận job.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from .migrations import migrate

WORKER_RETENTION_SECONDS = 24 * 3600

JSON_COLUMNS = {"payload", "result", "tags", "auto_labels", "track_ids", "params",
                "dit_models", "lm_models", "capabilities", "info", "backends"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _ago_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def _encode(values: dict[str, Any]) -> dict[str, Any]:
    """Cột JSON -> chuỗi JSON trước khi ghi."""
    return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_COLUMNS and v is not None else v)
            for k, v in values.items()}


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            self.migrations_applied = migrate(conn)

    # ---------- tiện ích ----------
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        for k in JSON_COLUMNS & d.keys():
            if d[k] is not None:
                d[k] = json.loads(d[k])
        return d

    def _one(self, sql: str, args: tuple = ()) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            return self.row_to_dict(conn.execute(sql, args).fetchone())

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [self.row_to_dict(r) for r in conn.execute(sql, args).fetchall()]

    def _insert(self, table: str, values: dict[str, Any]) -> int:
        values = _encode(values)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.connect() as conn:
            cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
            return int(cur.lastrowid)

    def _update(self, table: str, row_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        values = _encode(values)
        sets = ", ".join(f"{k} = ?" for k in values)
        with self.connect() as conn:
            conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))

    # ---------- jobs ----------
    def create_job(self, job_type: str, payload: dict[str, Any]) -> int:
        ts = now_iso()
        return self._insert("jobs", {"type": job_type, "status": "queued", "payload": payload,
                                     "created_at": ts, "updated_at": ts})

    def get_job(self, job_id: int) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def list_jobs(self, job_type: Optional[str] = None, status: Optional[str] = None,
                  limit: int = 100) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM jobs WHERE 1=1", []
        if job_type:
            sql += " AND type = ?"
            args.append(job_type)
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self._all(sql, tuple(args))

    def active_jobs(self) -> list[dict[str, Any]]:
        """Job đang chờ hoặc đang chạy (dùng để chặn đổi engine / xóa dữ liệu đang dùng)."""
        return self._all("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id")

    def job_counts(self) -> list[dict[str, Any]]:
        return self._all("SELECT type, status, COUNT(*) AS count FROM jobs "
                         "GROUP BY type, status ORDER BY type, status")

    def claim_job(self, worker_id: str, types: list[str]) -> Optional[dict[str, Any]]:
        """Nhận job queued cũ nhất có type phù hợp. An toàn với nhiều worker:
        BEGIN IMMEDIATE lấy write-lock ngay, nên hai worker không thể cùng chọn một job."""
        if not types:
            return None
        marks = ",".join("?" for _ in types)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    f"SELECT id FROM jobs WHERE status = 'queued' AND type IN ({marks}) "
                    "ORDER BY id ASC LIMIT 1", tuple(types)).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                conn.execute(
                    "UPDATE jobs SET status = 'running', worker_id = ?, progress = 0, updated_at = ? "
                    "WHERE id = ? AND status = 'queued'", (worker_id, now_iso(), row["id"]))
                job = self.row_to_dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone())
                conn.execute("COMMIT")
                return job
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def update_job_progress(self, job_id: int, progress: float, log_line: Optional[str]) -> bool:
        with self.connect() as conn:
            if log_line:
                cur = conn.execute(
                    "UPDATE jobs SET progress = ?, log = log || ? || char(10), updated_at = ? "
                    "WHERE id = ? AND status = 'running'", (progress, log_line, now_iso(), job_id))
            else:
                cur = conn.execute(
                    "UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                    (progress, now_iso(), job_id))
            return cur.rowcount > 0

    def finish_job(self, job_id: int, status: str, result: Optional[dict] = None,
                   error: Optional[str] = None) -> None:
        values: dict[str, Any] = {"status": status, "updated_at": now_iso()}
        if status == "done":
            values["progress"] = 1.0
        if result is not None:
            values["result"] = result
        if error is not None:
            values["error"] = error
        self._update("jobs", job_id, values)

    def _requeue(self, reason: str, where: str, args: tuple = ()) -> int:
        """Trả job 'running' khớp `where` về 'queued', ghi lý do vào log."""
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status = 'queued', worker_id = NULL, progress = 0, "
                "log = log || ? || char(10), updated_at = ? "
                f"WHERE status = 'running' AND {where}", (f"[requeue] {reason}", now_iso(), *args))
            return cur.rowcount

    def requeue_stale_jobs(self, stale_seconds: float) -> int:
        """Job 'running' mà worker giữ nó không gửi heartbeat quá `stale_seconds` (worker chết)
        -> trả về 'queued' để worker khác (hoặc chính nó sau khi bật lại) chạy tiếp."""
        threshold = _ago_iso(stale_seconds)
        return self._requeue(
            "worker không phản hồi, đưa lại vào hàng đợi",
            "updated_at < ? AND NOT EXISTS ("
            "SELECT 1 FROM workers w WHERE w.worker_id = jobs.worker_id AND w.last_seen >= ?)",
            (threshold, threshold))

    def release_job(self, job_id: int) -> None:
        self._requeue("worker dừng, trả job về hàng đợi", "id = ?", (job_id,))

    def requeue_worker_jobs(self, worker_id: str) -> int:
        """Worker khởi động lại với cùng id -> các job nó đang giữ chắc chắn đã mất."""
        return self._requeue("worker khởi động lại", "worker_id = ?", (worker_id,))

    # ---------- workers ----------
    def upsert_worker(self, w: dict[str, Any]) -> None:
        vals = {k: json.dumps(w.get(k, {} if k in ("info", "backends") else []), ensure_ascii=False)
                for k in ("dit_models", "lm_models", "capabilities", "info", "backends")}
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO workers (worker_id, backend, backends, dit_models, lm_models, capabilities, info, "
                "last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(worker_id) DO UPDATE SET "
                "backend=excluded.backend, backends=excluded.backends, dit_models=excluded.dit_models, "
                "lm_models=excluded.lm_models, capabilities=excluded.capabilities, info=excluded.info, "
                "last_seen=excluded.last_seen",
                (w["worker_id"], w["backend"], vals["backends"], vals["dit_models"], vals["lm_models"],
                 vals["capabilities"], vals["info"], now_iso()))
            # Dọn worker cũ (mỗi lần khởi động không đặt WORKER_ID sẽ có id mới): im lặng > 1 ngày
            # và không còn giữ job nào đang chạy.
            conn.execute(
                "DELETE FROM workers WHERE last_seen < ? AND worker_id NOT IN "
                "(SELECT worker_id FROM jobs WHERE status = 'running' AND worker_id IS NOT NULL)",
                (_ago_iso(WORKER_RETENTION_SECONDS),))

    def touch_worker(self, worker_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE workers SET last_seen = ? WHERE worker_id = ?", (now_iso(), worker_id))

    def list_workers(self, online_seconds: float) -> list[dict[str, Any]]:
        threshold = _ago_iso(online_seconds)
        rows = self._all("SELECT * FROM workers ORDER BY last_seen DESC")
        for r in rows:
            r["online"] = r["last_seen"] >= threshold
        return rows

    # ---------- tracks ----------
    def create_track(self, filename: str, path: str, duration: Optional[float], source: str = "ace") -> int:
        """source: 'ace' = nhạc tham chiếu thật do người dùng upload (mặc định), 'mock' = dữ liệu thử."""
        return self._insert("tracks", {"filename": filename, "path": path, "duration": duration,
                                       "source": source, "created_at": now_iso()})

    def get_track(self, track_id: int) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM tracks WHERE id = ?", (track_id,))

    def list_tracks(self, review_status: Optional[str] = None) -> list[dict[str, Any]]:
        if review_status:
            return self._all("SELECT * FROM tracks WHERE review_status = ? ORDER BY id", (review_status,))
        return self._all("SELECT * FROM tracks ORDER BY id")

    def update_track(self, track_id: int, values: dict[str, Any]) -> None:
        self._update("tracks", track_id, values)

    def delete_track(self, track_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))

    def save_track_auto_labels(self, track_id: int, labels: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Điền nhãn còn trống và đánh dấu bài đủ điều kiện là approved, trong một transaction."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                updated = self._merge_auto_labels(conn, track_id, labels)
                conn.execute("COMMIT")
                return updated
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _merge_auto_labels(self, conn: sqlite3.Connection, track_id: int,
                           labels: dict[str, Any]) -> Optional[dict[str, Any]]:
        track = self.row_to_dict(conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone())
        if track is None:
            return None
        values: dict[str, Any] = {"auto_labels": labels}
        if track["duration"] is None and labels.get("duration"):
            values["duration"] = labels["duration"]
        compatible = labels.get("is_instrumental", bool(track["is_instrumental"])) == bool(track["is_instrumental"])
        usable = (labels.get("source") == "ace" and (labels.get("caption") or "").strip()
                  and compatible and track["review_status"] != "rejected")
        if usable:
            # Chỉ điền trường còn trống: nhãn admin đã lưu không bị AI ghi đè.
            for key in ("caption", "key_scale", "time_signature", "tags"):
                if not track.get(key) and labels.get(key):
                    values[key] = labels[key]
            try:
                bpm = float(labels.get("bpm"))
                if not track.get("bpm") and 30 <= bpm <= 300:
                    values["bpm"] = bpm
            except (ValueError, TypeError):
                pass
            if not track["is_instrumental"]:
                if not track["lyrics"].strip() and labels.get("lyrics"):
                    values["lyrics"] = labels["lyrics"]
                if track["vocal_language"] in ("", "unknown") and labels.get("vocal_language"):
                    values["vocal_language"] = labels["vocal_language"]
            merged = {**track, **values}
            validation = labels.get("audio_validation")
            verified = validation is None or validation.get("status") == "verified"
            if merged["caption"].strip() and (merged["is_instrumental"] or merged["lyrics"].strip()) and verified:
                values["review_status"] = "approved"
            elif validation is not None:
                values["review_status"] = "pending"
        encoded = _encode(values)
        conn.execute("UPDATE tracks SET " + ", ".join(f"{key} = ?" for key in encoded) + " WHERE id = ?",
                     (*encoded.values(), track_id))
        return self.row_to_dict(conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone())

    # ---------- datasets ----------
    def next_dataset_version(self, name: str) -> int:
        row = self._one("SELECT COALESCE(MAX(version), 0) AS v FROM datasets WHERE name = ?", (name,))
        return int(row["v"]) + 1

    def create_dataset(self, name: str, version: int, track_ids: list[int], export_path: str) -> int:
        return self._insert("datasets", {"name": name, "version": version, "track_ids": track_ids,
                                         "export_path": export_path, "created_at": now_iso()})

    def get_dataset(self, dataset_id: int) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM datasets WHERE id = ? AND deleted = 0", (dataset_id,))

    def list_datasets(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM datasets WHERE deleted = 0 ORDER BY id DESC")

    def mark_dataset_deleted(self, dataset_id: int) -> None:
        """Giữ bản ghi (số phiên bản, liên kết LoRA) nhưng ẩn khỏi danh sách."""
        self._update("datasets", dataset_id, {"deleted": 1})

    def latest_active_dataset_version(self, name: str) -> int:
        return self._one("SELECT COALESCE(MAX(version), 0) AS v FROM datasets WHERE name = ? AND deleted = 0", (name,))["v"]

    # ---------- loras ----------
    def create_lora(self, values: dict[str, Any]) -> int:
        return self._insert("loras", {**values, "created_at": now_iso()})

    def get_lora(self, lora_id: int) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM loras WHERE id = ?", (lora_id,))

    def list_loras(self, base_model: Optional[str] = None) -> list[dict[str, Any]]:
        if base_model:
            return self._all("SELECT * FROM loras WHERE base_model = ? ORDER BY style_name, id DESC",
                             (base_model,))
        return self._all("SELECT * FROM loras ORDER BY style_name, id DESC")

    # ---------- generations ----------
    def create_generation(self, values: dict[str, Any]) -> int:
        return self._insert("generations", {**values, "created_at": now_iso()})

    def get_generation(self, gen_id: int) -> Optional[dict[str, Any]]:
        return self._one("SELECT * FROM generations WHERE id = ?", (gen_id,))

    def set_generation_rating(self, gen_id: int, rating: int) -> None:
        self._update("generations", gen_id, {"rating": rating})

    def list_generations(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM generations ORDER BY id DESC LIMIT ?", (limit,))

    def approved_captions(self, limit: int = 5) -> list[str]:
        rows = self._all("SELECT caption FROM tracks WHERE review_status = 'approved' AND caption != '' "
                         "ORDER BY id DESC LIMIT ?", (limit,))
        return [r["caption"] for r in rows]
