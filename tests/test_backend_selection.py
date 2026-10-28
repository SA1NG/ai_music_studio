"""Chọn backend theo loại job: GENERATE_BACKEND / AUTOLABEL_BACKEND / TRAIN_BACKEND (+ BACKEND cũ)."""
from __future__ import annotations

import pytest

from shared.config import Settings
from shared.schemas import ModelInfo
from worker.api_client import ApiClient
from worker.backends import make_backends
from worker.backends.base import Backend
from worker.backends.mock import MockBackend
from worker.worker import Worker

from .conftest import DIT_SMALL, drain


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for k in ("BACKEND", "GENERATE_BACKEND", "AUTOLABEL_BACKEND", "TRAIN_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "none.env"))
    return monkeypatch


def test_legacy_backend_is_default_for_all(clean_env):
    clean_env.setenv("BACKEND", "ace")
    assert Settings.from_env().backends == {"generate": "ace", "autolabel": "ace", "train": "ace"}


def test_per_type_overrides_legacy(clean_env):
    clean_env.setenv("BACKEND", "mock")
    clean_env.setenv("GENERATE_BACKEND", "ace")
    assert Settings.from_env().backends == {"generate": "ace", "autolabel": "mock", "train": "mock"}


def test_default_is_mock(clean_env):
    assert set(Settings.from_env().backends.values()) == {"mock"}


def test_make_backends_shares_instances(settings):
    settings.generate_backend, settings.autolabel_backend, settings.train_backend = "ace", "mock", "mock"
    b = make_backends(settings)
    assert b["generate"].name == "ace" and b["train"].name == "mock"
    assert b["autolabel"] is b["train"]  # cùng tên -> cùng instance
    settings.train_backend = "gpu"
    with pytest.raises(ValueError):
        make_backends(settings)


class Recording(Backend):
    """Backend giả ghi lại job nó nhận, để kiểm tra định tuyến."""
    capabilities = ["generate", "autolabel", "train"]

    def __init__(self, name, inner):
        self.name, self.inner, self.seen = name, inner, []

    def list_models(self):
        return [ModelInfo(name=f"{self.name}-dit", is_default=True)], [ModelInfo(name="none", is_default=True)]

    def _run(self, kind, ctx):
        self.seen.append(kind)
        return getattr(self.inner, kind)(ctx)

    def generate(self, ctx): return self._run("generate", ctx)
    def autolabel(self, ctx): return self._run("autolabel", ctx)
    def train(self, ctx): return self._run("train", ctx)


def test_worker_routes_jobs_and_reports_backends(settings, client):
    inner = MockBackend(settings, sleep=lambda s: None)
    gen, rest = Recording("ace", inner), Recording("mock", inner)
    w = Worker(settings, {"generate": gen, "autolabel": rest, "train": rest}, ApiClient("", client=client))
    w.register_with_retry()

    m = client.get("/models").json()
    assert [x["name"] for x in m["dit_models"]] == ["ace-dit"]  # model lấy từ backend sinh nhạc
    assert m["backends"] == ["ace"]
    assert m["backends_by_type"] == {"autolabel": ["mock"], "generate": ["ace"], "train": ["mock"]}
    worker_row = client.get("/system").json()["workers"][0]
    assert worker_row["backends"] == {"generate": "ace", "autolabel": "mock", "train": "mock"}

    client.post("/generate", json={"prompt": "x", "dit_model": "ace-dit", "duration": 10})
    client.post("/tracks", files=[("files", ("a.wav", open(__file__, "rb").read()[:0] or b"RIFF", "audio/wav"))])
    drain(w)
    assert gen.seen == ["generate"] and rest.seen == ["autolabel"]
    g = client.get("/generations").json()[0]
    assert g["backend"] == "mock"  # MockBackend bên trong báo backend thật đã chạy


def test_worker_runs_one_job_at_a_time(settings, client, worker):
    for _ in range(3):
        client.post("/generate", json={"prompt": "x", "dit_model": DIT_SMALL, "duration": 10})
    assert worker.run_once() is True
    statuses = sorted(j["status"] for j in client.get("/jobs?type=generate").json())
    assert statuses == ["done", "queued", "queued"]  # mỗi lần run_once đúng một job
    assert drain(worker) == 2


def test_worker_without_supported_types_registers_no_models(settings, client):
    settings.worker_types = ["train"]
    w = Worker(settings, MockBackend(settings, sleep=lambda s: None), ApiClient("", client=client))
    assert w.types == ["train"]
    assert w.registration()["backends"] == {"train": "mock"}
