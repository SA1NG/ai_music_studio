from __future__ import annotations

import hashlib
import json
import struct
import wave
import zipfile
from pathlib import Path

from shared.config import Settings
from worker.backends.mock import MockBackend
from worker.backends.music_synth import parse_key

from .conftest import make_wav_bytes


class FakeCtx:
    def __init__(self, workdir: Path, payload: dict, files: dict[str, Path] | None = None):
        self.job_id = 1
        self.payload = payload
        self.workdir = workdir
        self.files = files or {}
        self.events: list[tuple[float, str | None]] = []

    def progress(self, value, log=None):
        self.events.append((value, log))

    def download(self, rel_path):
        return self.files[rel_path]


def backend(settings: Settings) -> MockBackend:
    return MockBackend(settings, sleep=lambda s: None)


def gen(settings, tmp_path, **kw):
    payload = {"prompt": "lofi", "dit_model": "acestep-v15-turbo", "lm_model": "none",
               "duration": 8, "bpm": 100, "key_scale": "D minor", "time_signature": "4", **kw}
    wd = tmp_path / f"wd{len(list(tmp_path.iterdir()))}"
    wd.mkdir()
    ctx = FakeCtx(wd, payload)
    return backend(settings).generate(ctx), ctx


def test_generate_wav_duration_and_seed(settings, tmp_path):
    r1, ctx = gen(settings, tmp_path, seed=7)
    r2, _ = gen(settings, tmp_path, seed=7)
    r3, _ = gen(settings, tmp_path, seed=8)
    with wave.open(str(r1.files[0])) as w:
        assert abs(w.getnframes() / w.getframerate() - 8.0) < 0.01
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
    h = [hashlib.md5(r.files[0].read_bytes()).hexdigest() for r in (r1, r2, r3)]
    assert h[0] == h[1] and h[0] != h[2]
    assert r1.result["seed"] == 7 and r1.result["dit_model"] == "acestep-v15-turbo"
    assert ctx.events and ctx.events[-1][0] > 0.9
    assert [v for v, _ in ctx.events] == sorted(v for v, _ in ctx.events)  # progress tăng dần


def test_generate_random_seed_is_reported(settings, tmp_path):
    r, _ = gen(settings, tmp_path, seed=-1)
    assert r.result["seed"] >= 0
    r2, _ = gen(settings, tmp_path, seed=r.result["seed"])
    assert r.files[0].read_bytes() == r2.files[0].read_bytes()  # tái lập được từ seed đã ghi


def test_synth_never_overflows_buffer():
    import random
    from worker.backends.music_synth import synthesize, SAMPLE_RATE
    rng = random.Random(0)
    for _ in range(60):  # lỗi nằm ở ô nhịp cuối nên bài ngắn là đủ
        dur, bpm = rng.uniform(3, 12), rng.randint(30, 300)
        ts = rng.choice(["2", "3", "4", "6"])
        out = synthesize(dur, bpm, rng.choice(["C major", "A minor", ""]), ts, rng.randint(0, 2**31))
        assert len(out) == int(SAMPLE_RATE * dur)


def test_parse_key():
    assert parse_key("F# minor") == (6, "minor")
    assert parse_key("Bb major") == (10, "major")
    assert parse_key("Am") == (9, "minor")
    assert parse_key("") == (0, "major")


def test_autolabel_reads_duration_and_marks_mock(settings, tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(make_wav_bytes(3.0))
    ctx = FakeCtx(tmp_path, {"track_id": 1, "file": "uploads/x.wav", "filename": "x.wav"},
                  {"uploads/x.wav": wav})
    labels = backend(settings).autolabel(ctx).result["auto_labels"]
    assert labels["source"] == "mock"
    assert abs(labels["duration"] - 3.0) < 0.01
    assert "instrumental" in labels["caption"]


def test_train_produces_safetensors_with_base_model(settings, tmp_path):
    zpath = tmp_path / "ds.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"tracks": [{}, {}, {}]}))
    wd = tmp_path / "wd"
    wd.mkdir()
    ctx = FakeCtx(wd, {"dataset_id": 1, "dataset_file": "datasets/ds.zip", "dataset_name": "d",
                       "dataset_version": 1, "base_model": "acestep-v15-xl-sft", "style_name": "chill",
                       "name": "chill-xl", "params": {"rank": 8, "learning_rate": 1e-4, "epochs": 5}},
                  {"datasets/ds.zip": zpath})
    res = backend(settings).train(ctx)
    weights = next(f for f in res.files if f.suffix == ".safetensors")
    raw = weights.read_bytes()
    (n,) = struct.unpack("<Q", raw[:8])
    header = json.loads(raw[8:8 + n])
    assert header["__metadata__"]["base_model"] == "acestep-v15-xl-sft"
    assert header["mock.lora_down.weight"]["shape"] == [8, 8]
    meta = json.loads(next(f for f in res.files if f.suffix == ".json").read_text(encoding="utf-8"))
    assert meta["base_model"] == "acestep-v15-xl-sft" and meta["tracks"] == 3
    losses = [float(log.split("loss=")[1]) for _, log in ctx.events if log and "loss=" in log]
    assert len(losses) >= 20 and losses[-1] < losses[0]


def test_list_models_from_settings(settings):
    dit, lm = backend(settings).list_models()
    assert [m.name for m in dit] == ["acestep-v15-turbo", "acestep-v15-xl-sft"]
    assert dit[0].is_default and [m.name for m in lm][0] == "none"
