"""ClaudeRefiner — KHUNG, chỉ bật khi PROMPT_REFINER=claude.

Thiếu key, thiếu thư viện `anthropic`, lỗi mạng hoặc trả lời không đọc được -> lùi về NoOp
và trả warning; không bao giờ làm hỏng luồng sinh nhạc.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import PromptRefiner, RefineResult
from .noop import NoOpRefiner

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You turn a user's music description (it may be written in Vietnamese or any \
language) into a caption for the ACE-Step 1.5 text-to-music model. The music is ALWAYS instrumental.

Write the caption in English, as one line of comma-separated descriptors in this order:
genre/sub-genre, main instruments, tempo feel (and BPM if known), mood/atmosphere, production \
details, and always end with "instrumental, no vocals".

Check the description against the requested parameters and report contradictions as warnings, \
written in Vietnamese. Examples: meditation/ambient/"nhạc thiền" with BPM above ~100, "lofi" with \
BPM 170, a requested duration too short for the described structure, any request for vocals or lyrics.

If example captions approved by the user are given, match their vocabulary and style.

Answer with ONLY a JSON object, no prose, no code fences:
{"refined": "<caption>", "warnings": ["..."], "suggested_params": {"bpm": <int or null>, \
"key_scale": "<e.g. A minor or empty>"}}"""


def build_user_message(text: str, context: dict[str, Any]) -> str:
    parts = [f"Description: {text.strip()}"]
    params = {k: context[k] for k in ("bpm", "key_scale", "duration", "time_signature")
              if context.get(k) not in (None, "", 0)}
    if params:
        parts.append("Requested parameters: " + json.dumps(params, ensure_ascii=False))
    examples = [c for c in context.get("approved_captions") or [] if c][:5]
    if examples:
        parts.append("Approved example captions:\n" + "\n".join(f"- {c}" for c in examples))
    return "\n\n".join(parts)


def parse_reply(raw: str) -> RefineResult:
    m = re.search(r"\{.*\}", raw, flags=re.S)
    if not m:
        raise ValueError("không tìm thấy JSON trong câu trả lời")
    data = json.loads(m.group(0))
    refined = str(data.get("refined") or "").strip()
    if not refined:
        raise ValueError("thiếu trường refined")
    suggested = {k: v for k, v in (data.get("suggested_params") or {}).items() if v not in (None, "")}
    return {"refined": refined, "warnings": [str(w) for w in data.get("warnings") or []],
            "suggested_params": suggested}


class ClaudeRefiner(PromptRefiner):
    name = "claude"

    def __init__(self, api_key: str, model: str = "", client: Any = None):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self._client = client  # cho phép inject client giả trong test
        self._noop = NoOpRefiner()

    def _fallback(self, text: str, context: dict[str, Any], reason: str) -> RefineResult:
        res = self._noop.refine(text, context)
        res["warnings"].append(f"Claude refiner không dùng được ({reason}); dùng nguyên văn.")
        return res

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # tùy chọn: requirements-optional.txt
            self._client = anthropic.Anthropic(api_key=self.api_key, timeout=30.0, max_retries=1)
        return self._client

    def refine(self, text: str, context: dict[str, Any]) -> RefineResult:
        if not self.api_key:
            return self._fallback(text, context, "thiếu ANTHROPIC_API_KEY")
        try:
            client = self._get_client()
        except ImportError:
            return self._fallback(text, context, "chưa cài thư viện anthropic")
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=2048,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_message(text, context)}],
            )
            if getattr(response, "stop_reason", None) == "refusal":
                return self._fallback(text, context, "model từ chối yêu cầu")
            raw = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
            return parse_reply(raw)
        except Exception as e:  # lỗi mạng/API/parse: không được làm hỏng luồng sinh nhạc
            return self._fallback(text, context, f"{type(e).__name__}: {e}")
