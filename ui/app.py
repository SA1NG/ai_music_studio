"""Giao diện Gradio (tiếng Việt), bố cục kiểu Suno. Chỉ gọi API của chúng ta qua HTTP.
Chạy: python -m ui.app   (hoặc scripts/start_ui)
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime
from typing import Any, Iterator, Optional

import gradio as gr

from shared.config import get_settings
from shared.schemas import NO_LM
from shared.updates import UpdateError, check_for_update, feed_url
from shared.version import app_version

from . import theme
from .client import ApiError, StudioClient

NO_LORA = "(không dùng LoRA)"
NO_LM_LABEL = "Không dùng"
KEYS = ["", "C major", "G major", "D major", "A major", "E major", "F major", "Bb major", "Eb major",
        "A minor", "E minor", "B minor", "D minor", "G minor", "C minor", "F# minor"]
LIB_FILTERS = ["Tất cả", "👍 Đã thích", "Chưa đánh giá", "👎 Không thích"]
NOT_TAGS = {"instrumental", "no vocals"}
JOB_POLL = 1.0
FEED_LIMIT = 12

settings = get_settings()
api = StudioClient(settings.api_url, settings.api_token, cache_dir=settings.data_dir / "ui_cache")


def err(e: Exception) -> str:
    return f"⚠️ {e}"


def fmt_time(iso: str) -> str:
    """Thời gian UTC trong DB -> giờ địa phương để hiển thị."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return iso or ""


def lm_label(name: str) -> str:
    return NO_LM_LABEL if name == "none" else name


def model_choices() -> tuple[list[str], Optional[str], list[tuple[str, str]], Optional[str]]:
    """(dit, dit_default, lm (nhãn, giá trị), lm_default). Danh sách lấy động từ worker qua API."""
    m = api.models()
    dit = active_models(m)
    lm_names = [x["name"] for x in m["lm_models"]] or ["none"]
    lm = [(lm_label(n), n) for n in lm_names]
    dit_default = next((x["name"] for x in m["dit_models"] if x["is_default"]), dit[0] if dit else None)
    lm_default = next((x["name"] for x in m["lm_models"] if x["is_default"]), lm_names[0])
    return dit, dit_default, lm, lm_default


def active_models(models):
    """One fixed base model per engine session; only adapters can change in the UI."""
    available = models["dit_models"]
    if not available:
        return []
    return [next((m["name"] for m in available if m.get("is_default")), available[0]["name"])]


def lora_choices(dit_model: Optional[str]) -> list[tuple[str, Any]]:
    """Chỉ LoRA có base_model trùng với DiT model đang chọn."""
    if not dit_model:
        return [(NO_LORA, 0)]
    return [(NO_LORA, 0)] + [(f"[{x['style_name']}] {x['name']}", x["id"]) for x in api.loras(dit_model)]


def model_value(dit, lora_id=0):
    return json.dumps([dit, lora_id or 0]) if dit else None


def model_panel_label(dit=None):
    return f"Model · {dit}" if dit else "Model"


def combined_model_choices(dit_models):
    choices = []
    for dit in dit_models:
        choices.append((dit, model_value(dit)))
        for name, lora_id in lora_choices(dit)[1:]:
            choices.append((f"{name} · {dit}", model_value(dit, lora_id)))
    return choices


