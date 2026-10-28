from unittest.mock import patch

import pytest

from ui import app


def test_select_adapter_from_another_base_updates_underlying_choices():
    def loras(base):
        return [(app.NO_LORA, 0), ("Rain style", 42)] if base == "second-base" else [(app.NO_LORA, 0)]

    with patch.object(app, "lora_choices", side_effect=loras):
        choices = app.combined_model_choices(["first-base", "second-base"])
        value = app.model_value("second-base", 42)
        assert any(key == value for _, key in choices)
        dit, adapter, panel, strength = app.on_model_change(value)
    assert dit["value"] == "second-base"
    assert dit["choices"] == ["second-base"]
    assert adapter["value"] == 42
    assert ("Rain style", 42) in adapter["choices"]
    assert panel["label"] == "Model · second-base"
    assert strength["interactive"]


def test_select_base_clears_previous_adapter():
    with patch.object(app, "lora_choices", return_value=[(app.NO_LORA, 0)]):
        dit, adapter, panel, strength = app.on_model_change(app.model_value("base"))
    assert dit["value"] == "base"
    assert adapter["value"] == 0
    assert not strength["interactive"]
    assert panel["label"] == "Model · base"


def test_background_loading_refreshes_models_without_unlocking_active_job(monkeypatch):
    monkeypatch.delenv("MUSIC_STUDIO_SESSION_STARTED", raising=False)
    empty = dict(workers_online=0, dit_models=[], lm_models=[], warnings=[])
    ready = dict(workers_online=1, dit_models=[{"name": "base", "is_default": True}],
                 lm_models=[{"name": "none"}], warnings=[])
    with patch.object(app.api, "models", side_effect=[empty, ready, ready]), \
         patch.object(app.api, "engine", return_value={"managed": False}), \
         patch.object(app, "feed_items", return_value=[]), \
         patch.object(app.api, "loras", return_value=[]):
        loading = app.on_tick_create(None, None, "", False)
        assert not loading[7]["interactive"]
        loaded = app.on_tick_create(None, None, loading[-1], False)
        assert loaded[2]["value"] == app.model_value("base")
        assert loaded[7]["interactive"]
        assert loaded[8]["label"] == "Model · base"
        running = app.on_tick_create(app.model_value("base"), "none", loaded[-1], True)
        assert not running[7]["interactive"]
        assert "value" not in running[2]  # Don't reset selection every poll.


def test_previous_session_worker_does_not_mark_new_startup_ready(monkeypatch):
    monkeypatch.setenv("MUSIC_STUDIO_SESSION_STARTED", "2000000000")
    models = dict(workers_online=1, dit_models=[{"name": "base"}], lm_models=[], warnings=[])
    workers = {"workers": [{"online": True, "capabilities": ["generate"],
                            "last_seen": "2026-01-01T00:00:00+00:00"}]}
    with patch.object(app.api, "models", return_value=models), \
         patch.object(app.api, "engine", return_value={"managed": False}), \
         patch.object(app.api, "system", return_value=workers), \
         patch.object(app, "feed_items", return_value=[]):
        result = app.on_tick_create(None, None, "", False)
    assert not result[7]["interactive"]


def test_model_choices_expose_only_current_default_and_its_adapters():
    models = dict(dit_models=[{"name": "other", "is_default": False},
                             {"name": "active", "is_default": True}],
                  lm_models=[{"name": "none", "is_default": True}])
    with patch.object(app.api, "models", return_value=models):
        dit, default, _, _ = app.model_choices()
    assert dit == ["active"] and default == "active"


def test_reuse_of_another_base_requires_restart():
    with patch.object(app.api, "generation", return_value={"dit_model": "old-base"}), \
         patch.object(app.api, "models", return_value={"dit_models": [{"name": "active", "is_default": True}]}):
        with pytest.raises(app.gr.Error, match="model"):
            app.on_reuse(1)


def test_draft_engine_choices_are_not_reset_by_polling():
    state = dict(managed=True, status="ready", dit="base", lm="none",
                 dit_models=["base", "other"], lm_models=["none", "lm-small"])
    with patch.object(app.api, "engine", return_value=state), patch.object(app.api, "apply_engine") as apply:
        initial = app.on_engine_options("")
        following = app.on_engine_options(initial[-1])
        assert "value" not in following[0] and "value" not in following[1]
        apply.assert_not_called()


def test_model_title_waits_for_engine_ready_instead_of_showing_target():
    models = dict(workers_online=1, dit_models=[{"name": "old", "is_default": True}],
                  lm_models=[{"name": "none", "is_default": True}], warnings=[])
    state = dict(managed=True, status="loading", dit="new", lm="none", revision=2)
    with patch.object(app.api, "engine", return_value=state), \
         patch.object(app.api, "models", return_value=models), \
         patch.object(app, "feed_items", return_value=[]), \
         patch.object(app.api, "loras", return_value=[]):
        initial = app.on_load_create()
        assert initial[5]["value"] is None and initial[6]["label"] == "Model"
        loading = app.on_tick_create(app.model_value("old"), "none", "", False)
        assert loading[8]["label"] == "Model"
        state.update(status="ready")
        models["dit_models"] = [{"name": "new", "is_default": True}]
        ready = app.on_tick_create(None, "none", loading[-1], False)
        assert ready[8]["label"] == "Model · new"


def test_idle_engine_allows_configuration_but_not_generation(monkeypatch):
    monkeypatch.delenv("MUSIC_STUDIO_SESSION_STARTED", raising=False)
    state = dict(managed=True, status="idle", dit="base", lm="none", revision=1,
                 dit_models=["base"], lm_models=["none"])
    stale_models = dict(workers_online=1, dit_models=[{"name": "base"}], lm_models=[{"name": "none"}], warnings=[])
    with patch.object(app.api, "engine", return_value=state), \
         patch.object(app.api, "models", return_value=stale_models), \
         patch.object(app, "feed_items", return_value=[]):
        options = app.on_engine_options("")
        assert options[0]["interactive"] and options[1]["interactive"] and options[2]["interactive"]
        generation = app.on_tick_create(None, "none", "", False)
        assert not generation[7]["interactive"]
        assert generation[2]["choices"] == []
        assert generation[8]["label"] == "Model"
