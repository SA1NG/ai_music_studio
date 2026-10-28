"""AceStepBackend với HTTP giả lập (respx) — không cần ACE-Step thật.
Hình dạng response lấy theo mã nguồn ACE-Step 1.5 (acestep/api/http/*)."""
from __future__ import annotations

import json
import random

import httpx
import pytest
import respx

from shared.config import Settings
from worker.api_client import ApiClient
from worker.backends.acestep import (AceStepBackend, AceStepClient, AceStepError, AceStepUnavailable,
                                     parse_task_result)
from worker.backends.mock import MockBackend
from worker.worker import Worker

from .conftest import drain
from .test_mock_backend import FakeCtx

URL = "http://ace.test:8001"
TURBO = "acestep-v15-turbo"


def env(data, code=200, error=None):
    return {"data": data, "code": code, "error": error, "timestamp": 1700000000000, "extra": None}


HEALTH_OK = env({"status": "ok", "service": "ACE-Step API", "models_initialized": True, "loaded_model": TURBO})
# /v1/models liệt kê cả model CÓ TRÊN ĐĨA chưa nạp (gói tải mặc định có sẵn LM 1.7B)
MODELS = env({
    "models": [{"name": TURBO, "is_default": True, "is_loaded": True},
               {"name": "acestep-v15-xl-sft", "is_default": False, "is_loaded": False}],
    "default_model": TURBO,
    "lm_models": [{"name": "acestep-5Hz-lm-1.7B", "is_loaded": False}],
    "loaded_lm_model": None, "llm_initialized": False,
})


def make_settings(tmp_path, **kw) -> Settings:
    return Settings(data_dir=tmp_path, acestep_url=URL, acestep_api_key="secret", acestep_poll_interval=0,
                    default_dit_model=TURBO, acestep_inference_steps=0, **kw)


@pytest.fixture
def ace(tmp_path) -> AceStepBackend:
    return AceStepBackend(make_settings(tmp_path), AceStepClient(URL, "secret"), sleep=lambda s: None,
                          rng=random.Random(7))


def payload(**kw):
    return {"prompt": "ambient piano, instrumental, no vocals", "dit_model": TURBO, "lm_model": "none",
            "duration": 30, "bpm": 72, "key_scale": "C major", "time_signature": "4/4", "seed": 1234,
            "audio_format": "wav", **kw}


def test_prompt_metadata_is_not_overridden_by_request_defaults(ace):
    from shared.schemas import GenerateRequest

    prompt = "piano, 80 BPM, D minor, 3/4, instrumental"
    request = GenerateRequest(prompt=prompt, dit_model=TURBO)
    body = ace.build_release_body(request.model_dump())
    assert body["prompt"] == prompt
    assert not {"bpm", "key_scale", "time_signature"}.intersection(body)


def running(progress, text):
    return httpx.Response(200, json=env([{"task_id": "t-1", "status": 0, "progress_text": text,
                                          "result": json.dumps([{"file": "", "progress": progress,
                                                                 "stage": "running"}])}]))


# ---------------- danh sách model ----------------
@respx.mock
def test_list_models_only_loaded_dit_and_no_lm(ace):
    route = respx.get(f"{URL}/v1/model_inventory").respond(json=MODELS)
    dit, lm = ace.list_models()
    assert [m.name for m in dit] == [TURBO] and dit[0].is_default  # xl-sft trên đĩa nhưng chưa nạp -> bỏ
    assert [m.name for m in lm] == ["none"]  # LLM chưa khởi tạo -> chỉ "không dùng"
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret"


@respx.mock
def test_list_models_lazy_server_uses_default_and_loaded_lm(ace):
    data = json.loads(json.dumps(MODELS))
    for m in data["data"]["models"]:
        m["is_loaded"] = False  # server nạp lười, chưa nạp gì
    data["data"].update(llm_initialized=True, loaded_lm_model="acestep-5Hz-lm-0.6B")
    respx.get(f"{URL}/v1/model_inventory").respond(json=data)
    dit, lm = ace.list_models()
    assert [m.name for m in dit] == [TURBO]
    assert [m.name for m in lm] == ["none", "acestep-5Hz-lm-0.6B"]