def duration_label(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    value = f"{minutes} phút" if minutes else ""
    if seconds:
        value += f" {seconds} giây"
    return f"Thời lượng · {value.strip()}"


def on_model_change(value):
    if not value:
        return None, 0, gr.update(label="Model"), gr.update(interactive=False)
    dit, lora_id = json.loads(value)
    return (gr.update(choices=[dit], value=dit), gr.update(choices=lora_choices(dit), value=lora_id),
            gr.update(label=model_panel_label(dit), open=False),
            gr.update(interactive=bool(lora_id)))


def wait_job(job_id: int) -> Iterator[dict]:
    """Poll job tới khi xong; yield job sau mỗi lần poll."""
    while True:
        job = api.job(job_id)
        yield job
        if job["status"] in ("done", "failed"):
            return
        time.sleep(JOB_POLL)


def job_status_md(job: dict, label: str) -> str:
    icon = {"queued": "⏳", "running": "🔄", "done": "✅", "failed": "❌"}[job["status"]]
    extra = ""
    if job["status"] == "queued":
        extra = " — đang chờ worker (nếu không có worker online, job sẽ chờ trong hàng đợi)"
    if job["status"] == "failed":
        extra = f"\n\n```\n{(job.get('error') or '')[:1500]}\n```"
    last = [l for l in (job.get("log") or "").splitlines() if l][-1:] or [""]
    return f"{icon} **{label} #{job['id']}** — {job['status']} — {job['progress'] * 100:.0f}% `{last[0]}`{extra}"


# =====================================================================
# Bài hát: chuyển dữ liệu API -> thẻ hiển thị
# =====================================================================
def auto_title(text: str, limit: int = 40) -> str:
    """Tên bài từ mô tả: lấy đoạn đầu (trước dấu phẩy), cắt ở ranh giới từ nếu quá dài."""
    first = re.split(r"[,.;\n]", text or "")[0]
    words = re.sub(r"[^\w\s'-]", " ", first).split()
    t = ""
    for w in words:
        if len(t) + len(w) + 1 > limit:
            t += "…"
            break
        t = f"{t} {w}".strip()
    return (t[:1].upper() + t[1:]) if t else "Bài không tên"


def tags_of(prompt: str, n: int = 4) -> list[str]:
    """Tag phong cách để hiện trên thẻ: bỏ các đoạn dài như một câu mô tả."""
    tags = [t.strip() for t in (prompt or "").split(",")]
    tags = [t for t in tags if t and t.lower() not in NOT_TAGS and len(t) <= 32]
    return tags[:n] or ["instrumental"]


def gen_item(g: dict, lora_names: dict[int, str]) -> dict:
    p = g["params"]
    metas = p.get("metas") or {}
    bpm = p.get("bpm") or metas.get("bpm")
    key = p.get("key_scale") or metas.get("keyscale")
    return {
        "kind": "song", "gen_id": g["id"], "key": f"g{g['id']}-{p.get('seed')}",
        "title": p.get("title") or auto_title(g["prompt_original"] or g["prompt_final"]),
        "tags": tags_of(g["prompt_final"]), "rating": g["rating"],
        "meta": [g.get("backend", "mock"), theme.fmt_duration(p.get("duration")), g["dit_model"],
                 f"seed {g.get('seed') if g.get('seed') is not None else p.get('seed')}",
                 f"{bpm} BPM" if bpm else "", key or ""],
        "lora": lora_names.get(g["lora_id"], f"#{g['lora_id']}") + f" ×{g['lora_scale']}" if g["lora_id"] else "",
    }


def pending_items() -> list[dict]:
    items = []
    for status in ("running", "queued"):
        for j in api.jobs(type="generate", status=status, limit=20):
            p = j["payload"]
            items.append({
                "kind": "pending", "job_id": j["id"], "key": f"job{j['id']}",
                "title": p.get("title") or auto_title(p.get("prompt_original") or p.get("prompt", "")),
                "tags": tags_of(p.get("prompt", "")), "progress": j["progress"],
                "meta": ["Đang tạo…" if status == "running" else "Đang chờ worker…", p.get("dit_model", "")],
            })
    return items


def lora_name_map() -> dict[int, str]:
    return {x["id"]: x["name"] for x in api.loras()}


def feed_items() -> list[dict]:
    try:
        names = lora_name_map()
        return pending_items() + [gen_item(g, names) for g in api.generations(FEED_LIMIT)]
    except ApiError:
        return []


def library_items(filt: str, query: str) -> list[dict]:
    try:
        names = lora_name_map()
        gens = api.generations(300)
    except ApiError:
        return []
    want = {"👍 Đã thích": 1, "Chưa đánh giá": 0, "👎 Không thích": -1}.get(filt)
    q = (query or "").strip().lower()
    out = []
    for g in gens:
        if want is not None and g["rating"] != want:
            continue
        item = gen_item(g, names)
        hay = " ".join([item["title"], g["prompt_final"], g["prompt_original"], g["dit_model"]]).lower()
        if q and q not in hay:
            continue
        out.append(item)
    return out


def details_md(gen_id: int) -> str:
    g = api.generation(gen_id)
    p = g["params"]
    lora = "—"
    if g["lora_id"]:
        lo = next((x for x in api.loras() if x["id"] == g["lora_id"]), None)
        lora = f"{lo['name']} (base `{lo['base_model']}`)" if lo else f"#{g['lora_id']}"
        lora += f" · độ mạnh {g['lora_scale']}"
    rows = [("Tên bài", p.get("title") or auto_title(g["prompt_original"])),
            ("Mô tả gốc", g["prompt_original"]), ("Prompt gửi model", g["prompt_final"]),
            ("Backend", f"**{g.get('backend', 'mock')}**"),
            ("DiT model", f"`{g['dit_model']}`"), ("LM model", lm_label(g["lm_model"])), ("LoRA", lora),
            ("Seed", f"`{p.get('seed')}`" + (" (ngẫu nhiên)" if (p.get("seed_requested") or 0) < 0 else "")),
            ("Thời lượng", theme.fmt_duration(p.get("duration"))), ("BPM", p.get("bpm") or "tự chọn"),
            ("Key", p.get("key_scale") or "tự chọn"), ("Nhịp", p.get("time_signature")),
            ("Tạo lúc", fmt_time(g["created_at"])), ("Job", f"#{g['job_id']}")]
    body = "\n".join(f"| {k} | {html.escape(str(v)) if k in ('Mô tả gốc', 'Prompt gửi model') else v} |"
                     for k, v in rows)
    return f"### Chi tiết bài #{g['id']}\n\n| Thuộc tính | Giá trị |\n|---|---|\n{body}"


# =====================================================================
# Tab: Tạo nhạc
# =====================================================================
def banner_md(models: Optional[dict] = None) -> str:
    """Cảnh báo trạng thái hệ thống trên tab Tạo nhạc (worker chưa chạy, ACE-Step chưa bật...)."""
    try:
        m = models or api.models()
    except ApiError as e:
        return err(e)
    lines = []
    if not m["workers_online"]:
        lines.append("⚠️ Không có worker nào online — job sẽ nằm chờ trong hàng đợi.")
    lines += [f"⚠️ {w}" for w in m.get("warnings", [])]
    if not m["dit_models"]:
        lines.append("⚠️ Chưa có model nào khả dụng. Hãy chạy worker.")
    return "\n\n".join(lines)


def generation_ready(models: dict, engine: dict) -> bool:
    """Có worker online, có DiT, không cảnh báo backend và (nếu desktop quản lý) engine đã ready."""
    ready = bool(models["workers_online"] and models["dit_models"] and not models.get("warnings"))
    return ready and (not engine.get("managed") or engine["status"] == "ready")


def on_tick_create(selected, chosen_lm, signature, creating):
    """Refresh available models after background startup without resetting the form."""
    try:
        models = api.models()
        engine = api.engine()
        ready = generation_ready(models, engine)
        started = os.getenv("MUSIC_STUDIO_SESSION_STARTED")
        if started and ready:
            ready = any(w.get("online") and "generate" in w.get("capabilities", [])
                        and datetime.fromisoformat(w["last_seen"]).timestamp() >= float(started)
                        for w in api.system()["workers"])
        info = banner_md(models) if ready else "⏳ Đang chờ model AI sẵn sàng. Bạn có thể nhập mô tả trước."
        if engine.get("managed") and engine["status"] == "idle":
            info = "Chưa nạp model. Mở thanh **Model**, chọn DiT/LM và bấm **Áp dụng** để bắt đầu."
        if engine.get("managed") and engine["status"] == "error":
            info = "❌ Nạp engine thất bại: " + html.escape(engine.get("error") or "") + ". Chọn lại DiT/LM và bấm Áp dụng."
        dit = active_models(models) if ready else []
        choices = combined_model_choices(dit)
        lm = [(lm_label(m["name"]), m["name"]) for m in models["lm_models"]] if ready else []
        new_signature = json.dumps([choices, lm, engine.get("status"), engine.get("revision")])
        disabled_label = "Chưa nạp model" if engine.get("status") == "idle" else "Engine chưa sẵn sàng" if engine.get("status") == "error" else "Đang nạp model…"
        button = gr.update(interactive=ready and not creating,
                           **({} if creating else {"value": "✦ Tạo nhạc" if ready else disabled_label}))
        valid_selection = any(key == selected for _, key in choices) or (not choices and selected is None)
        if signature == new_signature and valid_selection:
            return feed_items(), info, gr.update(interactive=ready), gr.skip(), gr.skip(), gr.skip(), gr.skip(), button, gr.skip(), signature
        value = selected if any(key == selected for _, key in choices) else model_value(dit[0]) if dit else None
        selected_dit, adapter_id = json.loads(value) if value else (None, 0)
        lm_value = (engine["lm"] if ready and engine.get("managed") else chosen_lm)
        lm_value = lm_value if any(key == lm_value for _, key in lm) else lm[0][1] if lm else None
        return (feed_items(), info, gr.update(choices=choices, value=value, interactive=ready),
                gr.update(choices=dit, value=selected_dit),
                gr.update(choices=lora_choices(selected_dit), value=adapter_id),
                gr.update(choices=lm, value=lm_value), gr.update(interactive=bool(adapter_id)), button,
                gr.update(label=model_panel_label(selected_dit)), new_signature)
    except ApiError as e:
        return (gr.skip(), err(e), gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip(),
                gr.update(interactive=False), gr.skip(), signature)


def on_load_create():
    try:
        dit, dit_d, lm, lm_d = model_choices()
        engine = api.engine()
        if engine.get("managed") and engine.get("status") != "ready":
            dit, dit_d, lm, lm_d = [], None, [], None
        if engine.get("managed") and engine["status"] == "ready" and any(key == engine["lm"] for _, key in lm):
            lm_d = engine["lm"]
        choices = combined_model_choices(dit)
        value = model_value(dit_d)
        info = banner_md()
        if engine.get("status") == "idle":
            info = "Chưa nạp model. Chọn DiT/LM trong thanh **Model**, rồi bấm **Áp dụng**."
        return (gr.update(choices=dit, value=dit_d), gr.update(choices=lm, value=lm_d),
                gr.update(choices=lora_choices(dit_d), value=0), feed_items(), info,
                gr.update(choices=choices, value=value),
                gr.update(label=model_panel_label(dit_d if not engine.get("managed") or engine.get("status") == "ready" else None)),
                gr.update(interactive=False))
    except ApiError as e:
        return gr.update(), gr.update(), gr.update(), [], err(e), gr.update(), gr.update(), gr.update()


def on_engine_options(signature):
    """Refresh available configurations without discarding un-applied dropdown edits."""
    try:
        engine = api.engine()
        managed = engine.get("managed", False)
        enabled = managed and engine["status"] in ("idle", "ready", "error")
        apply_label = "Áp dụng và nạp model" if engine.get("status") == "idle" else "Áp dụng và khởi động lại engine"
        new_signature = json.dumps([managed, engine.get("dit_models", []), engine.get("lm_models", [])])
        label = "Mở bằng Music Studio.bat để áp dụng cấu hình engine."
        if managed:
            prefix = "Đang chạy" if engine["status"] == "ready" else "Đang nạp" if engine["status"] == "loading" else "Chưa nạp · gợi ý cấu hình" if engine["status"] == "idle" else "Nạp thất bại"
            label = f"{prefix}: **{html.escape(engine['dit'])}** · LM: **{html.escape(lm_label(engine['lm']))}**"
        if signature == new_signature:
            return gr.update(interactive=enabled), gr.update(interactive=enabled), gr.update(interactive=enabled, value=apply_label), label, signature
        return (gr.update(choices=engine.get("dit_models", []), value=engine.get("dit"), interactive=enabled),
                gr.update(choices=[(lm_label(m), m) for m in engine.get("lm_models", ["none"])],
                          value=engine.get("lm", "none"), interactive=enabled),
                gr.update(interactive=enabled, value=apply_label), label, new_signature)
    except ApiError as exc:
        return gr.skip(), gr.skip(), gr.update(interactive=False), err(exc), signature


def on_apply_engine(dit, lm):
    if not dit:
        return gr.skip(), "Hãy chọn DiT model."
    try:
        engine = api.apply_engine(dit, lm or "none")
        if engine["status"] == "ready":
            return gr.skip(), "Cấu hình này đang được sử dụng."
        return gr.update(interactive=False, value="Đang nạp model…"), "⏳ Đã áp dụng. Engine đang khởi động lại; giao diện vẫn mở."
    except ApiError as exc:
        gr.Warning(str(exc))
        return gr.skip(), err(exc)


def on_create(title, style, dit, lm, lora_id, lora_scale, duration, seed, variants, task="text2music", instrumental=True, lyrics="", language="unknown",
              bpm=None, key="", meter="", source=None, reference=None, cover_strength=0.8,
              repaint_start=0, repaint_end=None, repaint_mode="balanced", steps=0, guidance=7,
              thinking=True, rewrite=False, temperature=0.85, negative=""):
    prompt = (style or "").strip()
    if not prompt:
        yield "⚠️ Hãy nhập mô tả phong cách nhạc.", gr.update()
        return
    if not dit:
        yield "⚠️ Chưa có DiT model nào. Hãy chạy worker rồi bấm ↻.", gr.update()
        return
    name = title.strip() or auto_title(style)
    base_seed = int(seed if seed is not None else -1)
    try:
        if not instrumental and not (lyrics or "").strip():
            yield "⚠️ Hãy nhập lời hát hoặc chọn Không lời.", gr.update()
            return
        if task != "text2music" and not source:
            yield "⚠️ Hãy tải audio nguồn để Remix hoặc Sửa đoạn.", gr.update()
            return
        if task == "repaint" and (repaint_end is None or repaint_end <= (repaint_start or 0)):
            yield "⚠️ Điểm kết thúc phải lớn hơn điểm bắt đầu.", gr.update()
            return
        source_path = api.upload_generation_audio(source)["path"] if task != "text2music" else None
        reference_path = api.upload_generation_audio(reference)["path"] if reference and task == "text2music" else None
        options = dict(task_type=task, instrumental=instrumental, lyrics=lyrics or "",
                       vocal_language=language or "unknown", bpm=int(bpm) if bpm else None,
                       key_scale=key or "", time_signature=meter or "", source_audio=source_path,
                       reference_audio=reference_path, audio_cover_strength=cover_strength,
                       repainting_start=repaint_start or 0, repainting_end=repaint_end if task == "repaint" else None,
                       repaint_mode=repaint_mode, inference_steps=int(steps) if steps else None,
                       guidance_scale=guidance, thinking=thinking, use_cot_caption=rewrite,
                       lm_temperature=temperature, lm_negative_prompt=negative or "")
        job_ids = []
        for i in range(int(variants)):
            job_ids.append(api.generate({
                "prompt": prompt, "prompt_original": prompt, "title": name, "dit_model": dit,
                "lm_model": lm or "none", "lora_id": int(lora_id) if lora_id else None,
                "lora_scale": float(lora_scale), "duration": float(duration),
                "seed": base_seed + i if base_seed >= 0 else -1,
                **options,
            }))
        while True:
            jobs = [api.job(j) for j in job_ids]
            pending = [j for j in jobs if j["status"] not in ("done", "failed")]
            done = sum(j["status"] == "done" for j in jobs)
            failed = [j for j in jobs if j["status"] == "failed"]
            if pending:
                yield f"🔄 Đang tạo {len(jobs)} bản… ({done}/{len(jobs)} xong)", feed_items()
                time.sleep(JOB_POLL)
                continue
            msg = f"✅ Đã tạo {done} bản **{html.escape(name)}**." if done else ""
            for j in failed:
                msg += f"\n\n❌ Job #{j['id']} lỗi:\n```\n{(j.get('error') or '')[:800]}\n```"
            yield msg, feed_items()
            return
    except (ApiError, OSError, ValueError) as e:
        yield err(e), feed_items()


def on_play(gen_id: int):
    try:
        g = api.generation(gen_id)
        p = g["params"]
        title = p.get("title") or auto_title(g["prompt_original"])
        meta = f"{g['dit_model']} · seed {p.get('seed')} · {theme.fmt_duration(p.get('duration'))}"
        return (api.fetch(g["audio_path"]), theme.now_playing_html(f"g{g['id']}-{p.get('seed')}", title, meta),
                gen_id)
    except ApiError as e:
        gr.Warning(str(e))
        return gr.update(), gr.update(), gr.update()


def on_rate(gen_id: int, value: int, current: int, lib_filter: str, lib_query: str):
    try:
        api.rate(gen_id, 0 if current == value else value)  # bấm lại lần nữa = bỏ đánh giá
    except ApiError as e:
        gr.Warning(str(e))
    return feed_items(), library_items(lib_filter, lib_query)


def on_reuse(gen_id: int):
    """Đổ lại toàn bộ cài đặt của một bài vào khung Tạo nhạc (để tái lập hoặc biến tấu)."""
    g = api.generation(gen_id)
    available = active_models(api.models())
    if g["dit_model"] not in available:
        raise gr.Error("Bài này dùng model gốc khác hoặc model chưa sẵn sàng. "
                       "Hãy đổi cấu hình ACE-Step và khởi động lại trước khi dùng lại.")
    p = g["params"]
    try:
        loras = lora_choices(g["dit_model"])
    except ApiError:
        loras = [(NO_LORA, 0)]
    choices = combined_model_choices([g["dit_model"]])
    value = model_value(g["dit_model"], g["lora_id"])
    if not any(key == value for _, key in choices):
        choices.append((f"{g['dit_model']} · LoRA #{g['lora_id']}", value))
    return (p.get("title") or auto_title(g["prompt_original"]), g["prompt_final"],
            gr.update(choices=[g["dit_model"]], value=g["dit_model"]), gr.update(value=g["lm_model"]),
            gr.update(choices=loras, value=g["lora_id"] or 0), g["lora_scale"],
            p.get("duration") or 180,
            p.get("seed", -1), 1, gr.Tabs(selected="create"), "create",
            f"↺ Đã nạp mô tả và cài đặt tạo nhạc của bài #{g['id']}.",
            gr.update(choices=choices, value=value), gr.update(label=model_panel_label(g["dit_model"]), open=False),
            p.get("task_type", "text2music"), p.get("instrumental", True), p.get("lyrics", ""),
            p.get("vocal_language", "unknown"), p.get("bpm"), p.get("key_scale", ""), p.get("time_signature", ""),
            api.fetch(p["source_audio"]) if p.get("source_audio") else None,
            api.fetch(p["reference_audio"]) if p.get("reference_audio") else None,
            p.get("audio_cover_strength", 0.8), p.get("repainting_start", 0), p.get("repainting_end"),
            p.get("repaint_mode", "balanced"), p.get("inference_steps") or 0, p.get("guidance_scale", 7),
            p.get("thinking", True), p.get("use_cot_caption", False), p.get("lm_temperature", 0.85),
            p.get("lm_negative_prompt", ""))


# =====================================================================
# Tab: Gán nhãn
# =====================================================================
DATASET_HEADERS = ["ID", "Tên", "Phiên bản", "Số track", "Tạo lúc"]


def review_selection(selected=None, tracks=None):
    tracks = api.tracks() if tracks is None else tracks
    statuses = {"pending": "Cần xem lại", "approved": "Sẵn sàng dùng", "rejected": "Đã loại"}
    choices = [(f"{t['filename']} · {statuses[t['review_status']]}", t["id"]) for t in tracks]
    if selected not in {t["id"] for t in tracks}:
        selected = next((t["id"] for t in tracks if t["review_status"] == "pending"), tracks[0]["id"] if tracks else None)
    approved = [(t["filename"], t["id"]) for t in tracks if t["review_status"] == "approved"]
    pending = sum(t["review_status"] == "pending" for t in tracks)
    summary = f"**{len(tracks)} bài** · {len(approved)} sẵn sàng dùng · {pending} cần xem lại"
    return gr.update(choices=choices, value=selected), summary, gr.update(choices=approved, value=[key for _, key in approved])


def on_review_refresh(selected=None):
    try:
        return (*review_selection(selected), "")
    except ApiError as exc:
        return gr.skip(), gr.skip(), gr.skip(), err(exc)


def refresh_label_availability(selected, checked, signature):
    try:
        tracks = api.tracks()
        dropdown, summary, ready = review_selection(selected, tracks)
        new_signature = json.dumps([dropdown["choices"], ready["choices"]])
        if new_signature == signature:
            return gr.skip(), gr.skip(), gr.skip(), signature
        previous_ids = {key for _, key in json.loads(signature)[1]} if signature else set(ready["value"])
        ready_ids = set(ready["value"])
        selected_ids = (set(checked or []) | (ready_ids - previous_ids)) & ready_ids
        valid = any(t["id"] == selected for t in tracks)
        return (gr.update(choices=dropdown["choices"], **({} if valid else {"value": dropdown["value"]})), summary,
                gr.update(choices=ready["choices"], value=[key for key in ready["value"] if key in selected_ids]), new_signature)
    except ApiError:
        return gr.skip(), gr.skip(), gr.skip(), signature


def on_review_upload(files):
    return on_upload_and_label(files, False, True, [])[:4]


def on_upload_and_label(files, automatic, instrumental, existing_jobs):
    if not files:
        return gr.skip(), gr.skip(), gr.skip(), "⚠️ Chọn file nhạc trước.", gr.skip(), gr.skip()
    try:
        paths = [f if isinstance(f, str) else f.name for f in files]
        created = api.upload_tracks(paths, autolabel=automatic, require_ai=automatic, is_instrumental=instrumental)
        jobs = list(existing_jobs or []) + [{"job_id": t["autolabel_job_id"], "track_id": t["id"]}
                                          for t in created if t.get("autolabel_job_id")]
        message = f"✅ Đã thêm {len(created)} bài. " + ("AI đang tạo nhãn; kết quả sẽ tự hiện ở bước 2." if automatic else "Nghe và nhập nhãn ở bước 2.")
        return (*review_selection(created[0]["id"] if created else None), message, jobs, None)
    except ApiError as exc:
        return gr.skip(), gr.skip(), gr.skip(), err(exc), gr.skip(), gr.skip()


def poll_upload_labels(pending_jobs, track_id, *fields):
    if not pending_jobs:
        return tuple(gr.skip() for _ in range(10))
    try:
        updated_jobs = [dict(item) for item in pending_jobs]
        jobs = [(item, api.job(item["job_id"])) for item in updated_jobs]
        finished = [(item, job) for item, job in jobs if job["status"] in ("done", "failed")]
        failures = [job for _, job in finished if job["status"] == "failed"]
        updates = tuple(gr.skip() for _ in range(8))
        for item, job in finished:
            if item["track_id"] == track_id and job["status"] == "done" and not item.get("applied"):
                labels = (job.get("result") or {}).get("auto_labels") or {}
                updates = apply_review_ai_labels({"track_id": track_id, **labels}, track_id, *fields)
                item["applied"] = True
        complete = len(finished) == len(jobs)
        ready_count = sum(bool((job.get('result') or {}).get('dataset_ready')) for _, job in finished)
        message = (f"✅ Đã lưu nhãn AI. {ready_count}/{len(jobs)} bài sẵn sàng dùng; bài còn lại cần xem lại kết quả đối chiếu hoặc bổ sung nhãn." if complete and not failures else
                   f"Đang gán nhãn AI: {len(finished)}/{len(jobs)} bài hoàn tất.")
        if failures:
            message = (f"⚠️ {len(failures)} bài gán nhãn thất bại. " + ("Các bài còn lại đã xử lý xong." if complete else message))
            message += "\n\n" + "\n\n".join(html.escape(job.get("error") or "Không nhận được nhãn.") for job in failures[:3])
            message += "\n\nChọn bài lỗi để phân tích lại hoặc nhập nhãn thủ công."
        return ([] if complete else updated_jobs), message, *updates
    except ApiError as exc:
        return gr.skip(), err(exc), *[gr.skip() for _ in range(8)]


def on_review_track(track_id):
    empty = (None, "", "", 0, "", "", "Chọn một bài để bắt đầu.", True, "", "unknown")
    if not track_id:
        return empty
    try:
        t = api.track(int(track_id))
        fields = (t["caption"], ", ".join(t["tags"]), t["bpm"] or 0, t["key_scale"], t["time_signature"],
                  t.get("is_instrumental", True), t.get("lyrics", ""), t.get("vocal_language", "unknown"))
        labels = t.get("auto_labels") or {}
        if labels.get("source") == "ace" and labels.get("is_instrumental", fields[5]) == fields[5]:
            fields = apply_review_ai_labels({"track_id": int(track_id), **labels}, int(track_id), *fields)
        return (api.fetch(t["path"]), *fields[:5], f"**{html.escape(t['filename'])}** · {theme.fmt_duration(t['duration'])}", *fields[5:])
    except ApiError as exc:
        return (*empty[:6], err(exc), *empty[7:])


def on_audio_validation_note(track_id):
    if not track_id:
        return ""
    try:
        track = api.track(int(track_id))
    except ApiError:
        return ""
    validation = (track.get('auto_labels') or {}).get('audio_validation')
    if not validation:
        return "Chưa có kết quả đối chiếu Essentia cho bài này."
    lm, measured = validation.get('lm', {}), validation.get('measured', {})
    status = validation.get('status')
    title = "✅ BPM/key đã đối chiếu và nằm trong ngưỡng chấp nhận." if status == 'verified' else "⚠️ Cần xem lại: chênh lệch đáng kể hoặc phép đo chưa đủ rõ."
    if status == 'unavailable':
        title = "⚠️ Chưa chạy được Essentia; không tự xác nhận nhãn LM."
    if status != 'verified' and track.get('review_status') == 'approved':
        title += " Admin đã lưu chấp nhận nhãn."
    def value(data):
        if data is None or data == '':
            return '—'
        return html.escape(str(round(data, 2) if isinstance(data, float) else data)).replace('|', '\\|')
    rows = [title, '', '| Thông số | LM | Essentia | Đang lưu |', '|---|---|---|---|',
            f"| BPM | {value(lm.get('bpm'))} | {value(measured.get('bpm'))} | {value(track.get('bpm'))} |",
            f"| Key | {value(lm.get('key_scale'))} | {value(measured.get('key_scale'))} | {value(track.get('key_scale'))} |",
            f"| Nhịp | {value(lm.get('time_signature'))} | Không kiểm tra | {value(track.get('time_signature'))} |", '',
            f"Độ rõ key (strength): {value(measured.get('key_strength'))} · {measured.get('key_profile_votes', 0)}/3 cách ước lượng cùng key. "
            f"Độ biến thiên khoảng beat (CV): {value(measured.get('beat_interval_cv'))}. Các chỉ số này không phải xác suất đúng."]
    rows.extend('- ' + html.escape(text) for text in validation.get('warnings', []))
    rows.extend('- ' + html.escape(text) for text in validation.get('notes', []))
    rows += ['', 'Essentia cũng có thể đo sai. Caption và lời hát vẫn do LM tạo; nhãn admin đã lưu được giữ nguyên.']
    return '\n'.join(rows)


def on_review_save(track_id, caption, tags, bpm, key, ts, instrumental, lyrics, language, exclude=False):
    if not track_id:
        return gr.skip(), gr.skip(), gr.skip(), "⚠️ Chọn một bài trước."
    if not exclude and not (caption or "").strip():
        return gr.skip(), gr.skip(), gr.skip(), "⚠️ Nhập caption mô tả bài trước khi lưu."
    if not exclude and not instrumental and not (lyrics or "").strip():
        return gr.skip(), gr.skip(), gr.skip(), "⚠️ Bài có lời cần nhập lời hát trước khi lưu."
    if not exclude and bpm and not 30 <= bpm <= 300:
        return gr.skip(), gr.skip(), gr.skip(), "⚠️ BPM phải từ 30 đến 300, hoặc 0 nếu chưa biết."
    try:
        values = {"review_status": "rejected"} if exclude else dict(
            caption=caption.strip(), tags=[t.strip() for t in (tags or "").split(",") if t.strip()],
            bpm=bpm or None, key_scale=key or "", time_signature=ts or "", is_instrumental=instrumental,
            lyrics=lyrics or "", vocal_language=language or "unknown", review_status="approved")
        api.update_track(int(track_id), values)
        pending = [t["id"] for t in api.tracks() if t["review_status"] == "pending"]
        following = next((key for key in pending if key > int(track_id)), pending[0] if pending else track_id)
        message = "Đã loại bài khỏi bộ dữ liệu." if exclude else "Đã lưu thay đổi. Bài sẵn sàng dùng cho dataset."
        if not pending:
            message += " Chọn bài ở bước 3 để tạo bộ dữ liệu."
        return (*review_selection(following), "✅ " + message)
    except ApiError as exc:
        return gr.skip(), gr.skip(), gr.skip(), err(exc)


def on_review_auto_label(track_id, instrumental, lyrics, language, transcribe):
    if not track_id:
        yield "⚠️ Chọn một bài trước khi phân tích.", None
        return
    try:
        job_id = api.autolabel(int(track_id), require_ai=True, is_instrumental=instrumental,
                              lyrics=lyrics or "", vocal_language=language or "unknown", transcribe_lyrics=transcribe)
        for job in wait_job(job_id):
            if job["status"] == "failed":
                yield "❌ " + html.escape(job.get("error") or "Gán nhãn thất bại."), None
                return
            if job["status"] == "done":
                labels = (job.get("result") or {}).get("auto_labels") or {}
                if labels.get("source") != "ace":
                    yield "❌ Không nhận được nhãn từ ACE-Step thật.", None
                    return
                message = "✅ Đã lưu nhãn AI. Bài sẵn sàng dùng cho dataset; xem/sửa nếu cần." if (job.get("result") or {}).get("dataset_ready") else "⚠️ Đã lưu kết quả AI. Bài còn thiếu nhãn hoặc đã bị loại; xem và bổ sung nếu muốn dùng cho dataset."
                yield message, {"track_id": int(track_id), **labels}
                return
            yield job_status_md(job, "Gán nhãn AI"), None
    except ApiError as exc:
        yield err(exc), None


def apply_review_ai_labels(suggestion, track_id, caption, tags, bpm, key, ts, instrumental, lyrics, language):
    if not suggestion or suggestion.get("track_id") != track_id or suggestion.get("source") != "ace" or suggestion.get("is_instrumental", instrumental) != instrumental:
        return tuple(gr.skip() for _ in range(8))
    return (caption or suggestion.get("caption", ""), tags, bpm or suggestion.get("bpm") or 0,
            key or suggestion.get("key_scale", ""), ts or suggestion.get("time_signature", ""), instrumental,
            lyrics or (suggestion.get("lyrics", "") if not instrumental else ""),
            suggestion.get("vocal_language", "unknown") if not instrumental and language in (None, "", "unknown") else language)


def on_dataset_to_finetune(name, track_ids, mode="new", base_dataset_id=None):
    if not track_ids:
        return gr.skip(), gr.skip(), "⚠️ Chọn ít nhất một bài đã kiểm tra.", *[gr.skip() for _ in range(5)]
    if mode == "new" and not (name or "").strip():
        return gr.skip(), gr.skip(), "⚠️ Đặt tên cho bộ dữ liệu mới.", *[gr.skip() for _ in range(5)]
    if mode == "append" and not base_dataset_id:
        return gr.skip(), gr.skip(), "⚠️ Chọn dataset có sẵn để thêm nhạc.", *[gr.skip() for _ in range(5)]
    try:
        if mode == "new" and any(d["name"] == name.strip() for d in api.datasets()):
            return gr.skip(), gr.skip(), "⚠️ Tên này đã có. Chọn Thêm nhạc vào dataset có sẵn, hoặc đặt tên mới.", *[gr.skip() for _ in range(5)]
        ds = api.create_dataset((name or "").strip(), [int(key) for key in track_ids],
                                cleanup_sources=True,
                                **({"base_dataset_id": int(base_dataset_id)} if mode == "append" else {}))
        choices = dataset_choices()
        message = f"✅ Bộ dữ liệu **{html.escape(ds['name'])} v{ds['version']}** có {len(ds['track_ids'])} bài. Chọn model và thông số rồi bấm Train khi sẵn sàng."
        if ds.get("removed_track_ids"):
            message += f" Đã dọn {len(ds['removed_track_ids'])} bản tải lên sau khi lưu dataset."
        if ds.get("cleanup_warnings"):
            message += "\n\n⚠️ " + " · ".join(html.escape(text) for text in ds["cleanup_warnings"])
        return (datasets_rows(), gr.update(choices=choices), message,
                gr.update(choices=choices, value=ds["id"]), ds["name"], gr.Tabs(selected="finetune"), "finetune", message)
    except ApiError as exc:
        return gr.skip(), gr.skip(), err(exc), *[gr.skip() for _ in range(5)]


def datasets_rows():
    return [[d["id"], d["name"], d["version"], len(d["track_ids"]), fmt_time(d["created_at"])]
            for d in api.datasets()]


def dataset_choices():
    return [(f"{d['name']} v{d['version']} ({len(d['track_ids'])} track)", d["id"]) for d in api.datasets()]


def existing_dataset_choices():
    latest = {}
    for dataset in api.datasets():
        if dataset["name"] not in latest or dataset["version"] > latest[dataset["name"]]["version"]:
            latest[dataset["name"]] = dataset
    return [(f"{d['name']} · v{d['version']} · {len(d['track_ids'])} bài", d["id"])
            for d in sorted(latest.values(), key=lambda item: item["name"].lower())]


def on_delete_uploaded_track(track_id, confirmed):
    if not track_id or not confirmed:
        return gr.skip(), gr.skip(), gr.skip(), "⚠️ Chọn bài và xác nhận xóa vĩnh viễn.", gr.skip()
    try:
        result = api.delete_track(int(track_id))
        message = "✅ Đã xóa bản tải lên và nhãn. Dataset đã tạo vẫn giữ audio riêng."
        if result.get("warnings"):
            message += "\n\n⚠️ " + " · ".join(result["warnings"])
        return (*review_selection(), message, False)
    except ApiError as exc:
        return gr.skip(), gr.skip(), gr.skip(), err(exc), gr.skip()


def on_delete_dataset(dataset_id, confirmed, selected_finetune):
    if not dataset_id or not confirmed:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), "⚠️ Chọn phiên bản và xác nhận xóa.", gr.skip(), gr.skip()
    try:
        result = api.delete_dataset(int(dataset_id))
        choices = dataset_choices()
        message = "✅ Đã xóa dữ liệu của phiên bản đã chọn. LoRA đã huấn luyện được giữ lại."
        if result.get("warnings"):
            message += "\n\n⚠️ " + " · ".join(result["warnings"])
        return (datasets_rows(), gr.update(choices=choices, value=None),
                gr.update(choices=existing_dataset_choices(), value=None), None, message,
                gr.update(choices=choices, value=selected_finetune if any(key == selected_finetune for _, key in choices) else None), False)
    except ApiError as exc:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), err(exc), gr.skip(), gr.skip()


