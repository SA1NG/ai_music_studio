"""Luồng đầu-cuối qua HTTP: API thật (TestClient) + worker thật + MockBackend."""
from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

from api.main import create_app

from .conftest import DIT_SMALL, DIT_XL, drain, make_wav_bytes


def test_models_come_from_worker_registration(client, worker):
    data = client.get("/models").json()
    assert [m["name"] for m in data["dit_models"]] == [DIT_SMALL, DIT_XL]
    assert data["backends"] == ["mock"] and data["workers_online"] == 1


def test_generate_flow_records_everything(client, worker):
    r = client.post("/generate", json={"prompt": "lofi piano, instrumental", "prompt_original": "lofi buổi tối",
                                       "dit_model": DIT_SMALL, "lm_model": "acestep-5Hz-lm-0.6B",
                                       "duration": 10, "bpm": 85, "key_scale": "A minor", "seed": 11})
    job_id = r.json()["job_id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "queued"
    assert drain(worker) == 1
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done" and job["progress"] == 1.0 and "seed=11" in job["log"]
    g = client.get("/generations").json()[0]
    assert g["job_id"] == job_id and g["prompt_original"] == "lofi buổi tối"
    assert g["prompt_final"] == "lofi piano, instrumental"
    assert (g["dit_model"], g["lm_model"], g["lora_id"]) == (DIT_SMALL, "acestep-5Hz-lm-0.6B", None)
    assert g["params"]["seed"] == 11 and g["params"]["bpm"] == 85
    audio = client.get(f"/files/{g['audio_path']}")
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert client.post(f"/generations/{g['id']}/rating", json={"rating": 1}).json()["rating"] == 1
    assert client.post(f"/generations/{g['id']}/rating", json={"rating": 5}).status_code == 422


def test_title_saved_and_single_generation(client, worker):
    client.post("/generate", json={"prompt": "ambient", "dit_model": DIT_SMALL, "title": "Bình minh", "duration": 10})
    assert client.post("/generate", json={"prompt": "x", "dit_model": DIT_SMALL, "duration": 5}).status_code == 422
    drain(worker)
    g = client.get("/generations").json()[0]
    assert g["params"]["title"] == "Bình minh"
    assert client.get(f"/generations/{g['id']}").json() == g
    assert client.get("/generations/9999").status_code == 404


def test_unknown_model_rejected(client, worker):
    assert client.post("/generate", json={"prompt": "x", "dit_model": "nope"}).status_code == 400


def test_job_waits_in_queue_without_worker(settings, client):
    # Không worker nào: job nằm queued
    job_id = client.post("/generate", json={"prompt": "x", "dit_model": DIT_SMALL}).json()["job_id"]
    assert client.get(f"/jobs/{job_id}").json()["status"] == "queued"
    # Bật worker sau -> job tự chạy
    from worker.api_client import ApiClient
    from worker.backends.mock import MockBackend
    from worker.worker import Worker
    w = Worker(settings, MockBackend(settings, sleep=lambda s: None), ApiClient("", client=client))
    w.register_with_retry()
    drain(w)
    assert client.get(f"/jobs/{job_id}").json()["status"] == "done"


def test_label_dataset_train_lora_flow(client, worker):
    files = [("files", (f"song{i}.wav", make_wav_bytes(1.0 + i, 300 + 50 * i), "audio/wav")) for i in range(3)]
    tracks = client.post("/tracks", files=files).json()
    assert len(tracks) == 3 and tracks[0]["duration"] == 1.0
    assert drain(worker) == 3  # 3 job autolabel
    t = client.get(f"/tracks/{tracks[0]['id']}").json()
    assert t["auto_labels"]["source"] == "mock" and t["caption"] == ""  # gợi ý không ghi đè nhãn

    assert client.post("/datasets", json={"name": "chill"}).status_code == 400  # chưa approved
    client.put(f"/tracks/{tracks[0]['id']}", json={"review_status": "approved"})  # duyệt nhưng chưa có caption
    r = client.post("/datasets", json={"name": "chill"})
    assert r.status_code == 400 and "caption" in r.json()["detail"]
    for i, tr in enumerate(tracks[:2]):
        r = client.put(f"/tracks/{tr['id']}", json={"caption": f"lofi piano {i}, instrumental", "bpm": 80 + i,
                                                    "key_scale": "C major", "tags": ["lofi"], "review_status": "approved"})
        assert r.json()["review_status"] == "approved"
    client.put(f"/tracks/{tracks[2]['id']}", json={"review_status": "rejected"})

    ds = client.post("/datasets", json={"name": "chill"}).json()
    assert ds["version"] == 1 and len(ds["track_ids"]) == 2
    assert client.post("/datasets", json={"name": "chill"}).json()["version"] == 2
    z = client.get(f"/datasets/{ds['id']}/download")
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
        stem = manifest["tracks"][0]["audio"].rsplit(".", 1)[0]
        assert {f"{stem}.wav", f"{stem}.caption.txt", f"{stem}.json"} <= set(names)
        # nhạc không lời: không có lyrics.txt, không có "language" (cả hai làm ACE-Step coi là có lời)
        assert not any(n.endswith(".lyrics.txt") for n in names)
        meta = json.loads(zf.read(f"{stem}.json"))
        assert meta["bpm"] == 80 and meta["keyscale"] == "C major" and "language" not in meta
    assert len(manifest["tracks"]) == 2

    # Train 2 LoRA từ cùng dataset, base_model khác nhau, cùng style
    assert client.post("/train", json={"dataset_id": ds["id"], "style_name": "chill"}).status_code == 422
    for base in (DIT_SMALL, DIT_XL):
        r = client.post("/train", json={"dataset_id": ds["id"], "base_model": base, "style_name": "chill",
                                        "params": {"rank": 4, "learning_rate": 0.0001, "epochs": 2}})
        assert r.status_code == 200
    assert drain(worker) == 2
    train_job = client.get("/jobs?type=train").json()[0]
    assert train_job["status"] == "done" and "loss=" in train_job["log"]

    all_loras = client.get("/loras").json()
    assert len(all_loras) == 2 and {x["style_name"] for x in all_loras} == {"chill"}
    small = client.get(f"/loras?base_model={DIT_SMALL}").json()
    assert len(small) == 1 and small[0]["base_model"] == DIT_SMALL
    assert client.get(f"/files/{small[0]['path']}").status_code == 200

    # LoRA chỉ dùng được với đúng base_model
    bad = client.post("/generate", json={"prompt": "x", "dit_model": DIT_XL, "lora_id": small[0]["id"]})
    assert bad.status_code == 400
    ok = client.post("/generate", json={"prompt": "x", "dit_model": DIT_SMALL, "lora_id": small[0]["id"],
                                        "lora_scale": 0.7, "duration": 10, "seed": 3})
    assert ok.status_code == 200
    drain(worker)
    g = client.get("/generations").json()[0]
    assert g["lora_id"] == small[0]["id"] and g["lora_scale"] == 0.7


def test_upload_rejects_non_audio(client):
    r = client.post("/tracks", files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 400


def test_path_traversal_blocked(client):
    for p in ["..%2F..%2Fapp.db", "..%2Fapp.db", "%2Fetc%2Fpasswd", "uploads%2F..%2F..%2Fapp.db",
              "C:%5CWindows%5Cwin.ini"]:
        assert client.get(f"/files/{p}").status_code in (400, 404), p
    assert client.get("/files/uploads/khong-co.wav").status_code == 404


def test_token_protection(settings):
    settings.api_token = "s3cret"
    c = TestClient(create_app(settings))
    assert c.get("/health").status_code == 200  # công khai
    assert c.get("/models").status_code == 401
    assert c.get("/models", headers={"X-Token": "wrong"}).status_code == 401
    assert c.get("/models", headers={"X-Token": "s3cret"}).status_code == 200


def test_release_returns_job_to_queue(client, worker):
    job_id = client.post("/generate", json={"prompt": "x", "dit_model": DIT_SMALL}).json()["job_id"]
    assert client.post("/worker/claim", json={"worker_id": "w2", "types": ["generate"]}).json()["id"] == job_id
    assert client.post(f"/worker/jobs/{job_id}/release").status_code == 200
    assert client.get(f"/jobs/{job_id}").json()["status"] == "queued"
    assert client.post(f"/worker/jobs/{job_id}/release").status_code == 409  # không còn running
    drain(worker)
    assert client.get(f"/jobs/{job_id}").json()["status"] == "done"


def test_failed_job_reports_error(client, worker):
    # Ép lỗi: payload thiếu dữ liệu -> backend raise -> job failed kèm error
    client.app.state.db.create_job("train", {"dataset_file": "datasets/khong-co.zip"})
    drain(worker)
    job = client.get("/jobs?type=train").json()[0]
    assert job["status"] == "failed" and job["error"]
