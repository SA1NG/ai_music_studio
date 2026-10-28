"""MockBackend cho máy dev không có GPU. Mọi kết quả đều đánh dấu source/backend = mock."""
from __future__ import annotations

import hashlib
import json
import math
import random
import struct
import time
import zipfile
from pathlib import Path

import numpy as np

from shared.audio import HAS_LIBROSA, audio_duration, estimate_bpm
from shared.config import Settings
from shared.schemas import NO_LM, ModelInfo

from .base import Backend, BackendResult, JobContext
from .music_synth import synthesize, write_wav

FAKE_GENRES = ["lofi hip hop", "ambient", "cinematic orchestral", "acoustic folk", "synthwave",
               "jazz piano", "chillout electronic", "meditation"]
FAKE_INSTRUMENTS = ["piano", "soft synth pad", "acoustic guitar", "strings", "electric piano",
                    "flute", "warm bass", "light drums"]
FAKE_MOODS = ["calm", "nostalgic", "uplifting", "dreamy", "melancholic", "energetic", "warm"]
FAKE_KEYS = ["C major", "A minor", "G major", "E minor", "D major", "F major", "D minor"]


def write_fake_safetensors(path: Path, rank: int, metadata: dict[str, str]) -> None:
    """File .safetensors hợp lệ về định dạng (header JSON + dữ liệu) nhưng trọng số là ngẫu nhiên."""
    rank = max(1, min(rank, 64))
    data = np.random.default_rng(0).standard_normal((rank, 8)).astype("<f4").tobytes()
    header = {"__metadata__": metadata,
              "mock.lora_down.weight": {"dtype": "F32", "shape": [rank, 8], "data_offsets": [0, len(data)]}}
    hbytes = json.dumps(header).encode()
    hbytes += b" " * (-len(hbytes) % 8)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(hbytes)))
        f.write(hbytes)
        f.write(data)