def on_download_dataset(ds_id):
    if not ds_id:
        return None
    ds = next((d for d in api.datasets() if d["id"] == int(ds_id)), None)
    return api.fetch_dataset(ds) if ds else None


# =====================================================================
# Tab: Finetune
# =====================================================================
LORA_HEADERS = ["Phong cách", "Tên LoRA", "Base model", "Dataset", "Tham số", "Tạo lúc"]


def lora_rows():
    ds = {d["id"]: f"{d['name']} v{d['version']}" for d in api.datasets()}
    rows = []
    for x in sorted(api.loras(), key=lambda x: (x["style_name"], x["base_model"], -x["id"])):
        p = x["params"]
        params = f"r={p.get('rank')} lr={p.get('learning_rate')} ep={p.get('epochs')}"
        if "final_loss" in p:
            params += f" loss={p['final_loss']}"
        rows.append([x["style_name"], x["name"], x["base_model"], ds.get(x["dataset_id"], x["dataset_id"]),
                     params, fmt_time(x["created_at"])])
    return rows


def on_load_finetune():
    try:
        dit, dit_d, _, _ = model_choices()
        return gr.update(choices=dataset_choices()), gr.update(choices=dit, value=dit_d), lora_rows(), ""
    except ApiError as e:
        return gr.update(), gr.update(), [], err(e)