@respx.mock
def test_list_models_falls_back_to_openrouter_v1_models(ace):
    """Server không có /v1/model_inventory: /v1/models trả định dạng OpenRouter/OpenAI (đúng như server thật)."""
    respx.get(f"{URL}/v1/model_inventory").respond(404, json={"detail": "Not Found"})
    respx.get(f"{URL}/v1/models").respond(json={"object": "list", "data": [
        {"id": f"acestep/{TURBO}", "name": f"ACE-Step {TURBO}", "output_modalities": ["audio"]}]})
    dit, lm = ace.list_models()
    assert [m.name for m in dit] == [TURBO] and [m.name for m in lm] == ["none"]


@respx.mock
def test_list_models_or_last_when_down(ace):
    respx.get(f"{URL}/v1/model_inventory").mock(side_effect=[httpx.Response(200, json=MODELS),
                                                    httpx.ConnectError("refused")])
    assert [m.name for m in ace.list_models_or_last()[0]] == [TURBO]
    assert [m.name for m in ace.list_models_or_last()[0]] == [TURBO]  # lần 2 server tắt -> dùng lần trước


@respx.mock
def test_list_models_or_last_never_connected(tmp_path):
    respx.get(f"{URL}/v1/model_inventory").mock(side_effect=httpx.ConnectError("refused"))
    b = AceStepBackend(make_settings(tmp_path), AceStepClient(URL))
    dit, lm = b.list_models_or_last()
    assert [m.name for m in dit] == [TURBO] and [m.name for m in lm] == ["none"]


# ---------------- sinh nhạc ----------------
@respx.mock
def test_generate_full_flow(ace, tmp_path):
    respx.get(f"{URL}/health").respond(json=HEALTH_OK)
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status())
    release = respx.post(f"{URL}/release_task").respond(
        json=env({"task_id": "t-1", "status": "queued", "queue_position": 1}))
    # server thật trả lm_model mặc định của cấu hình dù không dùng LM -> không được ghi vào lịch sử
    items = [{"file": "/v1/audio?path=%2Ftmp%2Ft-1_0.wav", "status": 1, "seed_value": "1234",
              "dit_model": TURBO, "lm_model": "acestep-5Hz-lm-0.6B",
              "metas": {"bpm": 72, "duration": 30, "keyscale": "C major", "timesignature": "4"}}]
    query = respx.post(f"{URL}/query_result").mock(side_effect=[
        running(0.3, "Diffusion step 3/8"), running(0.7, "Decoding VAE"),
        httpx.Response(200, json=env([{"task_id": "t-1", "status": 1, "result": json.dumps(items)}])),
    ])
    audio = respx.get(f"{URL}/v1/audio").respond(content=b"RIFFfake-audio")

    ctx = FakeCtx(tmp_path, payload())
    res = ace.generate(ctx)

    body = json.loads(release.calls[0].request.content)
    assert body == {
        "prompt": "ambient piano, instrumental, no vocals", "lyrics": "[Instrumental]", "audio_duration": 30.0,
        "task_type": "text2music", "vocal_language": "unknown", "guidance_scale": 7,
        "lm_temperature": 0.85, "lm_negative_prompt": "NO USER INPUT",
        "model": TURBO, "thinking": False, "use_cot_caption": False, "use_cot_language": False,
        "use_random_seed": False, "seed": 1234, "batch_size": 1, "inference_steps": 8,
        "audio_format": "wav", "bpm": 72, "key_scale": "C major", "time_signature": "4",
    }
    assert json.loads(query.calls[0].request.content) == {"task_id_list": ["t-1"]}
    assert audio.calls[0].request.url.params["path"] == "/tmp/t-1_0.wav"  # file là URL sẵn -> gọi thẳng
    assert res.files[0].read_bytes() == b"RIFFfake-audio"
    assert res.result["backend"] == "ace" and res.result["seed"] == 1234
    assert res.result["dit_model"] == TURBO and res.result["lm_model"] == "none"
    logs = [l for _, l in ctx.events if l]
    assert any("Diffusion step 3/8" in l for l in logs) and any("Decoding VAE" in l for l in logs)
    progress = [v for v, _ in ctx.events]
    assert progress == sorted(progress)  # tiến độ tăng dần theo progress của server


