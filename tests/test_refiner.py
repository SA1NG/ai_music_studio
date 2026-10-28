from __future__ import annotations

from types import SimpleNamespace

from shared.config import Settings
from prompt_refiner import NoOpRefiner, get_refiner
from prompt_refiner.claude import ClaudeRefiner, build_user_message


def test_noop_returns_original():
    res = NoOpRefiner().refine("  nhạc thiền nhẹ nhàng ", {})
    assert res == {"refined": "nhạc thiền nhẹ nhàng", "warnings": [], "suggested_params": {}}


def test_factory_default_is_noop(tmp_path):
    assert get_refiner(Settings(data_dir=tmp_path)).name == "noop"
    r = get_refiner(Settings(data_dir=tmp_path, prompt_refiner="claude", anthropic_api_key=""))
    assert r.name == "claude"


def test_claude_without_key_falls_back():
    res = ClaudeRefiner(api_key="").refine("nhạc thiền", {"bpm": 170})
    assert res["refined"] == "nhạc thiền"
    assert any("ANTHROPIC_API_KEY" in w for w in res["warnings"])


class FakeClient:
    def __init__(self, behaviour):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.behaviour = behaviour

    def _create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


def text_response(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


def test_claude_network_error_falls_back():
    r = ClaudeRefiner("k", client=FakeClient(ConnectionError("network down")))
    res = r.refine("lofi buổi tối", {})
    assert res["refined"] == "lofi buổi tối"
    assert any("network down" in w for w in res["warnings"])


def test_claude_garbage_reply_falls_back():
    res = ClaudeRefiner("k", client=FakeClient(text_response("xin lỗi"))).refine("lofi", {})
    assert res["refined"] == "lofi" and res["warnings"]


def test_claude_refusal_falls_back():
    res = ClaudeRefiner("k", client=FakeClient(text_response("", "refusal"))).refine("lofi", {})
    assert res["refined"] == "lofi" and res["warnings"]


def test_claude_parses_json_and_passes_context():
    reply = ('{"refined": "meditation ambient, singing bowls, 60 BPM, calm, instrumental, no vocals",'
             ' "warnings": ["Nhạc thiền mà BPM 170 là mâu thuẫn"], "suggested_params": {"bpm": 60, "key_scale": ""}}')
    fake = FakeClient(text_response(reply))
    res = ClaudeRefiner("k", model="claude-test", client=fake).refine(
        "nhạc thiền", {"bpm": 170, "approved_captions": ["lofi piano, rain, instrumental, no vocals"]})
    assert res["refined"].endswith("instrumental, no vocals")
    assert res["warnings"] == ["Nhạc thiền mà BPM 170 là mâu thuẫn"]
    assert res["suggested_params"] == {"bpm": 60}
    call = fake.calls[0]
    assert call["model"] == "claude-test"
    assert "instrumental, no vocals" in call["system"]
    user = call["messages"][0]["content"]
    assert "170" in user and "lofi piano, rain" in user


def test_build_user_message_skips_empty():
    msg = build_user_message("x", {"bpm": None, "key_scale": "", "approved_captions": []})
    assert msg == "Description: x"
