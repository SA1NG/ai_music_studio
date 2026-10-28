"""Migration SQLite, tự chạy khi API khởi động (Database.__init__).

Bảng `schema_version` ghi các version đã áp dụng. Mỗi migration chạy trong một transaction riêng.
Chỉ THÊM (ALTER TABLE ADD COLUMN, CREATE ... IF NOT EXISTS), không xóa dữ liệu cũ.
Thêm migration mới: viết hàm `_vN(conn)` rồi thêm vào MIGRATIONS, không sửa migration đã phát hành.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable

# Schema của giai đoạn 1 (trước khi có schema_version). DB cũ đã có sẵn các bảng này,
# nên v1 dùng IF NOT EXISTS: với DB cũ nó chỉ đánh dấu version 1, với DB mới thì tạo bảng.
BASELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL CHECK (type IN ('generate','autolabel','train')),
    status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
    payload     TEXT NOT NULL DEFAULT '{}',
    result      TEXT,
    progress    REAL NOT NULL DEFAULT 0,
    log         TEXT NOT NULL DEFAULT '',
    error       TEXT,
    worker_id   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_type ON jobs(status, type, id);

CREATE TABLE IF NOT EXISTS tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    path            TEXT NOT NULL,
    duration        REAL,
    bpm             REAL,
    key_scale       TEXT NOT NULL DEFAULT '',
    time_signature  TEXT NOT NULL DEFAULT '',
    caption         TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',
    auto_labels     TEXT NOT NULL DEFAULT '{}',
    review_status   TEXT NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','approved','rejected')),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    track_ids   TEXT NOT NULL DEFAULT '[]',
    export_path TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS loras (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    style_name  TEXT NOT NULL,
    base_model  TEXT NOT NULL,
    dataset_id  INTEGER REFERENCES datasets(id),
    params      TEXT NOT NULL DEFAULT '{}',
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loras_base ON loras(base_model);

CREATE TABLE IF NOT EXISTS generations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id),
    prompt_original TEXT NOT NULL DEFAULT '',
    prompt_final    TEXT NOT NULL DEFAULT '',
    dit_model       TEXT NOT NULL,
    lm_model        TEXT NOT NULL DEFAULT 'none',
    lora_id         INTEGER REFERENCES loras(id),
    lora_scale      REAL NOT NULL DEFAULT 1.0,
    params          TEXT NOT NULL DEFAULT '{}',
    audio_path      TEXT NOT NULL,
    rating          INTEGER NOT NULL DEFAULT 0 CHECK (rating IN (-1,0,1)),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workers (
    worker_id    TEXT PRIMARY KEY,
    backend      TEXT NOT NULL,
    dit_models   TEXT NOT NULL DEFAULT '[]',
    lm_models    TEXT NOT NULL DEFAULT '[]',
    capabilities TEXT NOT NULL DEFAULT '[]',
    info         TEXT NOT NULL DEFAULT '{}',
    last_seen    TEXT NOT NULL
);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _v1(conn: sqlite3.Connection) -> None:
    conn.executescript(BASELINE_SCHEMA)


def _v2(conn: sqlite3.Connection) -> None:
    # Bản ghi cũ đều sinh ra ở chế độ mock -> DEFAULT 'mock'. Bản ghi mới do code ghi rõ giá trị.
    #   tracks.source:      'mock' = dữ liệu thử của giai đoạn 1, 'ace' = nhạc tham chiếu thật do người dùng upload
    #   loras.source:       backend đã train ra LoRA
    #   generations.backend: backend đã sinh ra bài
    _add_column(conn, "tracks", "source", "TEXT NOT NULL DEFAULT 'mock'")
    _add_column(conn, "loras", "source", "TEXT NOT NULL DEFAULT 'mock'")
    _add_column(conn, "generations", "backend", "TEXT NOT NULL DEFAULT 'mock'")
    _add_column(conn, "generations", "seed", "INTEGER")
    conn.execute("UPDATE generations SET seed = CAST(json_extract(params, '$.seed') AS INTEGER) "
                 "WHERE seed IS NULL AND json_extract(params, '$.seed') IS NOT NULL")
    # Backend theo từng loại job của worker: {"generate": "ace", "autolabel": "mock", ...}
    _add_column(conn, "workers", "backends", "TEXT NOT NULL DEFAULT '{}'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_generations_backend ON generations(backend)")


def _v3(conn: sqlite3.Connection) -> None:
    _add_column(conn, "tracks", "is_instrumental", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "tracks", "lyrics", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "tracks", "vocal_language", "TEXT NOT NULL DEFAULT 'unknown'")


def _v4(conn: sqlite3.Connection) -> None:
    _add_column(conn, "datasets", "deleted", "INTEGER NOT NULL DEFAULT 0")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "schema giai đoạn 1", _v1),
    (2, "source/backend/seed + backend theo loại job của worker", _v2),
    (3, "lời hát và ngôn ngữ cho nhạc tham chiếu", _v3),
    (4, "đánh dấu dataset đã xóa để giữ lịch sử và số phiên bản", _v4),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version ("
                 "version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Áp dụng các migration còn thiếu. Connection phải ở chế độ autocommit (isolation_level=None).
    Trả về danh sách version vừa áp dụng."""
    def record(version: int, description: str) -> None:
        conn.execute("INSERT OR IGNORE INTO schema_version (version, description, applied_at) VALUES (?, ?, ?)",
                     (version, description, datetime.now(timezone.utc).isoformat(timespec="seconds")))

    applied = []
    for version, description, fn in MIGRATIONS:
        if version <= current_version(conn):
            continue
        if version == 1:
            # executescript tự COMMIT nên không bọc transaction được; baseline idempotent nên chạy lại vẫn an toàn.
            fn(conn)
            record(version, description)
            applied.append(version)
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            if version <= current_version(conn):  # tiến trình khác vừa migrate xong
                conn.execute("COMMIT")
                continue
            fn(conn)
            record(version, description)
            conn.execute("COMMIT")
            applied.append(version)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return applied
