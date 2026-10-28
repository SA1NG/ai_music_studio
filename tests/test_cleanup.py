"""clean_mock_data: xóa đúng dữ liệu mock và mọi thứ phụ thuộc vào nó, giữ dữ liệu thật."""
from __future__ import annotations

from api.cleanup import execute_cleanup, plan_cleanup
from api.db import Database
from api.storage import Storage


def write(storage, rel, data=b"x"):
    p = storage.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_cleanup_cascades_and_keeps_real_data(tmp_path):
    db = Database(tmp_path / "app.db")
    st = Storage(tmp_path / "storage")
    job = db.create_job("generate", {})
    mock_t = db.create_track("m.wav", "uploads/m.wav", 1.0, source="mock")
    real_t = db.create_track("r.wav", "uploads/r.wav", 1.0, source="ace")
    ds_mixed = db.create_dataset("mixed", 1, [mock_t, real_t], "datasets/mixed_v1.zip")
    ds_real = db.create_dataset("real", 1, [real_t], "datasets/real_v1.zip")
    lora_from_mixed = db.create_lora({"name": "a", "style_name": "s", "base_model": "m", "dataset_id": ds_mixed,
                                      "params": {}, "path": "loras/lora_job000001/adapter.safetensors", "source": "ace"})
    lora_mock = db.create_lora({"name": "b", "style_name": "s", "base_model": "m", "dataset_id": ds_real,
                                "params": {}, "path": "loras/lora_job000002/adapter.safetensors", "source": "mock"})
    lora_real = db.create_lora({"name": "c", "style_name": "s", "base_model": "m", "dataset_id": ds_real,
                                "params": {}, "path": "loras/lora_job000003/adapter.safetensors", "source": "ace"})

    def gen(backend, lora_id, name):
        return db.create_generation({"job_id": job, "dit_model": "m", "audio_path": f"outputs/{name}.wav",
                                     "params": {}, "backend": backend, "lora_id": lora_id})
    g_mock = gen("mock", None, "g1")
    g_ace_on_mock_lora = gen("ace", lora_mock, "g2")
    g_ace_on_cascaded = gen("ace", lora_from_mixed, "g3")
    g_real = gen("ace", lora_real, "g4")

    for rel in ["uploads/m.wav", "uploads/r.wav", "datasets/mixed_v1.zip", "datasets/real_v1.zip",
                *[f"outputs/g{i}.wav" for i in range(1, 5)]]:
        write(st, rel)
    for n in (1, 2, 3):
        write(st, f"loras/lora_job00000{n}/adapter.safetensors")
        write(st, f"loras/lora_job00000{n}/metadata.json")

    plan = plan_cleanup(db)
    assert [t["id"] for t in plan.tracks] == [mock_t]
    assert [d["id"] for d in plan.datasets] == [ds_mixed]
    assert {x["id"] for x in plan.loras} == {lora_from_mixed, lora_mock}
    assert {g["id"] for g in plan.generations} == {g_mock, g_ace_on_mock_lora, g_ace_on_cascaded}
    assert "Track mock: 1" in plan.summary()

    removed = execute_cleanup(db, st, plan)
    assert removed == 1 + 1 + 4 + 3  # track, zip, 2 thư mục LoRA (2 file mỗi cái), 3 audio
    assert [t["id"] for t in db.list_tracks()] == [real_t]
    assert [d["id"] for d in db.list_datasets()] == [ds_real]
    assert [x["id"] for x in db.list_loras()] == [lora_real]
    assert [g["id"] for g in db.list_generations()] == [g_real]
    assert st.resolve("uploads/r.wav").exists() and not st.resolve("uploads/m.wav").exists()
    assert st.resolve("loras/lora_job000003/metadata.json").exists()
    assert not st.resolve("loras/lora_job000002").exists()
    assert plan_cleanup(db).empty


def test_nothing_to_clean(tmp_path):
    db = Database(tmp_path / "app.db")
    db.create_track("r.wav", "uploads/r.wav", 1.0)
    assert plan_cleanup(db).empty
