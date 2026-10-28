"""Lifecycle safeguards for the Windows desktop controller (no GPU required)."""
import socket
from unittest.mock import Mock

import httpx
import pytest

from scripts import desktop


def test_busy_port_is_not_claimed_or_stopped():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(RuntimeError, match="phiên khác"):
            desktop.Controller.assert_port_free(f"http://127.0.0.1:{listener.getsockname()[1]}")
        # The existing listener remains intact.
        assert listener.fileno() >= 0


def test_failed_start_cleans_up_owned_processes(settings, monkeypatch):
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop, "reload_settings", lambda: settings)
    controller = desktop.Controller()
    monkeypatch.setattr(controller, "assert_port_free", lambda url: None)
    monkeypatch.setattr(controller, "spawn", Mock())
    monkeypatch.setattr(controller, "wait", Mock(side_effect=RuntimeError("failed startup")))
    cleanup = Mock()
    monkeypatch.setattr(controller, "stop", cleanup)
    with pytest.raises(RuntimeError, match="failed startup"):
        controller.start()
    cleanup.assert_called_once()


def test_wait_does_not_accept_health_without_loaded_lm(settings, monkeypatch):
    settings.generate_backend = "ace"
    engine = settings.data_dir / "engine"
    (engine / ".venv/Scripts").mkdir(parents=True)
    (engine / ".venv/Scripts/acestep-api.exe").touch()
    (engine / ".env").write_text("ACESTEP_INIT_LLM=true\n", encoding="utf-8")
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop, "reload_settings", lambda: settings)
    controller = desktop.Controller()
    monkeypatch.setattr(controller, "engine_dir", lambda: engine)
    monkeypatch.setattr(controller, "assert_port_free", lambda url: None)
    monkeypatch.setattr(controller, "spawn", Mock())
    checks = []
    monkeypatch.setattr(controller, "configure_engine", lambda: {"dit": "acestep-v15-turbo", "lm": "acestep-5Hz-lm-0.6B", "revision": 1})
    monkeypatch.setattr(controller, "engine_post", Mock())
    monkeypatch.setattr(controller, "start_engine_monitor", Mock())
    def wait(url, check, timeout):
        if url == settings.acestep_url + "/health":
            for dit, lm in [(False, False), (True, False), (True, True)]:
                response = httpx.Response(200, json={"data": {"models_initialized": dit, "llm_initialized": lm,
                                                             "loaded_model": "acestep-v15-turbo",
                                                             "loaded_lm_model": "acestep-5Hz-lm-0.6B" if lm else None}})
                checks.append(bool(check(response)))
    monkeypatch.setattr(controller, "wait", wait)
    controller.launch_engine("acestep-v15-turbo", "acestep-5Hz-lm-0.6B")
    assert checks == [False, False, True]


def test_stop_only_targets_owned_trees_in_reverse_order(settings, monkeypatch):
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop, "reload_settings", lambda: settings)
    controller = desktop.Controller()
    processes = [Mock(pid=101), Mock(pid=202)]
    for process in processes:
        process.poll.return_value = None
    controller.processes = list(zip(["api", "worker"], processes))
    calls = Mock()
    monkeypatch.setattr(controller, "terminate_owned", calls)
    controller.stop()
    assert [call.args[0].pid for call in calls.call_args_list] == [202, 101]
    assert not controller.processes