class MockBackend(Backend):
    name = "mock"
    capabilities = ["generate", "autolabel", "train"]

    def __init__(self, settings: Settings, sleep=time.sleep):
        self.settings = settings
        self._sleep = sleep  # test có thể thay bằng hàm không chờ

    def list_models(self):
        s = self.settings
        default_dit = s.default_dit_model or (s.mock_dit_models[0] if s.mock_dit_models else "")
        default_lm = s.default_lm_model or NO_LM
        dit = [ModelInfo(name=n, is_default=(n == default_dit), description="mock") for n in s.mock_dit_models]
        lm = [ModelInfo(name=n, is_default=(n == default_lm), description="mock") for n in s.mock_lm_models]
        return dit, lm

    # ---------- generate ----------
    def generate(self, ctx: JobContext) -> BackendResult:
        p = ctx.payload
        seed = int(p.get("seed", -1))
        if seed < 0:
            seed = random.randint(0, 2**31 - 1)
        rng = random.Random(seed)
        bpm = int(p.get("bpm") or rng.randint(70, 120))
        key = p.get("key_scale") or rng.choice(FAKE_KEYS)
        duration = float(p.get("duration") or 30)
        ts = str(p.get("time_signature") or "4")

        delay = rng.uniform(self.settings.mock_delay_min, self.settings.mock_delay_max)
        steps = 5
        ctx.progress(0.05, f"[mock] model={p.get('dit_model')} lm={p.get('lm_model')} seed={seed}")
        if p.get("lora"):
            ctx.progress(0.1, f"[mock] gắn LoRA '{p['lora']['name']}' scale={p.get('lora_scale')}")
        for i in range(steps):
            self._sleep(delay / steps)
            ctx.progress(0.1 + 0.7 * (i + 1) / steps, f"[mock] diffusion bước {i + 1}/{steps}")

        out = ctx.workdir / f"mock_{ctx.job_id}_{seed}.wav"
        write_wav(out, synthesize(duration, bpm, key, ts, seed))
        ctx.progress(0.95, f"[mock] đã tổng hợp {duration:.0f}s, {bpm} BPM, {key}")
        return BackendResult(
            result={"backend": self.name, "seed": seed, "dit_model": p.get("dit_model"),
                    "lm_model": p.get("lm_model", NO_LM),
                    "metas": {"bpm": bpm, "keyscale": key, "timesignature": ts, "duration": duration}},
            files=[out])

    # ---------- autolabel ----------
    def autolabel(self, ctx: JobContext) -> BackendResult:
        p = ctx.payload
        local = ctx.download(p["file"])
        ctx.progress(0.3, f"[mock] phân tích {p.get('filename')}")
        duration = audio_duration(local)
        bpm = estimate_bpm(local)
        # Caption/tags giả lập, ổn định theo tên file
        rng = random.Random(int(hashlib.md5(p.get("filename", "").encode()).hexdigest()[:8], 16))
        genre, mood = rng.choice(FAKE_GENRES), rng.choice(FAKE_MOODS)
        instruments = rng.sample(FAKE_INSTRUMENTS, 2)
        labels = {
            "source": "mock",
            "duration": duration,
            "bpm": bpm,
            "bpm_source": "librosa" if bpm is not None else None,
            "key_scale": rng.choice(FAKE_KEYS),
            "time_signature": "4",
            "caption": f"{genre}, {instruments[0]} and {instruments[1]}, {mood} mood, instrumental, no vocals",
            "tags": [genre, mood, *instruments],
            "note": "Gợi ý giả lập (mock). Chỉ duration" + (" và BPM" if bpm else "") + " là đo thật."
                    + ("" if HAS_LIBROSA else " Cài librosa để ước lượng BPM."),
        }
        ctx.progress(0.9, "[mock] xong gợi ý nhãn")
        return BackendResult(result={"auto_labels": labels})

    # ---------- train ----------
    def train(self, ctx: JobContext) -> BackendResult:
        p = ctx.payload
        params = p.get("params", {})
        if p.get("dataset_format") == "directory":
            from shared.dataset_files import local_dataset, read_manifest
            manifest = read_manifest(local_dataset(p, self.settings))
        else:
            zip_path = ctx.download(p["dataset_file"])
            with zipfile.ZipFile(zip_path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
        n_tracks = len(manifest.get("tracks", []))
        ctx.progress(0.02, f"[mock] dataset {p.get('dataset_name')} v{p.get('dataset_version')}: "
                           f"{n_tracks} track; base_model={p['base_model']}")

        steps = 25
        epochs = int(params.get("epochs", 10))
        loss = 0.0
        for i in range(steps):
            self._sleep(self.settings.mock_train_seconds / steps)
            loss = 1.2 * math.exp(-3.0 * (i + 1) / steps) + 0.08 + random.uniform(-0.02, 0.02)
            epoch = max(1, round(epochs * (i + 1) / steps))
            ctx.progress(0.02 + 0.93 * (i + 1) / steps, f"[mock] epoch {epoch}/{epochs} step {i + 1}/{steps} loss={loss:.4f}")

        meta = {"backend": "mock", "base_model": p["base_model"], "style_name": p["style_name"],
                "name": p["name"], "dataset_id": str(p["dataset_id"]),
                "dataset_version": str(p.get("dataset_version")), "rank": str(params.get("rank", 16)),
                "learning_rate": str(params.get("learning_rate")), "epochs": str(epochs)}
        weights = ctx.workdir / "adapter_model.safetensors"
        write_fake_safetensors(weights, int(params.get("rank", 16)), meta)
        meta_file = ctx.workdir / "metadata.json"
        meta_file.write_text(json.dumps({**meta, "final_loss": round(loss, 4), "tracks": n_tracks},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        return BackendResult(result={"backend": self.name, "base_model": p["base_model"],
                                     "metrics": {"final_loss": round(loss, 4)}},
                             files=[weights, meta_file])