def on_train(ds_id, base_model, style, name, rank, lr, epochs):
    if not ds_id or not base_model or not style.strip():
        yield "⚠️ Cần chọn dataset, base model và nhập tên phong cách.", "", gr.update()
        return
    body = {"dataset_id": int(ds_id), "base_model": base_model, "style_name": style.strip(),
            "name": name.strip() or None,
            "params": {"rank": int(rank), "learning_rate": float(lr), "epochs": int(epochs)}}
    try:
        job_id = api.train(body)
        job = None
        for job in wait_job(job_id):
            yield job_status_md(job, "Train"), job.get("log", ""), gr.update()
        yield job_status_md(job, "Train"), job.get("log", ""), lora_rows()
    except ApiError as e:
        yield err(e), "", gr.update()


# =====================================================================
# Tab: Hệ thống
# =====================================================================
JOB_HEADERS = ["ID", "Loại", "Trạng thái", "Tiến độ", "Worker", "Cập nhật", "Lỗi"]
JOB_TYPE_LABELS = {"generate": "Sinh nhạc", "autolabel": "Gán nhãn", "train": "Train LoRA"}
RECENT_WORKER_SECONDS = 600


def on_system():
    try:
        s = api.system()
        m = api.models()
    except ApiError as e:
        return err(e), [], [], {}
    by_type = m.get("backends_by_type") or {}
    lines = [f"**Phiên bản:** {s.get('version', '—')} · **API:** `{settings.api_url}` · refiner: `{s['prompt_refiner']}` · "
             f"token: {'bật' if s['auth_enabled'] else 'tắt'}", "",
             "**Backend theo loại job** (worker online):", "",
             "| Loại job | Backend |", "|---|---|"]
    for job_type, label in JOB_TYPE_LABELS.items():
        names = by_type.get(job_type) or []
        lines.append(f"| {label} | {', '.join(f'**{n}**' for n in names) or '— (không có worker nhận)'} |")
    lines += [""] + [f"⚠️ {w}" for w in m.get("warnings", [])]
    lines += ["", "**Worker:**"]
    recent = datetime.now().astimezone().timestamp() - RECENT_WORKER_SECONDS
    shown = [w for w in s["workers"] if w["online"] or datetime.fromisoformat(w["last_seen"]).timestamp() >= recent]
    for w in shown:
        per_type = " · ".join(f"{JOB_TYPE_LABELS.get(t, t)}: **{b}**" for t, b in (w.get("backends") or {}).items())
        lines.append(f"- {'🟢' if w['online'] else '⚪'} `{w['worker_id']}` — {per_type or w['backend']} — "
                     f"lần cuối: {fmt_time(w['last_seen'])}")
    if len(shown) < len(s["workers"]):
        lines.append(f"- … và {len(s['workers']) - len(shown)} worker đã offline hơn "
                     f"{RECENT_WORKER_SECONDS // 60} phút (tự xóa sau 1 ngày)")
    if not s["workers"]:
        lines.append("- (chưa có worker nào đăng ký)")
    queue = [[q["type"], q["status"], q["count"]] for q in s["queue"]]
    jobs = [[j["id"], j["type"], j["status"], f"{j['progress'] * 100:.0f}%", j["worker_id"] or "",
             fmt_time(j["updated_at"]), (j["error"] or "")[:80]] for j in api.jobs(limit=30)]
    models = {"dit_models": [x["name"] for x in m["dit_models"]],
              "lm_models": [lm_label(x["name"]) for x in m["lm_models"]],
              "backends_by_type": by_type, "workers_online": m["workers_online"]}
    return "\n".join(lines), queue, jobs, models