def test_engine_environment_is_separate_from_client(settings, monkeypatch):
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop, "reload_settings", lambda: settings)
    monkeypatch.setenv("ACESTEP_API_KEY", "")
    monkeypatch.setenv("ACESTEP_LM_BACKEND", "wrong-client-value")
    monkeypatch.setattr(desktop.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    spawn = Mock()
    monkeypatch.setattr(desktop.subprocess, "Popen", spawn)
    monkeypatch.setattr(desktop, "OwnedProcessTree", Mock())
    controller = desktop.Controller()
    controller.spawn("engine", ["engine"], settings.data_dir,
                     {"ACESTEP_LM_BACKEND": "pt"}, isolate_engine=True)
    env = spawn.call_args.kwargs["env"]
    assert "ACESTEP_API_KEY" not in env
    assert env["ACESTEP_LM_BACKEND"] == "pt"


def test_start_opens_interface_without_loading_any_model(settings, monkeypatch):
    settings.generate_backend = "ace"
    engine = settings.data_dir / "engine"
    (engine / ".venv/Scripts").mkdir(parents=True)
    (engine / ".venv/Scripts/acestep-api.exe").touch()
    (engine / ".env").write_text("ACESTEP_INIT_LLM=false\n", encoding="utf-8")
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop, "reload_settings", lambda: settings)
    controller = desktop.Controller()
    monkeypatch.setattr(controller, "engine_dir", lambda: engine)
    monkeypatch.setattr(controller, "assert_port_free", lambda url: None)
    events = []
    monkeypatch.setattr(controller, "spawn", lambda name, *args, **kw: events.append(name))
    monkeypatch.setattr(controller, "wait", lambda *args: None)
    monkeypatch.setattr(controller, "configure_engine", lambda: {"dit": "acestep-v15-turbo", "lm": "none", "revision": 1})
    monkeypatch.setattr(controller, "engine_post", Mock())
    monkeypatch.setattr(controller, "start_engine_monitor", Mock())
    load = Mock()
    monkeypatch.setattr(controller, "launch_engine", load)

    def open_ui():
        assert controller.ui_ready
        assert events == ["api", "ui"]
        events.append("browser")

    controller.start(on_ui_ready=open_ui)
    assert events == ["api", "ui", "browser", "worker"]
    load.assert_not_called()
    controller.engine_post.assert_not_called()


def test_manual_restart_stops_only_engine_and_waits_for_worker(settings, monkeypatch):
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop, "reload_settings", lambda: settings)
    c = desktop.Controller()
    events = []
    monkeypatch.setattr(c, "stop_engine", lambda: events.append("old engine stopped"))
    monkeypatch.setattr(c, "launch_engine", lambda dit, lm: events.append((dit, lm)))
    monkeypatch.setattr(c, "wait", lambda *args: events.append("worker ready"))
    monkeypatch.setattr(c, "engine_post", lambda path, body: events.append(body["status"]))
    c.restart_engine({"dit": "new", "lm": "lm-small", "revision": 2})
    assert events == ["old engine stopped", ("new", "lm-small"), "worker ready", "ready"]


def test_failed_restart_cleans_up_partial_engine_and_allows_retry(settings, monkeypatch):
    monkeypatch.setattr(desktop, "get_settings", lambda: settings)
    monkeypatch.setattr(desktop, "reload_settings", lambda: settings)
    c = desktop.Controller()
    stop = Mock()
    monkeypatch.setattr(c, "stop_engine", stop)
    monkeypatch.setattr(c, "launch_engine", Mock(side_effect=RuntimeError("OOM")))
    post = Mock()
    monkeypatch.setattr(c, "engine_post", post)
    c.restart_engine({"dit": "new", "lm": "large", "revision": 2})
    assert stop.call_count == 2
    assert post.call_args.args[1]["status"] == "error"


@pytest.mark.parametrize("env, expected", [
    ({}, ("acestep-v15-turbo", "none")),
    ({"ACESTEP_CONFIG_PATH": "checkpoints\\acestep-v15-base\\", "ACESTEP_INIT_LLM": "true"},
     ("acestep-v15-base", "acestep-5Hz-lm-0.6B")),
    ({"ACESTEP_INIT_LLM": "1", "ACESTEP_LM_MODEL_PATH": "acestep-5Hz-lm-1.7B"},
     ("acestep-v15-turbo", "acestep-5Hz-lm-1.7B")),
    # LM path có sẵn nhưng LLM tắt -> không bật LM.
    ({"ACESTEP_INIT_LLM": "false", "ACESTEP_LM_MODEL_PATH": "acestep-5Hz-lm-1.7B"},
     ("acestep-v15-turbo", "none")),
])
def test_engine_defaults(env, expected):
    assert desktop.engine_defaults(env) == expected