@respx.mock
def test_random_seed_is_chosen_locally(ace, tmp_path):
    respx.get(f"{URL}/health").respond(json=HEALTH_OK)
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status())
    release = respx.post(f"{URL}/release_task").respond(json=env({"task_id": "t-2"}))
    # đường trả kết quả qua store của ACE-Step KHÔNG có seed_value
    respx.post(f"{URL}/query_result").respond(json=env([{"task_id": "t-2", "status": 1, "result": json.dumps(
        [{"file": "/tmp/x.wav", "metas": {}}])}]))
    respx.get(f"{URL}/v1/audio").respond(content=b"x")
    res = ace.generate(FakeCtx(tmp_path, payload(seed=-1, bpm=None, key_scale="", duration=5)))
    body = json.loads(release.calls[0].request.content)
    assert body["use_random_seed"] is False and body["seed"] == random.Random(7).randint(0, 2**31 - 1)
    assert res.result["seed"] == body["seed"]  # vẫn biết seed thật dù server không trả
    assert body["audio_duration"] == 10.0  # tối thiểu 10 giây
    assert "bpm" not in body and "key_scale" not in body


@respx.mock
def test_generate_task_failed_reports_server_error(ace, tmp_path):
    respx.get(f"{URL}/health").respond(json=HEALTH_OK)
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status())
    respx.post(f"{URL}/release_task").respond(json=env({"task_id": "t-3"}))
    respx.post(f"{URL}/query_result").respond(json=env([{"task_id": "t-3", "status": 2, "result": json.dumps(
        [{"file": "", "error": "CUDA out of memory"}])}]))
    with pytest.raises(AceStepError, match="CUDA out of memory"):
        ace.generate(FakeCtx(tmp_path, payload()))


@respx.mock
def test_generate_when_acestep_down_fails_fast(ace, tmp_path):
    respx.get(f"{URL}/health").mock(side_effect=httpx.ConnectError("refused"))
    release = respx.post(f"{URL}/release_task")
    with pytest.raises(AceStepUnavailable, match="ACE-Step chưa bật"):
        ace.generate(FakeCtx(tmp_path, payload()))
    assert not release.called
    assert "ACE-Step chưa bật" in ace.status()


@respx.mock
def test_error_envelope_and_detail(ace):
    respx.get(f"{URL}/v1/model_inventory").respond(json=env(None, code=500, error="model not loaded"))
    with pytest.raises(AceStepError, match="model not loaded"):
        ace.list_models()
    respx.post(f"{URL}/release_task").respond(status_code=401, json={"detail": "Invalid API key"})
    with pytest.raises(AceStepError, match="Invalid API key"):
        ace.client.release_task({"prompt": "x"})


@respx.mock
def test_init_model(ace):
    route = respx.post(f"{URL}/v1/init").respond(json=env({"loaded_model": TURBO, "slot": 1}))
    assert ace.client.init_model(TURBO)["loaded_model"] == TURBO
    assert json.loads(route.calls[0].request.content) == {"slot": 1, "init_llm": False, "model": TURBO}


def test_parse_task_result():
    assert parse_task_result("") == []
    assert parse_task_result('[{"file": "a"}]') == [{"file": "a"}]
    assert parse_task_result('{"file": "a"}') == [{"file": "a"}]


