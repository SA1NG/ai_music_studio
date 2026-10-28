"""AceStepBackend — adapter tới REST API server của ACE-Step 1.5 (`uv run acestep-api`, cổng 8001).

Tham chiếu: docs/en/API.md và mã nguồn acestep/api/ (đã đối chiếu với commit ca1e85f, 2026-08-29):
  - POST /release_task       -> data.task_id
  - POST /query_result       -> data[i].status (0 chạy / 1 xong / 2 lỗi), data[i].result là CHUỖI JSON
                                (list item: file, metas, seed_value, dit_model, lm_model, progress, stage, error),
                                data[i].progress_text
  - GET  /v1/audio?path=...  -> file audio (trường `file` có thể đã là URL "/v1/audio?path=...")
  - GET  /v1/model_inventory -> data.models[{name, is_default, is_loaded}], default_model,
                                lm_models[{name, is_loaded}], loaded_lm_model, llm_initialized
                                (liệt kê cả model CÓ TRÊN ĐĨA chưa nạp -> phải lọc is_loaded).
                                Lưu ý: /v1/models ở bản này bị adapter OpenRouter chiếm (định dạng OpenAI).
  - GET  /health             -> data.status == "ok", models_initialized, loaded_model
  - /v1/lora/{status,load,scale,unload}; /v1/dataset/{scan,preprocess_async,preprocess_status};
    /v1/training/{start,status,stop}; /v1/init — xem README mục "Cách ACE-Step xử lý LoRA".
"""
from __future__ import annotations

import json
import random
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from shared.audio import audio_duration
from shared.config import Settings
from shared.dataset_files import copy_dataset, local_dataset
from shared.essentia_check import verify_labels
from shared.schemas import NO_LM, ModelInfo

from .base import Backend, BackendError, BackendResult, JobContext

STATUS_RUNNING, STATUS_DONE, STATUS_FAILED = 0, 1, 2
MIN_DURATION, DEFAULT_DURATION = 10.0, 30.0  # ACE-Step nhận audio_duration 10–600 giây
TURBO_STEPS, BASE_STEPS = 8, 32  # số bước mặc định khi không cấu hình riêng


class AceStepError(BackendError):
    pass


class AceStepUnavailable(AceStepError):
    """Không kết nối được ACE-Step API server (chưa bật / sai ACESTEP_URL)."""