def update_notice_md() -> str:
    """Thông báo bản mới ở thanh bên. Cập nhật thực hiện trong cửa sổ điều khiển (cần dừng dịch vụ)."""
    try:
        manifest = check_for_update(feed_url(settings.update_url), use_cache=True)
    except UpdateError:
        return ""
    if not manifest:
        return ""
    return (f"🔔 **Có bản mới {html.escape(manifest['version'])}.** Chờ tác vụ xong, mở cửa sổ điều khiển "
            "Music Studio và bấm **Cập nhật**.")


# =====================================================================
def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Music Studio") as demo:
        playing = gr.State(None)  # gen_id đang phát (để tô sáng thẻ)
        creating = gr.State(False)
        models_signature = gr.State("")
        engine_options_signature = gr.State("")
        with gr.Row(elem_classes="studio-shell", equal_height=False):
            with gr.Column(scale=0, min_width=210, elem_classes="studio-sidebar"):
                gr.HTML(theme.BRAND_HTML)
                gr.Markdown("KHÔNG GIAN LÀM VIỆC", elem_classes="sidebar-caption")
                navigation = gr.Radio(
                    [("✦  Tạo nhạc", "create"), ("♫  Thư viện", "library"),
                     ("🏷  Gán nhãn", "label"), ("◈  Finetune", "finetune"), ("⚙  Hệ thống", "system")],
                    value="create", label="Điều hướng", show_label=False,
                    elem_classes="sidebar-navigation", interactive=True,
                )
                gr.Markdown(f"**Music Studio** · v{app_version()}\n\nKhông gian thử nghiệm nhạc AI",
                            elem_classes="sidebar-note")
                update_notice = gr.Markdown(elem_classes="sidebar-note")
            with gr.Column(scale=1, min_width=0, elem_classes="studio-workspace"):
                with gr.Tabs(selected="create", elem_classes="workspace-tabs") as tabs:
                    # ---------------- Tạo nhạc ----------------
                    with gr.Tab("✦ Tạo nhạc", id="create"):
                        gr.Markdown("## Tạo nhạc\nMô tả phong cách, chọn model và nghe thử các phiên bản.", elem_classes="page-heading")
                        info = gr.Markdown(elem_classes="status")
                        dit_dd = gr.Dropdown(choices=[], visible=False)
                        lora_dd = gr.Dropdown(choices=[(NO_LORA, 0)], value=0, visible=False)
                        with gr.Row(equal_height=False, elem_classes="create-layout"):
                            with gr.Column(scale=5, min_width=300, elem_classes="create-panel"):
                                with gr.Accordion("Model", open=False, elem_classes="model-picker") as model_panel:
                                    with gr.Row(elem_classes="engine-selection"):
                                        draft_dit = gr.Dropdown(label="DiT model", choices=[], interactive=False, min_width=140,
                                                                elem_classes="draft-dit")
                                        draft_lm = gr.Dropdown(label="LM model", choices=[], interactive=False, min_width=140,
                                                               elem_classes="draft-lm")
                                    apply_engine = gr.Button("Áp dụng và khởi động lại engine", interactive=False,
                                                             elem_classes="apply-engine")
                                    engine_note = gr.Markdown(elem_classes="engine-note")
                                    model_picker = gr.Dropdown(choices=[], label="Model đang dùng / LoRA", interactive=True,
                                                               elem_classes="model-select")
                                    adapter_note = gr.Markdown(visible=False, elem_classes="adapter-note")
                                    with gr.Row(elem_classes="model-settings"):
                                        lm_dd = gr.Dropdown(choices=[], visible=False)
                                        seed = gr.Number(label="Seed (-1 = ngẫu nhiên)", value=-1, precision=0, min_width=120)
                                    lora_scale = gr.Slider(0, 1, value=1.0, step=0.05,
                                                          label="Độ mạnh LoRA (0–1)", interactive=False)
                                    refresh_models = gr.Button("↻ Làm mới danh sách model", size="sm")
                                title = gr.Textbox(label="Tên bài (tùy chọn)", placeholder="Đêm mưa phố cổ")
                                task = gr.Radio([("Tạo mới", "text2music"), ("Remix", "cover"), ("Sửa đoạn", "repaint")],
                                                value="text2music", label="Bạn muốn làm gì?", elem_classes="task-selector")
                                instrumental = gr.Radio([("Không lời", True), ("Có lời", False)], value=True,
                                                        label="Giọng hát", elem_classes="vocal-selector")
                                with gr.Group(visible=False) as vocal_group:
                                    lyrics = gr.Textbox(label="Lời hát", lines=6, placeholder="[Verse]\n…\n[Chorus]\n…", elem_classes="lyrics-input")
                                    language = gr.Dropdown([("Tự động", "unknown"), ("Tiếng Việt", "vi"), ("English", "en"),
                                                            ("中文", "zh"), ("日本語", "ja"), ("한국어", "ko")],
                                                           value="unknown", label="Ngôn ngữ hát", allow_custom_value=True)
                                style = gr.Textbox(label="Mô tả phong cách nhạc (caption tiếng Anh)", lines=4, max_lines=8,
                                                   elem_classes="prompt-input",
                                                   info="Mô tả thể loại, cảm xúc và nhạc cụ. Thông số chọn bên dưới được ưu tiên hơn caption.",
                                                   placeholder="lofi hip hop, soft piano, vinyl crackle, 80 BPM, cozy, "
                                                               "gentle drums")
                                with gr.Row(elem_classes="music-metadata"):
                                    bpm = gr.Number(label="BPM (0 = Tự động)", value=0, precision=0, minimum=0, maximum=300,
                                                    min_width=90)
                                    key = gr.Dropdown([("Tự động", "")] + [(f"{note} {scale}", f"{note} {scale}")
                                                      for scale in ("major", "minor")
                                                      for note in ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")], value="",
                                                      label="Key / Tông", min_width=100)
                                    meter = gr.Dropdown([("Tự động", ""), ("2/4", "2"), ("3/4", "3"), ("4/4", "4"), ("6/8", "6")],
                                                        value="", label="Nhịp", min_width=90)
                                with gr.Accordion("Thời lượng & số bản · 3 phút · 2 bản", open=False,
                                                  elem_classes="generation-settings") as duration_panel:
                                    with gr.Row(elem_classes="generation-settings-row"):
                                        duration = gr.Slider(10, 240, value=180, step=5, label="Thời lượng (giây)", scale=3, min_width=160)
                                        variants = gr.Radio([1, 2], value=2, label="Số bản mỗi lần tạo", scale=2, min_width=120)
                                with gr.Group(visible=False, elem_classes="source-settings") as source_group:
                                    source = gr.Audio(label="Audio nguồn · tải lên và nghe thử", type="filepath", sources=["upload"])
                                    gr.Markdown("Remix/Sửa đoạn dùng thời lượng audio nguồn (10–600 giây).")
                                    cover_strength = gr.Slider(0, 1, value=0.8, step=0.05, label="Mức giữ bản gốc", visible=False)
                                    with gr.Group(visible=False) as repaint_group:
                                        with gr.Row(elem_classes="music-metadata"):
                                            repaint_start = gr.Number(label="Bắt đầu (giây)", value=0, minimum=0, min_width=100)
                                            repaint_end = gr.Number(label="Kết thúc (giây)", value=None, minimum=0, min_width=100)
                                        repaint_mode = gr.Radio([("Nhẹ", "conservative"), ("Cân bằng", "balanced"), ("Mạnh", "aggressive")],
                                                                value="balanced", label="Mức độ sửa")
                                        gr.Markdown("Chỉ định khoảng nằm trong audio nguồn; phần ngoài khoảng được giữ lại.")
                                with gr.Accordion("Tùy chỉnh nâng cao", open=False, elem_classes="advanced-generation"):
                                    steps = gr.Number(value=0, minimum=0, maximum=200, precision=0, label="Số bước sinh",
                                                      info="0 = mặc định theo model; nhiều bước hơn không luôn tốt hơn.")
                                    guidance = gr.Slider(1, 15, value=7, step=0.5, label="Độ bám caption (CFG)", visible=False)
                                    with gr.Group(visible=False) as lm_options:
                                        thinking = gr.Checkbox(value=True, label="Dùng LM lập kế hoạch âm nhạc")
                                        rewrite = gr.Checkbox(value=False, label="Cho LM cải thiện caption")
                                        temperature = gr.Slider(0, 2, value=0.85, step=0.05, label="Độ sáng tạo LM")
                                        negative = gr.Textbox(label="Những yếu tố muốn tránh (LM)", lines=2)
                                    with gr.Group() as reference_group:
                                        reference = gr.Audio(label="Audio tham chiếu phong cách (tùy chọn)", type="filepath", sources=["upload"])
                            with gr.Column(scale=7, min_width=300, elem_classes="create-results"):
                                create_btn = gr.Button("Đang nạp model…", interactive=False, variant="primary",
                                                       elem_classes=["create-btn", "create-action"])
                                status = gr.Markdown(elem_classes=["status", "generation-status"])
                                with gr.Row(elem_classes="feed-head"):
                                    gr.Markdown("### Bài gần đây")
                                    feed_refresh = gr.Button("↻", elem_classes="icon-btn", scale=0, min_width=36)
                                feed = gr.State([])

                                @gr.render(inputs=[feed, playing], show_progress="hidden")
                                def _feed(items, current):
                                    song_cards(items, current, with_details=False)

                    # ---------------- Thư viện ----------------
                    with gr.Tab("♫ Thư viện", id="library") as lib_tab:
                        gr.Markdown("## Thư viện\nNghe lại, đánh giá và dùng lại cài đặt của những bài đã tạo.", elem_classes="page-heading")
                        with gr.Row(elem_classes="lib-bar"):
                            lib_filter = gr.Radio(LIB_FILTERS, value=LIB_FILTERS[0], show_label=False, elem_classes="seg",
                                                  scale=0, min_width=480)
                            lib_query = gr.Textbox(show_label=False, placeholder="🔎 Tìm theo tên, prompt, model…", scale=1)
                        lib = gr.State([])
                        with gr.Row(equal_height=False):
                            with gr.Column(scale=7, min_width=320):
                                @gr.render(inputs=[lib, playing], show_progress="hidden")
                                def _lib(items, current):
                                    song_cards(items, current, with_details=True)
                            with gr.Column(scale=5, min_width=300):
                                lib_details = gr.Markdown("Bấm **ⓘ** trên một bài để xem đủ model, LoRA, seed và tham số.",
                                                          elem_classes="details")

                    # ---------------- Gán nhãn ----------------
                    with gr.Tab("🏷 Gán nhãn", id="label"):
                        gr.Markdown("## Gán nhãn & dataset\nChuẩn bị nhạc tham chiếu để huấn luyện phong cách riêng.", elem_classes="page-heading")
                        with gr.Column(elem_classes=["panel", "label-step"]):
                            gr.Markdown("### 1. Thêm nhạc & tạo nhãn\nTải nhạc lên, AI tự phân tích từng bài. Bạn kiểm tra kết quả ở bước 2.")
                            uploads = gr.File(label="Kéo thả hoặc chọn file nhạc", file_count="multiple",
                                              file_types=[".wav", ".mp3", ".flac", ".ogg", ".opus"], type="filepath", elem_classes="label-upload")
                            automatic_labels = gr.Radio([("Tự gán nhãn bằng AI", True), ("Nhập nhãn thủ công", False)],
                                                        value=True, label="Xử lý sau khi thêm", elem_classes="label-upload-mode")
                            upload_instrumental = gr.Radio([("Không lời", True), ("Có lời", False)], value=True,
                                                            label="Giọng hát của các bài tải lên", elem_classes="label-upload-vocals")
                            gr.Markdown("Chế độ AI cần DiT và LM đã nạp. Với nhạc có lời, AI sẽ nhận dạng lời hát; vẫn cần kiểm tra lại.")
                            upload_label_jobs = gr.State([])
                            with gr.Row():
                                upload_btn = gr.Button("Thêm nhạc & tự gán nhãn", variant="primary", elem_classes="label-upload-button")
                                refresh_tr = gr.Button("↻ Làm mới")
                            review_summary = gr.Markdown()
                            upload_ai_status = gr.Markdown(elem_classes="status")
                        label_msg = gr.Markdown(elem_classes="status")
                        with gr.Column(elem_classes=["panel", "label-step"]):
                            gr.Markdown("### 2. Xem / sửa nhãn (tùy chọn)\nNhãn AI được lưu tự động. Chỉ mở bài để nghe, xem hoặc sửa khi cần; bài nhập thủ công cần bổ sung nhãn trước khi dùng.")
                            track_dd = gr.Dropdown(label="Chọn bài", choices=[], interactive=True, elem_classes="label-track-picker")
                            selected_review_track = gr.State(None)
                            ai_suggestion = gr.State(None)
                            with gr.Row():
                                ai_label_btn = gr.Button("Phân tích lại bằng AI", size="sm", elem_classes="label-ai-button")
                                transcribe = gr.Checkbox(value=True, label="Nhận dạng lời hát nếu chọn Có lời")
                            gr.Markdown("AI tự lưu các nhãn còn trống, giữ nhãn đã lưu của bạn. Bài đủ caption và lời hát (nếu có) sẽ sẵn sàng dùng cho dataset.")
                            ai_label_status = gr.Markdown(elem_classes="status")
                            validation_note = gr.Markdown(elem_classes="audio-validation-note")
                            with gr.Row(elem_classes="label-review-layout"):
                                with gr.Column(scale=2, min_width=240):
                                    track_info = gr.Markdown()
                                    track_audio = gr.Audio(label="Nghe bài", type="filepath", interactive=False)
                                with gr.Column(scale=3, min_width=240):
                                    label_instrumental = gr.Radio([("Không lời", True), ("Có lời", False)], value=True, label="Giọng hát", elem_classes="label-vocal-selector")
                                    cap_box = gr.Textbox(label="Caption · mô tả bài bằng tiếng Anh", lines=3,
                                                         info="Mô tả thể loại, nhạc cụ và cảm xúc thực tế của bài.", elem_classes="label-caption")
                                    with gr.Row(elem_classes="music-metadata"):
                                        t_bpm = gr.Number(label="BPM (0 = chưa biết)", value=0, minimum=0, maximum=300, min_width=90)
                                        t_key = gr.Dropdown([("Chưa biết", "")] + [(k, k) for k in KEYS if k], value="",
                                                            label="Key", allow_custom_value=True, min_width=100)
                                        t_ts = gr.Dropdown([("Chưa biết", ""), ("2/4", "2"), ("3/4", "3"), ("4/4", "4"), ("6/8", "6")],
                                                           value="", label="Nhịp", min_width=90)
                                    with gr.Group(visible=False) as label_vocals:
                                        label_lyrics = gr.Textbox(label="Lời hát", lines=5, elem_classes="label-lyrics")
                                        label_language = gr.Dropdown([("Chưa xác định", "unknown"), ("Tiếng Việt", "vi"), ("English", "en")],
                                                                     value="unknown", label="Ngôn ngữ hát", allow_custom_value=True)
                                    with gr.Accordion("Thông tin bổ sung", open=False):
                                        tags_box = gr.Textbox(label="Tags (tùy chọn, phân cách bằng dấu phẩy)")
                            with gr.Row():
                                approve_btn = gr.Button("Lưu thay đổi & tiếp theo", variant="primary", elem_classes="label-save-next")
                                reject_btn = gr.Button("Loại khỏi bộ dữ liệu")
                            with gr.Accordion("Xóa bài tải lên", open=False, elem_classes="track-delete-panel"):
                                track_delete_confirm = gr.Checkbox(label="Xác nhận xóa vĩnh viễn bài đang chọn và nhãn của bài", elem_classes="track-delete-confirm")
                                track_delete_btn = gr.Button("Xóa bài tải lên", variant="stop", elem_classes="track-delete-button")
                        with gr.Column(elem_classes=["panel", "label-step"]):
                            gr.Markdown("### 3. Tạo bộ dữ liệu\nTạo mới hoặc thêm nhạc vào dataset có sẵn. Bước này mở Finetune và chưa bắt đầu huấn luyện.")
                            ds_mode = gr.Radio([("Tạo dataset mới", "new"), ("Thêm nhạc vào dataset có sẵn", "append")],
                                               value="new", label="Bạn muốn tạo hay bổ sung?", elem_classes="dataset-mode")
                            ds_name = gr.Textbox(label="Tên bộ dữ liệu mới", value="my-style", elem_classes="dataset-name")
                            with gr.Group(visible=False) as ds_existing_group:
                                ds_existing = gr.Dropdown(choices=[], label="Dataset có sẵn · phiên bản mới nhất", elem_classes="dataset-existing")
                                ds_existing_refresh = gr.Button("↻ Làm mới dataset")
                                gr.Markdown("Giữ các bài và nhãn trong dataset gốc, thêm các bài được chọn bên dưới, bỏ qua bài trùng và lưu thành phiên bản mới.")
                            approved_tracks = gr.CheckboxGroup(choices=[], value=[], label="Các bài sẵn sàng đưa vào bộ dữ liệu", elem_classes="label-approved-tracks")
                            checked_dataset_tracks = gr.State([])
                            gr.Markdown("Sau khi dataset được lưu và kiểm tra thành công, các bản tải lên được tự dọn. Audio và nhãn nằm trong thư mục dataset.")
                            ds_btn = gr.Button("Dùng để finetune", variant="primary", elem_classes="label-to-finetune")
                        ds_msg = gr.Markdown(elem_classes="status")
                        with gr.Accordion("Quản lý bộ dữ liệu · tải ZIP & xóa phiên bản", open=False, elem_classes="dataset-management"):
                            ds_df = gr.Dataframe(headers=DATASET_HEADERS, interactive=False)
                            ds_pick = gr.Dropdown(label="Chọn phiên bản", choices=[], interactive=True, elem_classes="dataset-management-picker")
                            ds_download_btn = gr.Button("Tải ZIP", elem_classes="dataset-download-button")
                            ds_file = gr.File(label="File ZIP", interactive=False, elem_classes="dataset-export-file")
                            ds_delete_confirm = gr.Checkbox(label="Xác nhận xóa vĩnh viễn dữ liệu của phiên bản đang chọn", elem_classes="dataset-delete-confirm")
                            ds_delete_btn = gr.Button("Xóa phiên bản dataset", variant="stop", elem_classes="dataset-delete-button")

                    # ---------------- Finetune ----------------
                    with gr.Tab("🏋 Finetune", id="finetune"):
                        gr.Markdown("## Finetune\nHuấn luyện LoRA từ bộ dữ liệu đã duyệt.", elem_classes="page-heading")
                        ft_msg = gr.Markdown(elem_classes="status")
                        with gr.Column(elem_classes="panel"):
                            with gr.Row():
                                ft_ds = gr.Dropdown(label="Phiên bản dataset", choices=[], interactive=True)
                                ft_base = gr.Dropdown(label="Base model (model gốc để train)", choices=[], interactive=True)
                            with gr.Row():
                                ft_style = gr.Textbox(label="Tên phong cách", placeholder="ví dụ: lofi-chieu-mua")
                                ft_name = gr.Textbox(label="Tên LoRA (bỏ trống = tự đặt)")
                            with gr.Row():
                                ft_rank = gr.Number(label="Rank", value=16, precision=0)
                                ft_lr = gr.Number(label="Learning rate", value=1e-4)
                                ft_epochs = gr.Number(label="Epochs", value=10, precision=0)
                            with gr.Row():
                                ft_btn = gr.Button("🏋️ Train", variant="primary")
                                ft_refresh = gr.Button("↻ Làm mới")
                        ft_status = gr.Markdown()
                        ft_log = gr.Textbox(label="Log", lines=10, max_lines=20, interactive=False, autoscroll=True)
                        gr.Markdown("### LoRA theo phong cách\nCùng một dataset có thể train nhiều LoRA trên các base model "
                                    "khác nhau (ví dụ bản 2B để thử nhanh, bản XL làm bản thật).")
                        lora_df = gr.Dataframe(headers=LORA_HEADERS, interactive=False, wrap=True)

                    # ---------------- Hệ thống ----------------
                    with gr.Tab("⚙ Hệ thống", id="system"):
                        gr.Markdown("## Hệ thống\nTheo dõi model, worker và tiến độ xử lý tác vụ.", elem_classes="page-heading")
                        sys_btn = gr.Button("↻ Làm mới", size="sm")
                        sys_md = gr.Markdown(elem_classes="panel")
                        with gr.Row():
                            with gr.Column():
                                gr.Markdown("**Hàng đợi**")
                                queue_df = gr.Dataframe(headers=["Loại", "Trạng thái", "Số job"], interactive=False)
                            with gr.Column():
                                gr.Markdown("**Model khả dụng**")
                                models_json = gr.JSON()
                        gr.Markdown("**Job gần đây**")
                        jobs_df = gr.Dataframe(headers=JOB_HEADERS, interactive=False, wrap=True)

        # ---------------- Thanh phát nhạc cố định ở đáy ----------------
        with gr.Row(elem_classes="player-bar", equal_height=True):
            now_html = gr.HTML(theme.now_playing_html(), scale=0, min_width=220, elem_classes="now-cell")
            player = gr.Audio(type="filepath", autoplay=True, show_label=False, interactive=False, min_width=0,
                              scale=7, elem_classes="audio")

        # ================= Thẻ bài (dùng chung cho feed và thư viện) =================
        # song_cards được gọi lúc chạy (bởi gr.render), khi mọi component bên dưới đã tồn tại.
        reuse_outputs = [title, style, dit_dd, lm_dd, lora_dd, lora_scale,
                         duration, seed, variants, tabs, navigation, status, model_picker, model_panel]
        generation_options = [task, instrumental, lyrics, language, bpm, key, meter, source, reference,
                              cover_strength, repaint_start, repaint_end, repaint_mode, steps, guidance,
                              thinking, rewrite, temperature, negative]
        reuse_outputs += generation_options

        def song_cards(items: list[dict], current: Optional[int], with_details: bool) -> None:
            if not items:
                gr.HTML('<div class="empty">Chưa có bài nào. Viết mô tả bên trái rồi bấm <b>✦ Tạo nhạc</b>.</div>')
                return
            for it in items:
                is_song = it["kind"] == "song"
                cls = ["song-card"] + (["playing"] if is_song and it["gen_id"] == current else [])
                with gr.Row(elem_classes=cls, equal_height=True):
                    gr.HTML(theme.cover_html(it["key"], pending=not is_song), scale=0, min_width=56,
                            padding=False, elem_classes="cover-cell")
                    gr.HTML(theme.song_info_html(it["title"], it["tags"], it["meta"],
                                                 None if is_song else it["progress"], it.get("lora", "")),
                            scale=1, min_width=0, padding=False, elem_classes="info-cell")
                    if not is_song:
                        continue
                    gid, rating = it["gen_id"], it["rating"]
                    play = gr.Button("▶", elem_classes=["icon-btn", "play"], scale=0, min_width=36)
                    like = gr.Button("👍", elem_classes=["icon-btn"] + (["on"] if rating == 1 else []),
                                     scale=0, min_width=36)
                    dislike = gr.Button("👎", elem_classes=["icon-btn"] + (["on"] if rating == -1 else []),
                                        scale=0, min_width=36)
                    reuse = gr.Button("↺", elem_classes=["icon-btn"], scale=0, min_width=36)
                    play.click(lambda g=gid: on_play(g), None, [player, now_html, playing], show_progress="hidden")
                    like.click(lambda f, q, g=gid, r=rating: on_rate(g, 1, r, f, q), [lib_filter, lib_query],
                               [feed, lib], show_progress="hidden")
                    dislike.click(lambda f, q, g=gid, r=rating: on_rate(g, -1, r, f, q), [lib_filter, lib_query],
                                  [feed, lib], show_progress="hidden")
                    reuse.click(lambda g=gid: on_reuse(g), None, reuse_outputs, show_progress="hidden")
                    if with_details:
                        info_btn = gr.Button("ⓘ", elem_classes=["icon-btn"], scale=0, min_width=36)
                        info_btn.click(lambda g=gid: details_md(g), None, lib_details, show_progress="hidden")

        # ================= Sự kiện: Tạo nhạc =================
        load_outputs = [dit_dd, lm_dd, lora_dd, feed, info, model_picker, model_panel, lora_scale]
        def navigate(section, filt, query):
            return gr.Tabs(selected=section), library_items(filt, query) if section == "library" else gr.skip()

        navigation.change(navigate, [navigation, lib_filter, lib_query], [tabs, lib], show_progress="hidden")
        demo.load(on_load_create, outputs=load_outputs)
        refresh_models.click(on_load_create, outputs=load_outputs)
        engine_option_outputs = [draft_dit, draft_lm, apply_engine, engine_note, engine_options_signature]
        demo.load(on_engine_options, engine_options_signature, engine_option_outputs)
        gr.Timer(4).tick(on_engine_options, engine_options_signature, engine_option_outputs, show_progress="hidden")
        apply_engine.click(on_apply_engine, [draft_dit, draft_lm], [create_btn, engine_note],
                           show_progress="hidden", queue=False)
        feed_refresh.click(feed_items, outputs=feed, show_progress="hidden")
        # Tự làm mới feed (job xếp từ nơi khác, worker bật lại...). State không đổi thì không vẽ lại.
        gr.Timer(4).tick(on_tick_create, inputs=[model_picker, lm_dd, models_signature, creating],
                        outputs=[feed, info, model_picker, dit_dd, lora_dd, lm_dd, lora_scale, create_btn, model_panel, models_signature],
                        show_progress="hidden")
        model_picker.change(on_model_change, model_picker, [dit_dd, lora_dd, model_panel, lora_scale],
                            show_progress="hidden")
        def settings_summary(seconds, count, task_type):
            if task_type != "text2music":
                return gr.update(label=f"Theo audio nguồn · {int(count)} bản")
            return gr.update(label=duration_label(seconds).replace("Thời lượng ·", "Thời lượng & số bản ·") + f" · {int(count)} bản")

        duration.change(settings_summary, [duration, variants, task], duration_panel, show_progress="hidden")
        variants.change(settings_summary, [duration, variants, task], duration_panel, show_progress="hidden")
        task.change(settings_summary, [duration, variants, task], duration_panel, show_progress="hidden")

        def adapter_caption(value):
            if not value:
                return gr.update(value="", visible=False)
            dit, adapter_id = json.loads(value)
            name = next((item["name"] for item in api.loras(dit) if item["id"] == adapter_id), "") if adapter_id else ""
            return gr.update(value=f"Adapter LoRA: {html.escape(name)}", visible=bool(name))

        model_picker.change(adapter_caption, model_picker, adapter_note, show_progress="hidden")
        instrumental.change(lambda value: gr.update(visible=not value), instrumental, vocal_group,
                            show_progress="hidden")
        task.change(lambda value: (gr.update(visible=value != "text2music"), gr.update(visible=value == "cover"),
                                   gr.update(visible=value == "repaint"), gr.update(visible=value == "text2music"),
                                   gr.update(visible=value == "text2music")),
                    task, [source_group, cover_strength, repaint_group, reference_group, duration], show_progress="hidden")
        def show_lm_options(value, task_type):
            return gr.update(visible=bool(value and value != NO_LM and task_type == "text2music"))

        lm_dd.change(show_lm_options, [lm_dd, task], lm_options, show_progress="hidden")
        task.change(show_lm_options, [lm_dd, task], lm_options, show_progress="hidden")
        dit_dd.change(lambda value: gr.update(visible=bool(value and "turbo" not in value.lower())),
                      dit_dd, guidance, show_progress="hidden")
        create_event = create_btn.click(
            lambda: (gr.update(interactive=False, value="Đang chờ / đang tạo…"), True),
            None, [create_btn, creating], queue=False, show_progress="hidden", trigger_mode="once",
        ).then(on_create, [title, style, dit_dd, lm_dd, lora_dd, lora_scale, duration, seed, variants]
               + generation_options,
               [status, feed], show_progress="hidden", concurrency_limit=1)
        def finish_create():
            try:
                ready = generation_ready(api.models(), api.engine())
            except ApiError:
                ready = False
            return gr.update(interactive=ready, value="✦ Tạo nhạc" if ready else "Đang chuyển/nạp model…"), False

        create_event.then(finish_create,
                          None, [create_btn, creating], queue=False, show_progress="hidden")

        # ================= Sự kiện: Thư viện =================
        lib_inputs = [lib_filter, lib_query]
        lib_tab.select(library_items, lib_inputs, lib, show_progress="hidden")
        lib_filter.change(library_items, lib_inputs, lib, show_progress="hidden")
        lib_query.submit(library_items, lib_inputs, lib, show_progress="hidden")

        # ================= Sự kiện: Gán nhãn =================
        fields = [cap_box, tags_box, t_bpm, t_key, t_ts, label_instrumental, label_lyrics, label_language]
        label_refresh_out = [track_dd, review_summary, approved_tracks, label_msg]
        label_availability_signature = gr.State("")
        demo.load(on_review_refresh, outputs=label_refresh_out)
        track_dd.change(lambda value: value, track_dd, selected_review_track, show_progress="hidden")
        approved_tracks.change(lambda value: value, approved_tracks, checked_dataset_tracks, show_progress="hidden")
        gr.Timer(3).tick(refresh_label_availability, [selected_review_track, checked_dataset_tracks, label_availability_signature],
                         [track_dd, review_summary, approved_tracks, label_availability_signature], show_progress="hidden")
        refresh_tr.click(on_review_refresh, track_dd, label_refresh_out)
        automatic_labels.change(lambda automatic: gr.update(value="Thêm nhạc & tự gán nhãn" if automatic else "Thêm nhạc"),
                                 automatic_labels, upload_btn, show_progress="hidden")
        upload_btn.click(on_upload_and_label, [uploads, automatic_labels, upload_instrumental, upload_label_jobs],
                          label_refresh_out + [upload_label_jobs, uploads], concurrency_id="label-upload-state", concurrency_limit=1)
        gr.Timer(3).tick(poll_upload_labels, [upload_label_jobs, selected_review_track, *fields],
                         [upload_label_jobs, upload_ai_status, *fields], show_progress="hidden",
                         concurrency_id="label-upload-state", concurrency_limit=1)
        track_dd.change(on_review_track, track_dd, [track_audio, *fields[:5], track_info, *fields[5:]])
        track_dd.change(on_audio_validation_note, track_dd, validation_note, show_progress="hidden")
        gr.Timer(3).tick(on_audio_validation_note, selected_review_track, validation_note, show_progress="hidden")
        label_instrumental.change(lambda value: gr.update(visible=not value), label_instrumental, label_vocals,
                                  show_progress="hidden")
        ai_event = ai_label_btn.click(lambda: gr.update(interactive=False), outputs=ai_label_btn,
                                       queue=False, show_progress="hidden", trigger_mode="once").then(
            on_review_auto_label, [track_dd, label_instrumental, label_lyrics, label_language, transcribe],
            [ai_label_status, ai_suggestion], show_progress="hidden", concurrency_limit=1)
        ai_event.then(apply_review_ai_labels, [ai_suggestion, track_dd, *fields], fields,
                      show_progress="hidden").then(lambda: gr.update(interactive=True), outputs=ai_label_btn,
                                                   queue=False, show_progress="hidden")
        approve_btn.click(on_review_save, [track_dd, *fields], label_refresh_out)
        reject_btn.click(lambda *args: on_review_save(*args, exclude=True), [track_dd, *fields], label_refresh_out)
        track_dd.change(lambda: False, outputs=track_delete_confirm, show_progress="hidden")
        track_delete_btn.click(on_delete_uploaded_track, [track_dd, track_delete_confirm], label_refresh_out + [track_delete_confirm])
        demo.load(lambda: (datasets_rows(), gr.update(choices=dataset_choices()), gr.update(choices=existing_dataset_choices())),
                   outputs=[ds_df, ds_pick, ds_existing])
        ds_mode.change(lambda mode: (gr.update(visible=mode == "new"), gr.update(visible=mode == "append")),
                        ds_mode, [ds_name, ds_existing_group], show_progress="hidden")
        ds_existing_refresh.click(lambda: gr.update(choices=existing_dataset_choices(), value=None), outputs=ds_existing)
        ds_btn.click(on_dataset_to_finetune, [ds_name, approved_tracks, ds_mode, ds_existing],
                     [ds_df, ds_pick, ds_msg, ft_ds, ft_style, tabs, navigation, ft_msg]).then(
            lambda: gr.update(choices=existing_dataset_choices(), value=None), outputs=ds_existing)
        ds_pick.change(lambda: (None, False), outputs=[ds_file, ds_delete_confirm], show_progress="hidden")
        ds_download_btn.click(on_download_dataset, ds_pick, ds_file)
        ds_delete_btn.click(on_delete_dataset, [ds_pick, ds_delete_confirm, ft_ds],
                            [ds_df, ds_pick, ds_existing, ds_file, ds_msg, ft_ds, ds_delete_confirm])

        # ================= Sự kiện: Finetune =================
        ft_load_out = [ft_ds, ft_base, lora_df, ft_msg]
        demo.load(on_load_finetune, outputs=ft_load_out)
        ft_refresh.click(on_load_finetune, outputs=ft_load_out)
        ft_btn.click(on_train, [ft_ds, ft_base, ft_style, ft_name, ft_rank, ft_lr, ft_epochs],
                     [ft_status, ft_log, lora_df])

        # ================= Sự kiện: Hệ thống =================
        demo.load(update_notice_md, outputs=update_notice)
        gr.Timer(3600).tick(update_notice_md, outputs=update_notice, show_progress="hidden")

        sys_out = [sys_md, queue_df, jobs_df, models_json]
        demo.load(on_system, outputs=sys_out)
        sys_btn.click(on_system, outputs=sys_out)
        gr.Timer(5).tick(on_system, outputs=sys_out, show_progress="hidden")
    return demo


def main() -> None:
    demo = build_ui()
    demo.queue(default_concurrency_limit=4)
    demo.launch(server_name=settings.ui_host, server_port=settings.ui_port,
                allowed_paths=[str(api.cache_dir.resolve())], theme=theme.build_theme(), css=theme.CSS,
                js=theme.FORCE_DARK_JS)


if __name__ == "__main__":
    main()