# ---------------- tích hợp: worker + API thật (TestClient) + ACE-Step giả ----------------
def test_worker_reports_acestep_down_then_recovers(tmp_path, settings, client):
    settings.default_dit_model = TURBO
    ace_backend = AceStepBackend(make_settings(tmp_path), AceStepClient(URL), sleep=lambda s: None)
    mock = MockBackend(settings, sleep=lambda s: None)
    w = Worker(settings, {"generate": ace_backend, "autolabel": mock, "train": mock}, ApiClient("", client=client))

    with respx.mock(base_url=URL, assert_all_called=False) as ace_http:
        ace_http.get("/health").mock(side_effect=httpx.ConnectError("refused"))
        ace_http.get("/v1/model_inventory").mock(side_effect=httpx.ConnectError("refused"))
        w.register_with_retry()  # vẫn đăng ký được dù ACE-Step tắt
        m = client.get("/models").json()
        assert [x["name"] for x in m["dit_models"]] == [TURBO]
        assert any("ACE-Step chưa bật" in x for x in m["warnings"])

        job_id = client.post("/generate", json={"prompt": "x", "dit_model": TURBO, "duration": 10}).json()["job_id"]
        drain(w)
        job = client.get(f"/jobs/{job_id}").json()
        assert job["status"] == "failed" and job["error"].startswith("ACE-Step chưa bật")
        assert "Traceback" not in job["error"]

    with respx.mock(base_url=URL, assert_all_called=False) as ace_http:  # bật lại ACE-Step
        ace_http.get("/health").respond(json=HEALTH_OK)
        ace_http.get("/v1/model_inventory").respond(json=MODELS)
        ace_http.get("/v1/lora/status").respond(json=lora_status())
        ace_http.post("/release_task").respond(json=env({"task_id": "t-9"}))
        ace_http.post("/query_result").respond(json=env([{"task_id": "t-9", "status": 1, "result": json.dumps(
            [{"file": "/tmp/t9.wav", "seed_value": "5"}])}]))
        ace_http.get("/v1/audio").respond(content=b"RIFFok")
        client.post("/worker/register", json=w.registration(fresh_start=False))
        assert client.get("/models").json()["warnings"] == []
        job_id = client.post("/generate", json={"prompt": "x", "dit_model": TURBO, "duration": 10,
                                                "seed": 5}).json()["job_id"]
        drain(w)
        assert client.get(f"/jobs/{job_id}").json()["status"] == "done"
        g = client.get("/generations").json()[0]
        assert (g["backend"], g["seed"], g["dit_model"]) == ("ace", 5, TURBO)


# ---------------- LoRA khi sinh nhạc ----------------
LORA = {"id": 7, "name": "chill-turbo", "path": "loras/lora_job000003/adapter_model.safetensors",
        "base_model": TURBO, "source": "ace"}


@pytest.fixture
def ace_work(tmp_path) -> AceStepBackend:
    s = make_settings(tmp_path, acestep_work_dir=str(tmp_path / "ace_work"))
    return AceStepBackend(s, AceStepClient(URL), sleep=lambda s: None, rng=random.Random(7))


def lora_ctx(tmp_path, **kw):
    src = tmp_path / "srcfiles"
    src.mkdir(exist_ok=True)
    (src / "adapter_model.safetensors").write_bytes(b"weights")
    (src / "adapter_config.json").write_text("{}")
    files = {LORA["path"]: src / "adapter_model.safetensors",
             "loras/lora_job000003/adapter_config.json": src / "adapter_config.json"}
    return FakeCtx(tmp_path, payload(**kw), files)


def mock_generation_ok():
    respx.get(f"{URL}/health").respond(json=HEALTH_OK)
    respx.post(f"{URL}/release_task").respond(json=env({"task_id": "t-1"}))
    respx.post(f"{URL}/query_result").respond(json=env([{"task_id": "t-1", "status": 1, "result": json.dumps(
        [{"file": "/tmp/x.wav", "seed_value": "1234"}])}]))
    respx.get(f"{URL}/v1/audio").respond(content=b"RIFF")


def lora_status(loaded=False, adapters=(), use=None):
    return env({"lora_loaded": loaded, "use_lora": loaded if use is None else use, "lora_scale": 1.0,
                "adapters": list(adapters)})


