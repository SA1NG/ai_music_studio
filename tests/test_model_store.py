import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.ace_models import catalog, download
from scripts.desktop import Controller
from scripts.model_store import ModelStore


@pytest.fixture
def store(tmp_path):
    controller = Mock()
    controller.engine_dir.return_value = tmp_path / "engine"
    controller.settings = SimpleNamespace(api_url="http://127.0.0.1:8000", acestep_url="http://127.0.0.1:8001")
    controller.ui_url = "http://127.0.0.1:7860"
    controller.processes = []
    controller.resource_extras = {}
    controller.assert_port_free = Mock()
    controller.logs = tmp_path / "logs"
    controller.logs.mkdir()
    controller.operation_lock = threading.RLock()
    controller.checkpoint_complete = Controller.checkpoint_complete
    result = ModelStore(controller)
    result.root.mkdir(parents=True)
    return result


def test_catalog_includes_bundled_dit_lm_but_not_shared_support_models():
    downloader = SimpleNamespace(SUBMODEL_REGISTRY={"acestep-v15-base": "repo"},
                                 MAIN_MODEL_COMPONENTS=["acestep-v15-turbo", "acestep-5Hz-lm-1.7B", "vae", "Qwen3-Embedding-0.6B"])
    entries = catalog(downloader)
    assert {e["name"] for e in entries} == {"acestep-v15-base", "acestep-v15-turbo", "acestep-5Hz-lm-1.7B"}


def test_bundled_model_download_does_not_fetch_entire_package(tmp_path, monkeypatch):
    snapshot = Mock()
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot))
    downloader = SimpleNamespace(SUBMODEL_REGISTRY={}, MAIN_MODEL_COMPONENTS=["acestep-5Hz-lm-1.7B"], MAIN_MODEL_REPO="main-repo")
    download("acestep-5Hz-lm-1.7B", tmp_path, downloader)
    assert snapshot.call_args.kwargs["allow_patterns"] == ["acestep-5Hz-lm-1.7B/*"]


def test_resume_bypasses_directory_exists_shortcut(tmp_path):
    downloader = SimpleNamespace(SUBMODEL_REGISTRY={"acestep-v15-base": "repo"}, MAIN_MODEL_COMPONENTS=[],
                                 download_submodel=Mock(return_value=(True, "ok")))
    download("acestep-v15-base", tmp_path, downloader)
    assert downloader.download_submodel.call_args.kwargs["force"] is True


@pytest.mark.parametrize("name", ["../checkpoints", "vae", "Qwen3-Embedding-0.6B", "acestep-test/../../data"])
def test_delete_rejects_unmanaged_or_escaping_paths(store, name):
    with pytest.raises(ValueError):
        store.delete(name)


def test_delete_only_selected_model_and_requires_stopped_services(store):
    target = store.root / "acestep-test"
    target.mkdir()
    (target / "model.safetensors").write_bytes(b"weights")
    support = store.root / "vae"
    support.mkdir()
    store.models = [dict(name="acestep-test")]
    store.controller.processes = [("engine", Mock())]
    with pytest.raises(RuntimeError):
        store.delete("acestep-test")
    assert target.exists()
    store.controller.processes = []
    store.delete("acestep-test")
    assert not target.exists() and support.exists()


def test_cancel_stops_download_process_and_preserves_partial_files(store):
    target = store.root / "acestep-test"
    target.mkdir()
    partial = target / "download.incomplete"
    partial.write_bytes(b"partial")
    helper = store.engine / "fake_download.py"
    helper.write_text("import time; time.sleep(60)")
    store.helper = helper
    store.python = Path(sys.executable)
    store.models = [dict(name="acestep-test", local_only=False)]
    cancel = threading.Event()
    timer = threading.Timer(.3, cancel.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            store.download("acestep-test", cancel, lambda size: None)
    finally:
        timer.cancel()
    assert partial.read_bytes() == b"partial"


def test_custom_checkpoint_directory_is_resolved_relative_to_engine(store):
    (store.engine / ".env").write_text("ACESTEP_CHECKPOINTS_DIR=shared-models\n")
    other = ModelStore(store.controller)
    assert other.root == (store.engine / "shared-models").resolve()


def test_external_running_server_prevents_deleting_model(store):
    target = store.root / "acestep-test"
    target.mkdir()
    store.models = [dict(name="acestep-test")]
    store.controller.assert_port_free.side_effect = RuntimeError("port in use")
    with pytest.raises(RuntimeError):
        store.delete("acestep-test")
    assert target.is_dir()
