"""Migration: DB giai đoạn 1 (chưa có schema_version) được nâng cấp mà không mất dữ liệu."""
from __future__ import annotations

import json
import sqlite3

from api.db import Database
from api.migrations import BASELINE_SCHEMA, LATEST_VERSION


def make_v1_db(path):
    c = sqlite3.connect(path)
    c.executescript(BASELINE_SCHEMA)
    ts = "2026-10-23T00:00:00.000+00:00"
    c.execute("INSERT INTO jobs (type, status, payload, created_at, updated_at) VALUES ('generate','done','{}',?,?)", (ts, ts))
    c.execute("INSERT INTO tracks (filename, path, created_at) VALUES ('a.wav','uploads/a.wav',?)", (ts,))
    c.execute("INSERT INTO datasets (name, version, track_ids, export_path, created_at) VALUES ('d',1,'[1]','datasets/d_v1.zip',?)", (ts,))
    c.execute("INSERT INTO loras (name, style_name, base_model, dataset_id, path, created_at) "
              "VALUES ('l','s','acestep-v15-turbo',1,'loras/x.safetensors',?)", (ts,))
    c.execute("INSERT INTO generations (job_id, dit_model, params, audio_path, created_at) VALUES (1,'m',?,'outputs/g.wav',?)",
              (json.dumps({"seed": 4242, "bpm": 90}), ts))
    c.execute("INSERT INTO workers (worker_id, backend, last_seen) VALUES ('w','mock',?)", (ts,))
    c.commit()
    c.close()


def versions(path):
    return [r[0] for r in sqlite3.connect(path).execute("SELECT version FROM schema_version ORDER BY version")]


def test_fresh_db_is_latest(tmp_path):
    db = Database(tmp_path / "new.db")
    assert db.migrations_applied == list(range(1, LATEST_VERSION + 1))
    cols = {r[1] for r in sqlite3.connect(tmp_path / "new.db").execute("PRAGMA table_info(generations)")}
    assert {"backend", "seed"} <= cols


def test_v1_db_upgraded_without_data_loss(tmp_path):
    path = tmp_path / "old.db"
    make_v1_db(path)
    db = Database(path)
    assert db.migrations_applied == list(range(1, LATEST_VERSION + 1))
    assert versions(path) == list(range(1, LATEST_VERSION + 1))
    t = db.get_track(1)
    assert t["filename"] == "a.wav" and t["source"] == "mock"  # bản ghi cũ = mock
    assert t["is_instrumental"] == 1 and t["lyrics"] == "" and t["vocal_language"] == "unknown"
    assert db.get_lora(1)["source"] == "mock" and db.get_lora(1)["dataset_id"] == 1
    g = db.get_generation(1)
    assert g["backend"] == "mock" and g["seed"] == 4242 and g["params"]["bpm"] == 90  # seed điền từ params
    assert db.get_dataset(1)["track_ids"] == [1]
    assert db.list_workers(30)[0]["backends"] == {}


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "old.db"
    make_v1_db(path)
    Database(path)
    again = Database(path)
    assert again.migrations_applied == [] and versions(path) == list(range(1, LATEST_VERSION + 1))
    assert len(again.list_tracks()) == 1


def test_new_rows_get_explicit_source(tmp_path):
    db = Database(tmp_path / "x.db")
    assert db.get_track(db.create_track("b.wav", "uploads/b.wav", 1.0))["source"] == "ace"
    assert db.get_track(db.create_track("c.wav", "uploads/c.wav", 1.0, source="mock"))["source"] == "mock"