def lora_routes():
    """Ghi thứ tự gọi các endpoint LoRA. /v1/lora/unload KHÔNG được gọi (hỏng ở ACE-Step commit ca1e85f)."""
    calls = []

    def rec(name):
        def handler(request):
            calls.append((name, json.loads(request.content) if request.content else None))
            return httpx.Response(200, json=env({"message": "✅"}))
        return handler
    for name in ("load", "scale", "toggle", "unload"):
        respx.post(f"{URL}/v1/lora/{name}").mock(side_effect=rec(name))
    return calls


@respx.mock
def test_generate_with_lora_loads_enables_and_scales(ace_work, tmp_path):
    mock_generation_ok()
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status())
    calls = lora_routes()
    res = ace_work.generate(lora_ctx(tmp_path, lora=LORA, lora_scale=0.6))
    lora_dir = tmp_path / "ace_work" / "loras" / "ms_lora_7"
    assert calls == [("load", {"lora_path": str(lora_dir.resolve()), "adapter_name": "ms_lora_7"}),
                     ("toggle", {"use_lora": True}),
                     ("scale", {"scale": 0.6, "adapter_name": "ms_lora_7"})]
    assert (lora_dir / "adapter_config.json").is_file() and (lora_dir / "adapter_model.safetensors").is_file()
    assert res.result["lora_adapter"] == "ms_lora_7" and res.result["lora_scale"] == 0.6


@respx.mock
def test_generate_same_lora_already_loaded_skips_load(ace_work, tmp_path):
    mock_generation_ok()
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status(True, ["ms_lora_7"], use=False))
    calls = lora_routes()
    ace_work.generate(lora_ctx(tmp_path, lora=LORA, lora_scale=1.0))
    assert [c[0] for c in calls] == ["toggle", "scale"]  # bật lại adapter đã nạp, không nạp lại


@respx.mock
def test_generate_other_lora_is_added_not_unloaded(ace_work, tmp_path):
    mock_generation_ok()
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status(True, ["ms_lora_3"]))
    calls = lora_routes()
    ace_work.generate(lora_ctx(tmp_path, lora=LORA))
    assert [c[0] for c in calls] == ["load", "toggle", "scale"]  # thêm adapter thứ hai, không unload
    assert calls[2][1]["adapter_name"] == "ms_lora_7"  # scale theo tên -> chọn adapter này


@respx.mock
def test_generate_without_lora_disables_adapter(ace_work, tmp_path):
    mock_generation_ok()
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status(True, ["ms_lora_7"]))
    calls = lora_routes()
    res = ace_work.generate(FakeCtx(tmp_path, payload()))
    assert calls == [("toggle", {"use_lora": False})] and "lora_adapter" not in res.result


@respx.mock
def test_generate_without_lora_already_disabled_does_nothing(ace_work, tmp_path):
    mock_generation_ok()
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status(True, ["ms_lora_7"], use=False))
    calls = lora_routes()
    ace_work.generate(FakeCtx(tmp_path, payload()))
    assert calls == []


@respx.mock
def test_mock_lora_rejected_on_ace(ace_work, tmp_path):
    respx.get(f"{URL}/health").respond(json=HEALTH_OK)
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status())
    with pytest.raises(AceStepError, match="không gắn được"):
        ace_work.generate(lora_ctx(tmp_path, lora={**LORA, "source": "mock"}))


@respx.mock
def test_lora_requires_work_dir(ace, tmp_path):
    respx.get(f"{URL}/health").respond(json=HEALTH_OK)
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status())
    with pytest.raises(AceStepError, match="ACESTEP_WORK_DIR"):
        ace.generate(lora_ctx(tmp_path, lora=LORA))


