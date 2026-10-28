"""Hàng đợi job trong SQLite: claim an toàn khi nhiều worker chạy đồng thời."""
from __future__ import annotations

import threading
from collections import Counter

from api.db import Database


def test_claim_order_and_type_filter(tmp_path):
    db = Database(tmp_path / "q.db")
    g1 = db.create_job("generate", {})
    t1 = db.create_job("train", {})
    g2 = db.create_job("generate", {})
    assert db.claim_job("w", ["train"])["id"] == t1
    assert db.claim_job("w", ["generate"])["id"] == g1  # cũ nhất trước
    job = db.claim_job("w", ["generate", "train"])
    assert job["id"] == g2 and job["status"] == "running" and job["worker_id"] == "w"
    assert db.claim_job("w", ["generate", "train", "autolabel"]) is None


def test_concurrent_claims_never_duplicate(tmp_path):
    db = Database(tmp_path / "q.db")
    n_jobs, n_workers = 60, 8
    ids = {db.create_job("generate", {"i": i}) for i in range(n_jobs)}
    claimed: list[tuple[str, int]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_workers)

    def run(wid: str):
        barrier.wait()  # cùng xuất phát để tối đa tranh chấp
        while (job := db.claim_job(wid, ["generate"])) is not None:
            with lock:
                claimed.append((wid, job["id"]))

    threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = Counter(j for _, j in claimed)
    assert set(counts) == ids
    assert all(c == 1 for c in counts.values()), "một job bị nhiều worker nhận"
    rows = db.list_jobs(limit=1000)
    assert all(r["status"] == "running" for r in rows)
    # worker_id trong DB khớp với worker đã nhận
    owner = dict((j, w) for w, j in claimed)
    assert all(r["worker_id"] == owner[r["id"]] for r in rows)


def test_requeue_when_worker_silent(tmp_path):
    db = Database(tmp_path / "q.db")
    job_id = db.create_job("generate", {})
    db.upsert_worker({"worker_id": "dead", "backend": "mock"})
    db.claim_job("dead", ["generate"])
    # Worker vừa heartbeat -> không requeue
    assert db.requeue_stale_jobs(stale_seconds=60) == 0
    # Giả lập worker im lặng: stale_seconds = 0 -> mọi heartbeat đều quá hạn
    with db.connect() as c:
        c.execute("UPDATE workers SET last_seen = '2000-01-01T00:00:00+00:00'")
        c.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00'")
    assert db.requeue_stale_jobs(stale_seconds=60) == 1
    job = db.get_job(job_id)
    assert job["status"] == "queued" and job["worker_id"] is None
    assert db.claim_job("other", ["generate"])["id"] == job_id


def test_requeue_on_worker_restart(tmp_path):
    db = Database(tmp_path / "q.db")
    job_id = db.create_job("train", {})
    db.claim_job("w1", ["train"])
    assert db.requeue_worker_jobs("w1") == 1
    assert db.get_job(job_id)["status"] == "queued"


def test_old_workers_pruned_on_register(tmp_path):
    db = Database(tmp_path / "q.db")
    for wid in ("old-idle", "old-busy"):
        db.upsert_worker({"worker_id": wid, "backend": "mock"})
    db.create_job("generate", {})
    db.claim_job("old-busy", ["generate"])
    with db.connect() as c:
        c.execute("UPDATE workers SET last_seen = '2000-01-01T00:00:00+00:00'")
    db.upsert_worker({"worker_id": "new", "backend": "mock"})
    ids = {w["worker_id"] for w in db.list_workers(30)}
    assert ids == {"new", "old-busy"}  # worker đang giữ job thì giữ lại để còn requeue


def test_progress_appends_log_only_when_running(tmp_path):
    db = Database(tmp_path / "q.db")
    job_id = db.create_job("generate", {})
    assert db.update_job_progress(job_id, 0.5, "x") is False  # chưa running
    db.claim_job("w", ["generate"])
    db.update_job_progress(job_id, 0.3, "a")
    db.update_job_progress(job_id, 0.6, "b")
    job = db.get_job(job_id)
    assert job["progress"] == 0.6 and job["log"] == "a\nb\n"


def test_active_jobs_and_counts(tmp_path):
    db = Database(tmp_path / "q.db")
    g = db.create_job("generate", {})
    db.create_job("train", {})
    db.claim_job("w", ["generate"])
    db.finish_job(g, "done")
    db.create_job("generate", {})
    assert [j["type"] for j in db.active_jobs()] == ["train", "generate"]
    counts = {(c["type"], c["status"]): c["count"] for c in db.job_counts()}
    assert counts == {("generate", "done"): 1, ("generate", "queued"): 1, ("train", "queued"): 1}


def test_requeue_helpers_log_reason(tmp_path):
    db = Database(tmp_path / "q.db")
    job_id = db.create_job("generate", {})
    db.claim_job("w1", ["generate"])
    assert db.requeue_worker_jobs("w1") == 1
    job = db.get_job(job_id)
    assert job["status"] == "queued" and job["worker_id"] is None
    assert "[requeue] worker khởi động lại" in job["log"]
    db.claim_job("w1", ["generate"])
    db.release_job(job_id)
    assert "[requeue] worker dừng" in db.get_job(job_id)["log"]


def test_auto_labels_for_missing_track_leave_no_open_transaction(tmp_path):
    db = Database(tmp_path / "q.db")
    assert db.save_track_auto_labels(999, {"source": "ace", "caption": "piano"}) is None
    db.create_job("generate", {})  # DB vẫn ghi được: không còn khóa ghi treo