class AceStepClient:
    """Client mỏng cho ACE-Step API. Mọi response thành công bọc dạng {data, code, error, timestamp, extra};
    response lỗi có thể chỉ có {detail}."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 60.0,
                 client: Optional[httpx.Client] = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.base_url = base_url.rstrip("/")
        # connect timeout ngắn: ACE-Step tắt thì báo lỗi ngay, không treo
        self.http = client or httpx.Client(base_url=self.base_url,
                                           timeout=httpx.Timeout(timeout, connect=5.0))
        self.http.headers.update(headers)

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            return self.http.request(method, url, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise AceStepUnavailable(
                f"ACE-Step chưa bật: không kết nối được {self.base_url} ({type(e).__name__}). "
                "Hãy chạy `uv run acestep-api` trong thư mục ACE-Step-1.5.") from e

    @staticmethod
    def _unwrap(r: httpx.Response) -> Any:
        try:
            body = r.json()
        except ValueError:
            raise AceStepError(f"ACE-Step trả HTTP {r.status_code} không phải JSON: {r.text[:300]}")
        if r.status_code >= 400 or not isinstance(body, dict):
            detail = body.get("detail") if isinstance(body, dict) else body
            raise AceStepError(f"HTTP {r.status_code}: {detail or body}")
        if "data" not in body and "detail" in body:
            raise AceStepError(str(body["detail"]))
        if body.get("error") or body.get("code", 200) != 200:
            raise AceStepError(f"code={body.get('code')} error={body.get('error')}")
        return body.get("data")

    def health(self) -> dict[str, Any]:
        return self._unwrap(self._request("GET", "/health", timeout=httpx.Timeout(10.0, connect=3.0))) or {}

    def list_models(self) -> dict[str, Any]:
        """Kho model của server. Ở bản hiện tại, /v1/models bị adapter OpenRouter chiếm (trả định dạng OpenAI
        {"object":"list","data":[{"id":"acestep/<tên>"}]}), nên ưu tiên /v1/model_inventory (định dạng gốc);
        server cũ không có endpoint đó thì lùi về /v1/models và chuẩn hoá cả hai định dạng."""
        r = self._request("GET", "/v1/model_inventory")
        if r.status_code == 404:
            r = self._request("GET", "/v1/models")
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else None
            if isinstance(body, dict) and body.get("object") == "list" and isinstance(body.get("data"), list):
                names = [str(m.get("id", "")).split("/")[-1] for m in body["data"] if m.get("id")]
                return {"models": [{"name": n, "is_loaded": True} for n in names],
                        "default_model": names[0] if names else None, "llm_initialized": False}
        return self._unwrap(r)

    def init_model(self, model: Optional[str] = None, slot: int = 1, init_llm: bool = False,
                   lm_model_path: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"slot": slot, "init_llm": init_llm}
        if model:
            body["model"] = model
        if lm_model_path:
            body["lm_model_path"] = lm_model_path
        return self._unwrap(self._request("POST", "/v1/init", json=body, timeout=900.0))

    # ---------- LoRA (acestep/api/http/lora_routes.py) ----------
    def lora_status(self) -> dict[str, Any]:
        return self._unwrap(self._request("GET", "/v1/lora/status")) or {}

    def lora_load(self, lora_path: str, adapter_name: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"lora_path": lora_path}
        if adapter_name:
            body["adapter_name"] = adapter_name
        return self._unwrap(self._request("POST", "/v1/lora/load", json=body, timeout=300.0))

    def lora_scale(self, scale: float, adapter_name: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"scale": scale}
        if adapter_name:
            body["adapter_name"] = adapter_name
        return self._unwrap(self._request("POST", "/v1/lora/scale", json=body))

    def lora_toggle(self, use_lora: bool) -> dict[str, Any]:
        return self._unwrap(self._request("POST", "/v1/lora/toggle", json={"use_lora": use_lora}))

    def lora_unload(self) -> dict[str, Any]:
        # CẢNH BÁO (commit ca1e85f): unload KHÔNG gỡ được LoRA — get_base_model() vẫn còn lớp LoRA chèn trong
        # module và load_state_dict(strict=False) lệch khóa nên không khôi phục gì. Adapter không dùng hàm này;
        # dùng lora_toggle(False) để tắt, /v1/init để có decoder sạch.
        return self._unwrap(self._request("POST", "/v1/lora/unload", timeout=300.0))

    # ---------- dataset + training (acestep/api/train_api_*.py) ----------
    def dataset_scan(self, audio_dir: str, dataset_name: str, all_instrumental: bool = True) -> dict[str, Any]:
        return self._unwrap(self._request("POST", "/v1/dataset/scan", json={
            "audio_dir": audio_dir, "dataset_name": dataset_name, "all_instrumental": all_instrumental}, timeout=300.0))

    def auto_label_start(self, body: dict) -> dict:
        return self._unwrap(self._request("POST", "/v1/dataset/auto_label_async", json=body)) or {}

    def auto_label_status(self, task_id: str) -> dict:
        return self._unwrap(self._request("GET", f"/v1/dataset/auto_label_status/{task_id}")) or {}

    def preprocess_async(self, output_dir: str) -> dict[str, Any]:
        return self._unwrap(self._request("POST", "/v1/dataset/preprocess_async", json={"output_dir": output_dir}))

    def preprocess_status(self, task_id: str) -> dict[str, Any]:
        return self._unwrap(self._request("GET", f"/v1/dataset/preprocess_status/{task_id}"))

    def training_start(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._unwrap(self._request("POST", "/v1/training/start", json=body, timeout=300.0))

    def training_status(self) -> dict[str, Any]:
        return self._unwrap(self._request("GET", "/v1/training/status")) or {}

    def training_stop(self) -> dict[str, Any]:
        return self._unwrap(self._request("POST", "/v1/training/stop"))

    def release_task(self, body: dict[str, Any]) -> str:
        data = self._unwrap(self._request("POST", "/release_task", json=body))
        task_id = (data or {}).get("task_id")
        if not task_id:
            raise AceStepError(f"release_task không trả task_id: {data}")
        return str(task_id)

    def query_result(self, task_ids: list[str]) -> list[dict[str, Any]]:
        data = self._unwrap(self._request("POST", "/query_result", json={"task_id_list": task_ids}))
        return list(data or [])

    def download_audio(self, file_ref: str, dest: Path) -> Path:
        # `file` là URL "/v1/audio?path=..." (job_result_payload) hoặc đường dẫn thô -> truyền vào ?path=
        if file_ref.startswith(("http://", "https://", "/v1/audio")):
            url, params = file_ref, None
        else:
            url, params = "/v1/audio", {"path": file_ref}
        try:
            with self.http.stream("GET", url, params=params) as r:
                if r.status_code >= 400:
                    r.read()
                    raise AceStepError(f"tải audio lỗi HTTP {r.status_code}: {r.text[:300]}")
                with open(dest, "wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise AceStepUnavailable(f"ACE-Step mất kết nối khi tải audio: {e}") from e
        return dest


def parse_task_result(raw: Any) -> list[dict[str, Any]]:
    """Trường `result` của /query_result là CHUỖI JSON (list các item)."""
    if raw in (None, ""):
        return []
    data = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(data, dict):
        data = [data]
    return [x for x in data if isinstance(x, dict)]


def first_seed(seed_value: Any) -> Optional[int]:
    """seed_value là chuỗi các seed phân cách bằng dấu phẩy."""
    if seed_value in (None, ""):
        return None
    try:
        return int(str(seed_value).split(",")[0].strip())
    except ValueError:
        return None


def normalize_time_signature(ts: str) -> str:
    """API nhận 2/3/4/6 (cho 2/4, 3/4, 4/4, 6/8)."""
    return str(ts).split("/")[0].strip() if ts else ""


class AceStepBackend(Backend):
    name = "ace"
    capabilities = ["generate", "autolabel", "train"]

    def __init__(self, settings: Settings, client: Optional[AceStepClient] = None,
                 sleep: Callable[[float], None] = time.sleep, rng: Optional[random.Random] = None):
        self.settings = settings
        self.client = client or AceStepClient(settings.acestep_url, settings.acestep_api_key)
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._last_models: Optional[tuple[list[ModelInfo], list[ModelInfo]]] = None
        self._pending_label_task: Optional[tuple[str, Path]] = None

    def _guard_label_task(self) -> None:
        """A timed-out HTTP poll does not cancel ACE-Step's background GPU work."""
        if self._pending_label_task is None:
            return
        task_id, root = self._pending_label_task
        try:
            state = self.client.auto_label_status(task_id)
        except AceStepError as exc:
            if "HTTP 404" not in str(exc):
                raise
            state = {"status": "failed"}  # The engine was restarted and no longer owns this task.
        if state.get("status") not in ("completed", "failed"):
            raise AceStepError("ACE-Step vẫn đang gán nhãn tác vụ trước. Hãy chờ hoàn tất trước khi chạy tác vụ AI khác.")
        self._pending_label_task = None
        shutil.rmtree(root, ignore_errors=True)

    # ---------- trạng thái / model ----------
    def status(self) -> Optional[str]:
        """None nếu ACE-Step phản hồi, ngược lại là thông báo lỗi để hiện trên UI."""
        try:
            data = self.client.health()
        except AceStepError as e:
            return str(e)
        if data.get("status") != "ok":
            return f"ACE-Step trả trạng thái bất thường: {data}"
        return None

    def list_models(self):
        """DiT: chỉ model ĐÃ NẠP (is_loaded); nếu server nạp lười và chưa nạp gì thì lấy default_model.
        LM: chỉ khi server đã khởi tạo LLM (llm_initialized), ngược lại chỉ có "none" (không dùng)."""
        data = self.client.list_models() or {}
        models = data.get("models") or []
        default = data.get("default_model") or self.settings.default_dit_model
        loaded = [m["name"] for m in models if m.get("name") and m.get("is_loaded")]
        names = loaded or ([default] if default else [])
        dit = [ModelInfo(name=n, is_default=(n == default or len(names) == 1), description="acestep") for n in names]
        lm_names = [NO_LM]
        if data.get("llm_initialized") and data.get("loaded_lm_model"):
            lm_names.append(data["loaded_lm_model"])
        lm = [ModelInfo(name=n, is_default=(n == NO_LM), description="acestep") for n in lm_names]
        self._last_models = (dit, lm)
        return dit, lm

    def list_models_or_last(self) -> tuple[list[ModelInfo], list[ModelInfo]]:
        """Như list_models nhưng không ném lỗi khi ACE-Step tắt: dùng danh sách lần trước,
        hoặc DEFAULT_DIT_MODEL, để UI vẫn chọn được model (job sẽ báo 'ACE-Step chưa bật')."""
        try:
            return self.list_models()
        except AceStepError:
            if self._last_models:
                return self._last_models
            d = self.settings.default_dit_model
            return ([ModelInfo(name=d, is_default=True, description="acestep (chưa kết nối)")] if d else [],
                    [ModelInfo(name=NO_LM, is_default=True)])

    # ---------- generate ----------
    def build_release_body(self, p: dict[str, Any]) -> dict[str, Any]:
        lm_model = p.get("lm_model") or NO_LM
        seed = int(p.get("seed", -1))
        if seed < 0:
            # Tự bốc seed thay vì để server random: đường trả kết quả qua store của ACE-Step không có
            # seed_value, nên phải tự biết seed thì mới tái lập được bài.
            seed = self._rng.randint(0, 2**31 - 1)
        default_steps = TURBO_STEPS if "turbo" in p["dit_model"].lower() else BASE_STEPS
        body: dict[str, Any] = {
            "prompt": p["prompt"],
            "lyrics": "[Instrumental]" if p.get("instrumental", True) else p.get("lyrics", ""),
            "vocal_language": "unknown" if p.get("instrumental", True) else p.get("vocal_language", "unknown"),
            "task_type": p.get("task_type", "text2music"),
            "audio_duration": max(MIN_DURATION, float(p.get("duration") or DEFAULT_DURATION)),
            "model": p["dit_model"],
            "thinking": lm_model != NO_LM and p.get("thinking", True) and p.get("task_type", "text2music") == "text2music",
            "use_cot_caption": lm_model != NO_LM and p.get("use_cot_caption", False),
            "use_cot_language": False,
            "use_random_seed": False,
            "seed": seed,
            "batch_size": 1,
            "inference_steps": int(p.get("inference_steps") or self.settings.acestep_inference_steps
                                   or default_steps),
            "audio_format": p.get("audio_format") or "wav",
        }
        if lm_model != NO_LM:
            body["lm_model_path"] = lm_model
        body["guidance_scale"] = p.get("guidance_scale", 7)
        body["lm_temperature"] = p.get("lm_temperature", 0.85)
        body["lm_negative_prompt"] = p.get("lm_negative_prompt") or "NO USER INPUT"
        if body["task_type"] == "cover":
            body["audio_cover_strength"] = p.get("audio_cover_strength", 0.8)
        if body["task_type"] == "repaint":
            body.update(repainting_start=p.get("repainting_start", 0),
                        repainting_end=p.get("repainting_end"), chunk_mask_mode="explicit",
                        repaint_mode=p.get("repaint_mode", "balanced"))
        if p.get("bpm"):
            body["bpm"] = int(p["bpm"])
        if p.get("key_scale"):
            body["key_scale"] = p["key_scale"]
        ts = normalize_time_signature(p.get("time_signature") or "")
        if ts:
            body["time_signature"] = ts
        return body

    # ---------- LoRA ----------
    def _work_dir(self) -> Path:
        if not self.settings.acestep_work_dir:
            raise AceStepError("Chưa đặt ACESTEP_WORK_DIR (thư mục làm việc BÊN TRONG thư mục ACE-Step-1.5, "
                               "ví dụ D:/ACE-Step-1.5/music_studio_work) — cần cho train và gắn LoRA.")
        p = Path(self.settings.acestep_work_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @staticmethod
    def adapter_name(lora_id: Any) -> str:
        return f"ms_lora_{lora_id}"

    def _prepare_lora_dir(self, ctx: JobContext, lora: dict[str, Any]) -> Path:
        """Tải adapter PEFT (adapter_model.safetensors + adapter_config.json) từ API về ACESTEP_WORK_DIR
        (cache theo id) để ACE-Step đọc bằng đường dẫn cục bộ."""
        if lora.get("source") != "ace":
            raise AceStepError(f"LoRA '{lora.get('name')}' do backend '{lora.get('source')}' tạo (không phải adapter "
                               "ACE-Step thật) nên không gắn được. Hãy train lại với TRAIN_BACKEND=ace.")
        target = self._work_dir() / "loras" / self.adapter_name(lora["id"])
        weights_rel = lora["path"]
        folder = weights_rel.rsplit("/", 1)[0]
        needed = {Path(weights_rel).name: weights_rel, "adapter_config.json": f"{folder}/adapter_config.json"}
        if not all((target / n).is_file() for n in needed):
            target.mkdir(parents=True, exist_ok=True)
            for name, rel in needed.items():
                shutil.copy(ctx.download(rel), target / name)
        return target

    def _apply_lora_state(self, ctx: JobContext, p: dict[str, Any]) -> dict[str, Any]:
        """LoRA là trạng thái CHUNG của model đang nạp trên ACE-Step: mỗi job tự đặt cho đúng.
        Không dùng /v1/lora/unload (hỏng ở commit này, xem AceStepClient.lora_unload). Thay vào đó:
        mỗi LoRA nạp MỘT lần dưới tên adapter riêng (ACE-Step hỗ trợ nhiều adapter), job không LoRA thì
        tắt adapter (PEFT disable_adapter_layers -> đúng output gốc), job có LoRA thì bật + chọn adapter + scale."""
        status = self.client.lora_status()
        loaded = bool(status.get("lora_loaded"))
        in_use = bool(status.get("use_lora"))
        adapters = [a if isinstance(a, str) else (a or {}).get("name") for a in status.get("adapters") or []]
        lora = p.get("lora")
        if not lora:
            if loaded and in_use:
                self.client.lora_toggle(False)
                ctx.progress(0.03, "[ace] tắt LoRA đang nạp (job này không dùng LoRA)")
            return {}
        name = self.adapter_name(lora["id"])
        if name not in adapters:
            path = self._prepare_lora_dir(ctx, lora)
            self.client.lora_load(str(path), adapter_name=name)
            ctx.progress(0.04, f"[ace] đã nạp LoRA '{lora.get('name')}' ({name})")
        self.client.lora_toggle(True)  # set_lora_scale bị bỏ qua khi LoRA đang tắt -> bật trước
        scale = min(1.0, max(0.0, float(p.get("lora_scale", 1.0))))
        self.client.lora_scale(scale, adapter_name=name)  # cũng chọn adapter này làm adapter đang dùng
        return {"lora_adapter": name, "lora_scale": scale}

    def generate(self, ctx: JobContext) -> BackendResult:
        self._guard_label_task()
        p = ctx.payload
        problem = self.status()
        if problem:
            raise AceStepUnavailable(problem)
        lora_info = self._apply_lora_state(ctx, p)

        body = self.build_release_body(p)
        for field, target in (("source_audio", "src_audio_path"), ("reference_audio", "reference_audio_path")):
            if p.get(field):
                body[target] = str(ctx.download(p[field]).resolve())
        task_id = self.client.release_task(body)
        ctx.progress(0.02, f"[ace] task_id={task_id} model={body['model']} seed={body['seed']} "
                           f"duration={body['audio_duration']:.0f}s steps={body['inference_steps']}")

        started = time.monotonic()
        last_text = ""
        item: dict[str, Any] = {}
        while True:
            rows = self.client.query_result([task_id])
            row = next((r for r in rows if str(r.get("task_id")) == task_id), rows[0] if rows else {})
            status = int(row.get("status", STATUS_RUNNING))
            items = parse_task_result(row.get("result"))
            if status == STATUS_DONE:
                if not items or not items[0].get("file"):
                    raise AceStepError(f"task {task_id} xong nhưng không có file audio: {row.get('result')}")
                item = items[0]
                break
            if status == STATUS_FAILED:
                err = (items[0].get("error") if items else None) or row.get("progress_text") or row.get("result")
                raise AceStepError(f"ACE-Step báo lỗi task {task_id}: {str(err)[-1500:]}")
            elapsed = time.monotonic() - started
            if elapsed > self.settings.acestep_timeout:
                raise TimeoutError(f"task {task_id} quá {self.settings.acestep_timeout:.0f}s chưa xong")
            server_progress = float(items[0].get("progress") or 0) if items else 0.0
            text = str(row.get("progress_text") or (items[0].get("stage") if items else "") or "")
            ctx.progress(0.05 + 0.85 * min(1.0, server_progress),
                         f"[ace] {text}" if text and text != last_text else None)
            last_text = text or last_text
            self._sleep(self.settings.acestep_poll_interval)

        ext = "." + str(body["audio_format"]).replace("wav32", "wav")
        out = ctx.workdir / f"ace_{ctx.job_id}{ext}"
        self.client.download_audio(item["file"], out)
        took = time.monotonic() - started
        ctx.progress(0.97, f"[ace] đã tải audio ({took:.1f}s)")
        seed = first_seed(item.get("seed_value"))
        # Server trả lm_model = LM mặc định trong cấu hình kể cả khi KHÔNG nạp/không dùng LM,
        # nên chỉ tin giá trị đó khi job thực sự dùng LM (thinking=true).
        lm_used = (item.get("lm_model") or p.get("lm_model")) if body["thinking"] else NO_LM
        return BackendResult(result={
            "backend": self.name, "task_id": task_id,
            "seed": seed if seed is not None else body["seed"],
            "dit_model": item.get("dit_model") or body["model"],
            "lm_model": lm_used or NO_LM,
            "metas": item.get("metas") or {}, "generation_info": item.get("generation_info", ""),
            "elapsed_seconds": round(took, 1), **lora_info,
        }, files=[out])

    # ---------- autolabel ----------
    def autolabel(self, ctx: JobContext) -> BackendResult:
        self._guard_label_task()
        problem = self.status()
        if problem:
            raise AceStepUnavailable(problem)
        inventory = self.client.list_models()
        if not inventory.get("llm_initialized"):
            raise AceStepError("Gán nhãn AI cần LM đã nạp. Chọn LM trong thanh Model rồi bấm Áp dụng.")
        p = ctx.payload
        local = ctx.download(p["file"])
        instrumental = p.get("is_instrumental", True)
        root = self._work_dir() / f"label_job{ctx.job_id:06d}"
        root.mkdir(parents=True, exist_ok=True)
        source = root / ("source" + local.suffix.lower())
        shutil.copy2(local, source)
        if not instrumental and (p.get("lyrics") or "").strip():
            source.with_suffix(".lyrics.txt").write_text(p["lyrics"], encoding="utf-8")
        ctx.progress(0.05, "[ace] chuẩn bị audio để phân tích bằng DiT và LM")
        self.client.dataset_scan(str(root), f"label-{ctx.job_id}", all_instrumental=instrumental)
        task = self.client.auto_label_start({"skip_metas": False, "format_lyrics": False,
                                             "transcribe_lyrics": not instrumental and p.get("transcribe_lyrics", True),
                                             "only_unlabeled": False, "save_path": str(root / "labels.json")})
        task_id = task.get("task_id")
        if not task_id or task.get("total") == 0:
            raise AceStepError("ACE-Step không tìm thấy audio cần gán nhãn.")
        self._pending_label_task = (task_id, root)
        state = self._poll("gán nhãn AI", lambda: self.client.auto_label_status(task_id),
                           lambda s: s.get("status") in ("completed", "failed"),
                           lambda s: ctx.progress(0.3, "[ace] " + str(s.get("progress") or "Đang phân tích audio…")),
                           self.settings.acestep_timeout)
        self._pending_label_task = None
        if state.get("status") != "completed":
            shutil.rmtree(root, ignore_errors=True)
            raise AceStepError("Gán nhãn AI thất bại: " + str(state.get("error") or state.get("progress")))
        samples = (state.get("result") or {}).get("samples") or []
        sample = next((s for s in samples if s.get("labeled") and (s.get("caption") or "").strip()), None)
        if sample is None:
            shutil.rmtree(root, ignore_errors=True)
            raise AceStepError("ACE-Step chưa tạo được caption: " + str((state.get("result") or {}).get("message") or state.get("progress")))
        bpm = sample.get("bpm")
        labels = {"source": "ace", "duration": sample.get("duration") or audio_duration(local),
                  "bpm": bpm if bpm is not None and 30 <= float(bpm) <= 300 else None,
                  "bpm_source": "ace", "caption": sample["caption"],
                  "key_scale": sample.get("keyscale") or "", "time_signature": normalize_time_signature(str(sample.get("timesignature") or "")),
                  "tags": [], "is_instrumental": bool(instrumental),
                  "lyrics": "" if instrumental else sample.get("lyrics") or "",
                  "vocal_language": "unknown" if instrumental else sample.get("language") or "unknown"}
        shutil.rmtree(root, ignore_errors=True)
        if self.settings.essentia_enabled:
            ctx.progress(0.8, "[essentia] đo BPM/key từ audio để đối chiếu với LM")
            labels = verify_labels(local, labels, self.settings)
        ctx.progress(0.95, "[ace] đã phân tích xong; đang lưu nhãn")
        return BackendResult(result={"auto_labels": labels})

    # ---------- train ----------
    def _poll(self, what: str, fn: Callable[[], dict[str, Any]], done: Callable[[dict[str, Any]], bool],
              on_tick: Callable[[dict[str, Any]], None], timeout: float, max_errors: int = 3) -> dict[str, Any]:
        """Poll tới khi done(st). Lỗi đọc trạng thái tạm thời được thử lại; lỗi liên tiếp > max_errors thì ném ra."""
        started, errors = time.monotonic(), 0
        while True:
            try:
                st = fn()
                errors = 0
            except AceStepUnavailable:
                raise
            except AceStepError:
                errors += 1
                if errors > max_errors:
                    raise
                self._sleep(self.settings.acestep_poll_interval)
                continue
            on_tick(st)
            if done(st):
                return st
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"{what} quá {timeout:.0f}s chưa xong")
            self._sleep(self.settings.acestep_poll_interval)

    def _stop_training_and_wait(self, timeout: float = 300) -> bool:
        """Dừng train và chờ ACE-Step xác nhận is_training=false. Trả False nếu không xác nhận được.
        /v1/training/stop cũng xóa current_loss: nhờ vậy /v1/training/status đọc lại được khi loss = NaN
        (ACE-Step trả HTTP 500 vì không serialize được NaN)."""
        try:
            self.client.training_stop()
            self._poll("dừng train", self.client.training_status, lambda s: not s.get("is_training"),
                       lambda s: None, timeout, max_errors=10)
            return True
        except Exception:
            return False

    def train(self, ctx: JobContext) -> BackendResult:
        """Quy trình đã xác minh trong mã nguồn ACE-Step (README: "Cách ACE-Step xử lý LoRA"):
        unload LoRA -> /v1/dataset/scan -> preprocess_async (poll) -> /v1/training/start (poll status)
        -> lấy <output>/final/adapter -> POST /v1/init để nạp lại model sạch (train để lại LoRA trong decoder)."""
        self._guard_label_task()
        p = ctx.payload
        params = p.get("params", {})
        base_model = p["base_model"]
        problem = self.status()
        if problem:
            raise AceStepUnavailable(problem)
        inv = self.client.list_models() or {}
        loaded = [m["name"] for m in inv.get("models") or [] if m.get("is_loaded")] or [inv.get("default_model")]
        if base_model not in loaded:
            raise AceStepError(f"base_model '{base_model}' chưa được nạp trên ACE-Step (đang nạp: {loaded}). "
                               "Train dùng chính model đang nạp: đổi ACESTEP_CONFIG_PATH bên ACE-Step hoặc chọn base model khác.")

        root = self._work_dir() / f"train_job{ctx.job_id:06d}"
        shutil.rmtree(root, ignore_errors=True)
        audio_dir, tensor_dir, out_dir = root / "audio", root / "tensors", root / "output"
        source = local_dataset(p, self.settings) if p.get("dataset_format") == "directory" else ctx.download(p["dataset_file"])
        copy_dataset(source, audio_dir)
        rank = int(params.get("rank", 16))
        epochs = int(params.get("epochs", 10))
        touched_model = training_running = False
        try:
            if self.client.lora_status().get("lora_loaded"):
                # Đã có LoRA chèn trong decoder (unload không gỡ được) -> nạp lại decoder sạch trước khi train
                ctx.progress(0.02, f"[ace] đang có LoRA nạp sẵn: nạp lại model sạch ({base_model}) trước khi train")
                self.client.init_model(base_model)
            scan = self.client.dataset_scan(str(audio_dir), f"{p.get('dataset_name', 'ds')}_v{p.get('dataset_version', 1)}")
            samples = scan.get("samples") or []
            labeled = [s for s in samples if s.get("labeled")]
            vocal = [s["filename"] for s in samples if not s.get("is_instrumental", True)]
            ctx.progress(0.03, f"[ace] scan: {len(samples)} bài, {len(labeled)} có caption"
                               + (f" — {len(vocal)} bài có lời" if vocal else ""))
            if not labeled:
                raise AceStepError("Dataset không có bài nào có caption (ACE-Step chỉ train mẫu có caption).")

            touched_model = True  # preprocess/train tạm dời các thành phần model khỏi GPU
            task_id = self.client.preprocess_async(str(tensor_dir))["task_id"]

            def pre_tick(st):
                total = max(1, int(st.get("total") or len(labeled)))
                ctx.progress(0.03 + 0.17 * int(st.get("current") or 0) / total, f"[ace] tiền xử lý: {st.get('progress')}")

            st = self._poll("tiền xử lý dataset", lambda: self.client.preprocess_status(task_id),
                            lambda s: s.get("status") in ("completed", "failed"), pre_tick, self.settings.acestep_train_timeout)
            if st.get("status") != "completed":
                raise AceStepError(f"Tiền xử lý dataset lỗi: {st.get('error') or st.get('progress')}")

            body = {"tensor_dir": str(tensor_dir), "lora_rank": rank, "lora_alpha": min(512, 2 * rank),
                    "lora_dropout": 0.1, "learning_rate": float(params.get("learning_rate", 1e-4)),
                    "train_epochs": epochs, "train_batch_size": 1,
                    # dataset rất nhỏ (vài bài): tích lũy 1 bước để mỗi epoch đều cập nhật trọng số
                    "gradient_accumulation": 1, "save_every_n_epochs": epochs + 1,  # chỉ lưu bản final
                    "training_seed": 42, "lora_output_dir": str(out_dir),
                    "gradient_checkpointing": self.settings.acestep_train_grad_checkpointing}
            training_running = True  # từ đây: chưa xác nhận dừng thì KHÔNG được nạp lại model / xóa tensor
            self.client.training_start(body)
            ctx.progress(0.21, f"[ace] bắt đầu train: rank={rank} lr={body['learning_rate']} epochs={epochs} "
                               f"batch=1 grad_ckpt={body['gradient_checkpointing']}")
            last = {"text": "", "nonfinite": 0}

            def train_tick(st):
                text = str(st.get("status") or "")
                loss = st.get("current_loss")
                line = f"[ace] {text}" + (f" loss={loss:.4f}" if isinstance(loss, (int, float)) else "")
                ep = int(st.get("current_epoch") or 0)
                if "Non-finite" in text:
                    last["nonfinite"] += 1
                ctx.progress(0.21 + 0.69 * min(1.0, ep / max(1, epochs)), line if text != last["text"] else None)
                last["text"] = text

            try:
                st = self._poll("train LoRA", self.client.training_status, lambda s: not s.get("is_training"),
                                train_tick, self.settings.acestep_train_timeout)
                training_running = False
            except AceStepError as e:
                # Hay gặp nhất: loss = NaN -> /v1/training/status trả HTTP 500 (ACE-Step không serialize được NaN)
                training_running = not self._stop_training_and_wait()
                raise AceStepError(
                    "Không đọc được trạng thái train từ ACE-Step — thường do loss = NaN (train phân kỳ"
                    + (f", đã thấy 'Non-finite gradients' {last['nonfinite']} lần" if last["nonfinite"] else "")
                    + f"). {'Đã dừng train.' if not training_running else 'KHÔNG dừng được train.'} Chi tiết: {e}") from e
            except (TimeoutError, KeyboardInterrupt):
                training_running = not self._stop_training_and_wait()
                raise
            if st.get("error") or str(st.get("status", "")).startswith("❌"):
                raise AceStepError(f"Train lỗi: {st.get('error') or st.get('status')}")
            finite = [h for h in st.get("loss_history") or [] if isinstance(h.get("loss"), (int, float))]
            if last["nonfinite"] and not finite:
                raise AceStepError("Train phân kỳ: mọi gradient đều NaN/Inf (Non-finite gradients), LoRA không dùng được.")

            adapter = out_dir / "final" / "adapter"
            if not (adapter / "adapter_config.json").is_file():
                raise AceStepError(f"Train xong nhưng không thấy adapter ở {adapter} (status: {st.get('status')})")
            files = []
            for name in ("adapter_model.safetensors", "adapter_config.json"):
                shutil.copy(adapter / name, ctx.workdir / name)
                files.append(ctx.workdir / name)
            history = st.get("loss_history") or []
            final_loss = history[-1]["loss"] if history else st.get("current_loss")
            meta = {"backend": "ace", "base_model": base_model, "style_name": p["style_name"], "name": p["name"],
                    "dataset_id": p["dataset_id"], "dataset_version": p.get("dataset_version"),
                    "train_request": body, "final_loss": final_loss, "steps": st.get("current_step"),
                    "samples": len(labeled), "format": "peft-lora (ACE-Step /v1/lora/load)"}
            (ctx.workdir / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            files.append(ctx.workdir / "metadata.json")
            ctx.progress(0.92, f"[ace] train xong: {st.get('current_step')} bước, loss cuối={final_loss}")
            return BackendResult(result={"backend": self.name, "base_model": base_model,
                                         "metrics": {"final_loss": final_loss, "steps": st.get("current_step"),
                                                     "lora_alpha": body["lora_alpha"]}},
                                 files=files)
        finally:
            if training_running:
                # Luồng train của ACE-Step có thể vẫn đang đọc tensor: không nạp lại model (sẽ thành 2 bản trong
                # VRAM) và không xóa thư mục làm việc.
                ctx.progress(0.95, f"[ace] CẢNH BÁO: không xác nhận được train đã dừng — giữ nguyên {root}, "
                                   "KHÔNG nạp lại model. Hãy khởi động lại ACE-Step.")
            else:
                if touched_model:
                    # Train chèn LoRA thẳng vào decoder đang nạp; nạp lại model sạch cho các job sau.
                    try:
                        ctx.progress(0.95, f"[ace] nạp lại model sạch ({base_model}) sau train...")
                        self.client.init_model(base_model)
                    except Exception as e:  # không che lỗi chính
                        ctx.progress(0.95, f"[ace] CẢNH BÁO: không nạp lại được model sau train: {e}")
                shutil.rmtree(root, ignore_errors=True)