# ---------------- train ----------------
def train_ctx(tmp_path, base=TURBO):
    import zipfile as zf_mod
    z = tmp_path / "ds.zip"
    with zf_mod.ZipFile(z, "w") as zf:
        zf.writestr("0001_a.wav", b"RIFF")
        zf.writestr("0001_a.caption.txt", "lofi")
        zf.writestr("manifest.json", "{}")
    return FakeCtx(tmp_path, {"dataset_id": 1, "dataset_file": "datasets/ds_v1.zip", "dataset_name": "ds",
                              "dataset_version": 1, "base_model": base, "style_name": "chill", "name": "chill-turbo",
                              "params": {"rank": 8, "learning_rate": 0.0001, "epochs": 2}},
                   {"datasets/ds_v1.zip": z})


def mock_train_server(out_holder, fail=False, lora_loaded=False):
    respx.get(f"{URL}/health").respond(json=HEALTH_OK)
    respx.get(f"{URL}/v1/model_inventory").respond(json=MODELS)
    respx.get(f"{URL}/v1/lora/status").respond(json=lora_status(lora_loaded, ["ms_lora_1"] if lora_loaded else []))
    unload = respx.post(f"{URL}/v1/lora/unload").respond(json=env({"message": "✅"}))
    scan = respx.post(f"{URL}/v1/dataset/scan").respond(json=env({"num_samples": 1, "samples": [
        {"filename": "0001_a.wav", "labeled": True, "is_instrumental": True}]}))
    respx.post(f"{URL}/v1/dataset/preprocess_async").respond(json=env({"task_id": "p-1", "total": 1}))
    respx.get(f"{URL}/v1/dataset/preprocess_status/p-1").mock(side_effect=[
        httpx.Response(200, json=env({"status": "running", "current": 0, "total": 1, "progress": "Preprocessing 1/1"})),
        httpx.Response(200, json=env({"status": "completed", "current": 1, "total": 1, "progress": "✅ done"})),
    ])

    def start(request):
        out_holder["body"] = json.loads(request.content)
        return httpx.Response(200, json=env({"message": "Training started"}))
    respx.post(f"{URL}/v1/training/start").mock(side_effect=start)

    calls = {"n": 0}

    def status(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=env({"is_training": True, "current_epoch": 1, "current_step": 1,
                                                 "current_loss": 0.9, "status": "Epoch 1/2, Step 1, Loss: 0.9"}))
        if fail:
            return httpx.Response(200, json=env({"is_training": False, "status": "Stopped", "error": "CUDA out of memory"}))
        adapter = __import__("pathlib").Path(out_holder["body"]["lora_output_dir"]) / "final" / "adapter"
        adapter.mkdir(parents=True)
        (adapter / "adapter_model.safetensors").write_bytes(b"trained")
        (adapter / "adapter_config.json").write_text('{"r": 8}')
        return httpx.Response(200, json=env({"is_training": False, "current_epoch": 2, "current_step": 2,
                                             "current_loss": 0.5, "loss_history": [{"step": 1, "loss": 0.9},
                                                                                  {"step": 2, "loss": 0.5}],
                                             "status": "✅ Training complete!"}))
    respx.get(f"{URL}/v1/training/status").mock(side_effect=status)
    init = respx.post(f"{URL}/v1/init").respond(json=env({"loaded_model": TURBO}))
    return {"unload": unload, "scan": scan, "init": init}


