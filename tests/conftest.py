from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.main import create_app  # noqa: E402
from shared.config import Settings  # noqa: E402
from worker.api_client import ApiClient  # noqa: E402
from worker.backends.mock import MockBackend  # noqa: E402
from worker.worker import Worker  # noqa: E402

DIT_SMALL = "acestep-v15-turbo"
DIT_XL = "acestep-v15-xl-sft"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        mock_dit_models=[DIT_SMALL, DIT_XL],
        mock_lm_models=["none", "acestep-5Hz-lm-0.6B"],
        mock_delay_min=0, mock_delay_max=0, mock_train_seconds=0,
        worker_id="test-worker",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def worker(settings: Settings, client: TestClient) -> Worker:
    """Worker thật, nói chuyện với API qua HTTP (TestClient), backend mock không chờ."""
    w = Worker(settings, MockBackend(settings, sleep=lambda s: None), ApiClient("", client=client))
    w.register_with_retry()
    return w


def make_wav_bytes(seconds: float = 2.0, freq: float = 440.0, rate: int = 8000) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    pcm = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def drain(worker: Worker) -> int:
    n = 0
    while worker.run_once():
        n += 1
    return n