@respx.mock
@pytest.mark.parametrize('directory', [False, True])
def test_train_full_flow(ace_work, tmp_path, directory):
    holder = {}
    routes = mock_train_server(holder)
    ctx = train_ctx(tmp_path)
    if directory:
        from api.storage import Storage
        import io
        storage = Storage(ace_work.settings.storage_dir)
        storage.save_stream('uploads', 'a.wav', io.BytesIO(b'RIFF'))
        folder = storage.export_dataset('direct', 1, [{'id': 1, 'filename': 'a.wav', 'path': 'uploads/a.wav', 'caption': 'lofi'}], directory=True)
        ctx.payload.update(dataset_file=storage.rel(folder), dataset_format='directory')
        ctx.files.clear()  # A directory train must not request a ZIP download.
    res = ace_work.train(ctx)
    body = holder["body"]
    assert body["lora_rank"] == 8 and body["lora_alpha"] == 16 and body["train_epochs"] == 2
    assert body["train_batch_size"] == 1 and body["gradient_checkpointing"] is True
    work = (tmp_path / "ace_work").resolve()
    assert body["tensor_dir"].startswith(str(work)) and body["lora_output_dir"].startswith(str(work))
    scan_body = json.loads(routes["scan"].calls[0].request.content)
    assert scan_body["all_instrumental"] is True and scan_body["audio_dir"].startswith(str(work))
    assert not routes["unload"].called
    assert routes["init"].call_count == 1  # chỉ nạp lại model sạch SAU train (trước train không có LoRA)
    assert json.loads(routes["init"].calls[0].request.content)["model"] == TURBO
    names = sorted(f.name for f in res.files)
    assert names == ["adapter_config.json", "adapter_model.safetensors", "metadata.json"]
    assert res.result["backend"] == "ace" and res.result["metrics"]["final_loss"] == 0.5
    assert not any(work.glob("train_job*"))  # dọn thư mục làm việc
    logs = " ".join(l for _, l in ctx.events if l)
    assert "Epoch 1/2" in logs and "loss=0.9000" in logs


@respx.mock
def test_train_with_lora_loaded_reinitializes_first(ace_work, tmp_path):
    holder = {}
    routes = mock_train_server(holder, lora_loaded=True)
    ace_work.train(train_ctx(tmp_path))
    assert not routes["unload"].called  # unload hỏng ở ACE-Step -> dùng /v1/init
    assert routes["init"].call_count == 2  # trước train (decoder sạch) và sau train
    assert routes["scan"].called


@respx.mock
def test_train_failure_still_reinitializes(ace_work, tmp_path):
    routes = mock_train_server({}, fail=True)
    with pytest.raises(AceStepError, match="CUDA out of memory"):
        ace_work.train(train_ctx(tmp_path))
    assert routes["init"].called


@respx.mock
def test_train_nan_loss_status_500_stops_before_reinit(ace_work, tmp_path):
    """Tái hiện lỗi thật trên 3060: loss NaN -> /v1/training/status trả 500 (text, không phải JSON).
    Worker phải dừng train và CHỜ is_training=false rồi mới /v1/init và xóa tensor."""
    routes = mock_train_server({})
    order = []
    stopped = {"v": False}

    def status(request):
        order.append("status")
        if not stopped["v"]:
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(200, json=env({"is_training": False, "status": "Stopping..."}))

    def stop(request):
        order.append("stop")
        stopped["v"] = True
        return httpx.Response(200, json=env({"message": "Stopping training..."}))

    respx.get(f"{URL}/v1/training/status").mock(side_effect=status)
    respx.post(f"{URL}/v1/training/stop").mock(side_effect=stop)
    routes["init"].mock(side_effect=lambda r: (order.append("init"), httpx.Response(200, json=env({})))[1])
    with pytest.raises(AceStepError, match="NaN"):
        ace_work.train(train_ctx(tmp_path))
    assert order.index("stop") < order.index("init")  # dừng xong mới nạp lại
    assert order[order.index("init") - 1] == "status"  # đã xác nhận is_training=false


@respx.mock
def test_train_cannot_confirm_stop_keeps_model_and_files(ace_work, tmp_path):
    routes = mock_train_server({})
    respx.get(f"{URL}/v1/training/status").respond(500, text="Internal Server Error")
    respx.post(f"{URL}/v1/training/stop").respond(500, text="boom")
    with pytest.raises(AceStepError):
        ace_work.train(train_ctx(tmp_path))
    assert not routes["init"].called  # không nạp model thứ hai khi train có thể vẫn chạy
    assert any((tmp_path / "ace_work").glob("train_job*"))  # không xóa tensor đang dùng


@respx.mock
def test_train_base_model_not_loaded(ace_work, tmp_path):
    routes = mock_train_server({})
    with pytest.raises(AceStepError, match="chưa được nạp"):
        ace_work.train(train_ctx(tmp_path, base="acestep-v15-xl-sft"))
    assert not routes["scan"].called and not routes["init"].called
