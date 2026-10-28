"""Giao diện kiểu Suno: nền tối, khung Create bên trái, danh sách bài dạng thẻ bên phải,
thanh phát nhạc cố định ở đáy. Chỉ là theme/CSS/HTML; logic nằm ở app.py.
Chỉ nhắm tới màn hình máy tính (không có CSS cho điện thoại)."""
from __future__ import annotations

import hashlib
import html
from pathlib import Path
from typing import Any

import gradio as gr

ACCENT_A, ACCENT_B = "#ff4f8b", "#ff8a3d"

# Luôn bật chế độ tối (Gradio đọc tham số ?__theme=dark).
FORCE_DARK_JS = """
() => {
  const url = new URL(window.location);
  if (url.searchParams.get('__theme') !== 'dark') {
    url.searchParams.set('__theme', 'dark');
    window.location.replace(url.href);
  }
  if (!window.musicStudioKeyboard) {
    window.musicStudioKeyboard = true;
    document.addEventListener('keydown', (event) => {
      if (event.ctrlKey && event.key === 'Enter' && event.target.matches('.prompt-input textarea')) {
        event.preventDefault();
        const button = document.querySelector('button.create-action, .create-action button');
        if (button && !button.disabled) button.click();
      }
    });
  }
  const sidebar = document.querySelector('.studio-sidebar');
  if (sidebar && !window.musicStudioNavigationObserver) {
    const updateNavigationHeight = () => {
      document.documentElement.style.setProperty('--studio-navigation-height', `${sidebar.getBoundingClientRect().height}px`);
    };
    window.musicStudioNavigationObserver = new ResizeObserver(updateNavigationHeight);
    window.musicStudioNavigationObserver.observe(sidebar);
    updateNavigationHeight();
  }
}
"""


def build_theme() -> gr.themes.Base:
    return gr.themes.Base(
        primary_hue=gr.themes.colors.pink,
        neutral_hue=gr.themes.colors.zinc,
        radius_size=gr.themes.sizes.radius_lg,
        font=["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
    ).set(
        body_background_fill_dark="#0b0b0f",
        background_fill_primary_dark="#141418",
        background_fill_secondary_dark="#101014",
        block_background_fill_dark="#141418",
        block_border_color_dark="#24242c",
        block_label_background_fill_dark="transparent",
        block_label_text_color_dark="#a1a1aa",
        block_title_text_color_dark="#a1a1aa",
        border_color_primary_dark="#24242c",
        panel_background_fill_dark="#141418",
        input_background_fill_dark="#1c1c22",
        input_background_fill_focus_dark="#22222a",
        body_text_color_dark="#f4f4f5",
        body_text_color_subdued_dark="#a1a1aa",
        checkbox_label_background_fill_dark="#1c1c22",
        checkbox_label_background_fill_selected_dark="#3a1a2a",
        button_primary_background_fill_dark=f"linear-gradient(90deg, {ACCENT_A}, {ACCENT_B})",
        button_primary_background_fill_hover_dark="linear-gradient(90deg, #ff6a9e, #ffa05c)",
        button_primary_text_color_dark="#ffffff",
        button_secondary_background_fill_dark="#1c1c22",
        button_secondary_background_fill_hover_dark="#26262e",
        button_secondary_text_color_dark="#e4e4e7",
        table_even_background_fill_dark="#141418",
        table_odd_background_fill_dark="#18181d",
        table_border_color_dark="#24242c",
    )


CSS = f"""
:root {{ --accent-a: {ACCENT_A}; --accent-b: {ACCENT_B}; --muted: #a1a1aa; --line: #24242c; }}
/* width 100%: .gradio-container là flex item, margin auto một mình sẽ làm nó co theo nội dung (vỡ mobile) */
.gradio-container {{ width: 100% !important; max-width: 1680px !important; min-width: 0 !important;
  margin: 0 auto !important; }}
footer {{ display: none !important; }}

/* ---------- persistent left navigation and workspace ---------- */
.studio-shell {{ gap: 28px !important; flex-wrap: nowrap !important; align-items: flex-start !important; }}
.studio-sidebar {{ flex: 0 0 210px !important; width: 210px !important; min-width: 210px !important;
  position: sticky !important; top: 20px; padding: 18px 14px !important; gap: 20px !important;
  min-height: calc(100vh - 220px); border-right: 1px solid var(--line); }}
.studio-workspace {{ min-width: 0 !important; padding: 16px 8px 0 0 !important; }}
.sidebar-caption p {{ font-size: 10px !important; letter-spacing: .12em; color: #71717a !important; font-weight: 700; }}
.sidebar-navigation {{ border: none !important; background: transparent !important; padding: 0 !important; }}
.sidebar-navigation .wrap {{ display: flex !important; flex-direction: column !important; gap: 8px !important;
  background: transparent !important; }}
.sidebar-navigation label {{ box-sizing: border-box; width: 100%; margin: 0 !important; padding: 13px 14px !important;
  border: 1px solid transparent !important; border-radius: 12px !important; background: transparent !important;
  color: var(--muted) !important; font-size: 14px !important; font-weight: 600; cursor: pointer; }}
.sidebar-navigation label:hover {{ background: #1c1c22 !important; color: #fff !important; }}
.sidebar-navigation label.selected, .sidebar-navigation label:has(input:checked) {{
  background: linear-gradient(100deg, rgba(255,79,139,.16), rgba(255,138,61,.06)) !important;
  border-color: rgba(255,79,139,.25) !important; color: #ff9dbe !important; }}
.sidebar-navigation input {{ position: absolute; opacity: 0; width: 1px; height: 1px; }}
.sidebar-navigation label:focus-within {{ outline: 2px solid var(--accent-a); outline-offset: 3px; }}
.sidebar-note {{ margin-top: auto; padding: 18px 4px 0 !important; border-top: 1px solid var(--line); }}
.sidebar-note p {{ color: #71717a !important; font-size: 12px !important; }}
.workspace-tabs > .tab-wrapper, .workspace-tabs > .tab-container {{ display: none !important; }}
.workspace-tabs, .workspace-tabs > .tabitem {{ border: none !important; padding: 0 !important; background: transparent !important; }}
.page-heading {{ margin-bottom: 20px !important; }}
.page-heading h2 {{ font-size: 28px !important; letter-spacing: -.035em; margin: 0 0 6px !important; }}
.page-heading p {{ color: var(--muted) !important; font-size: 13px !important; }}
.model-picker {{ border: 1px solid var(--line) !important; border-radius: 14px !important; background: #141418 !important; }}
.model-picker > button {{ padding: 16px !important; font-weight: 650 !important; }}
.model-options .wrap {{ display: flex !important; flex-direction: column !important; gap: 8px !important; }}
.model-options label {{ width: 100%; box-sizing: border-box; padding: 12px !important; border-radius: 10px !important; }}
@media (max-width: 1000px) {{
  .studio-shell {{ gap: 14px !important; }}
  .studio-sidebar {{ flex-basis: 170px !important; width: 170px !important; min-width: 170px !important; padding: 12px 8px !important; }}
  .studio-sidebar .brand b {{ font-size: 16px; }}
  .studio-sidebar .brand span.sub {{ display: none; }}
}}
.brand {{ display:flex; flex-wrap: wrap; align-items:center; gap:10px; padding: 6px 2px 2px; min-width: 0; }}
.brand .logo {{ width:34px; height:34px; border-radius:10px; display:grid; place-items:center; font-size:18px;
  background: linear-gradient(135deg, var(--accent-a), var(--accent-b)); color:#fff; }}
.brand b {{ font-size: 20px; letter-spacing: -0.02em; }}
.brand span.sub {{ color: var(--muted); font-size: 13px; }}

/* ---------- khung Create ---------- */
.create-panel {{ background: #121216; border: 1px solid var(--line); border-radius: 18px; padding: 14px !important; gap: 12px !important; }}
.seg {{ background: transparent !important; border: none !important; padding: 0 !important; }}
.seg .wrap {{ background:#1c1c22; border-radius: 999px; padding: 4px; display:inline-flex !important; gap: 4px; width: fit-content; }}
.seg label {{ border-radius: 999px !important; border: none !important; background: transparent !important; padding: 6px 16px !important; }}
.seg label.selected {{ background: #2a2a33 !important; color: #fff !important; }}
.seg input {{ display: none; }}
.pills {{ background: transparent !important; border: none !important; padding: 0 !important; }}
.pills .wrap {{ gap: 6px !important; }}
.pills label {{ border-radius: 999px !important; padding: 4px 12px !important; font-size: 13px !important;
  border: 1px solid var(--line) !important; background: #18181d !important; }}
.pills label.selected {{ border-color: var(--accent-a) !important; background: rgba(255,79,139,.14) !important; color: #fff !important; }}
.pills input {{ display: none; }}
.badge {{ display:inline-flex; align-items:center; gap:6px; font-size:13px; color:#e4e4e7; background:#1c1c22;
  border:1px solid var(--line); border-radius:999px; padding:4px 12px; }}
.badge i {{ width:8px; height:8px; border-radius:50%; background: linear-gradient(135deg, var(--accent-a), var(--accent-b)); }}
.create-btn {{ font-size: 16px !important; font-weight: 700 !important; padding: 12px !important; border-radius: 999px !important;
  box-shadow: 0 8px 24px rgba(255,79,139,.25); }}
.status {{ font-size: 13px; color: var(--muted); min-height: 0 !important; }}

/* ---------- danh sách bài (feed) ---------- */
.feed-head {{ align-items: center !important; }}
.feed-head h3 {{ margin: 0 !important; }}
.song-card {{ background: #121216; border: 1px solid transparent; border-radius: 14px; padding: 8px 10px !important;
  align-items: center !important; gap: 12px !important; flex-wrap: nowrap !important; transition: background .15s, border-color .15s; }}
.song-card:hover {{ background: #18181e; border-color: var(--line); }}
.song-card.playing {{ border-color: var(--accent-a); }}
.song-card > div {{ min-width: 0 !important; }}
.song-card > .cover-cell {{ flex: 0 0 56px !important; width: 56px !important; min-width: 56px !important; }}
.song-card > .info-cell {{ flex: 1 1 auto !important; }}
.song-card .html-container, .song-card .prose {{ padding: 0 !important; }}
.cover {{ width: 56px; height: 56px; border-radius: 10px; display:grid; place-items:center; flex: none;
  color: rgba(255,255,255,.85); font-size: 22px; box-shadow: inset 0 0 0 1px rgba(255,255,255,.08); }}
.cover.big {{ width: 44px; height: 44px; font-size: 18px; }}
.cover.pending {{ background: linear-gradient(110deg, #1c1c22 30%, #2a2a33 50%, #1c1c22 70%) !important;
  background-size: 200% 100% !important; animation: shimmer 1.4s linear infinite; }}
@keyframes shimmer {{ to {{ background-position: -200% 0; }} }}
.song-title {{ font-weight: 650; color: #fafafa; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.song-tags {{ color: #d4d4d8; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.song-meta {{ color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.song-meta .lora {{ color: var(--accent-b); }}
.progress {{ height: 4px; border-radius: 4px; background: #24242c; overflow: hidden; margin-top: 6px; }}
.progress > div {{ height: 100%; background: linear-gradient(90deg, var(--accent-a), var(--accent-b)); }}
.icon-btn {{ min-width: 36px !important; width: 36px !important; height: 36px !important; padding: 0 !important;
  border-radius: 999px !important; background: transparent !important; border: none !important; font-size: 16px !important; }}
.icon-btn:hover {{ background: #26262e !important; }}
.icon-btn.play {{ background: #fff !important; color: #000 !important; }}
.icon-btn.on {{ background: rgba(255,79,139,.18) !important; }}
.empty {{ color: var(--muted); text-align: center; padding: 40px 0; }}
.details {{ background: #121216; border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px !important; }}

/* ---------- thanh phát nhạc cố định ở đáy ---------- */
/* fixed thay vì sticky: Gradio đặt overflow:hidden lên .gradio-container nên sticky không bám được */
.gradio-container {{ padding-bottom: 200px !important; }}
.player-bar {{ position: fixed !important; left: 0; right: 0; bottom: 0; z-index: 50; margin: 0 auto !important;
  width: 100% !important; max-width: 1680px; box-sizing: border-box;
  background: rgba(11,11,15,.94); backdrop-filter: blur(12px); border: 1px solid var(--line); border-bottom: none;
  border-radius: 16px 16px 0 0; padding: 8px 12px !important; align-items: center !important; gap: 12px !important; }}
.player-bar .now {{ display:flex; align-items:center; gap:10px; min-width: 0; }}
.player-bar > .now-cell {{ flex: 0 0 300px !important; min-width: 0 !important; }}
.player-bar .now .t {{ font-weight: 650; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.player-bar .now .m {{ color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.lib-bar {{ align-items: center !important; }}
.lib-bar .seg {{ flex: 0 0 auto !important; }}
.player-bar .audio {{ background: transparent !important; border: none !important; }}

/* ---------- các tab khác ---------- */
.panel {{ background: #121216; border: 1px solid var(--line); border-radius: 18px; padding: 14px !important; }}
"""


CSS += "\n" + Path(__file__).with_name("studio.css").read_text(encoding="utf-8")


def _hue(key: str) -> tuple[int, int]:
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    a = h % 360
    return a, (a + 35 + (h >> 9) % 90) % 360


def cover_html(key: str, pending: bool = False, big: bool = False) -> str:
    """Ảnh bìa tạo từ khoá (id + seed): mỗi bài một dải màu riêng, ổn định qua các lần tải."""
    a, b = _hue(key)
    style = f"background: linear-gradient(135deg, hsl({a} 85% 58%), hsl({b} 80% 38%));"
    cls = "cover" + (" pending" if pending else "") + (" big" if big else "")
    return f'<div class="{cls}" style="{style}">{"" if pending else "♪"}</div>'


def fmt_duration(sec: Any) -> str:
    try:
        s = int(round(float(sec)))
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}:{s % 60:02d}"


def song_info_html(title: str, tags: list[str], meta: list[str], progress: float | None = None,
                   lora: str = "") -> str:
    e = html.escape
    tag_line = ", ".join(e(t) for t in tags if t)
    meta_line = " · ".join(e(m) for m in meta if m)
    if lora:
        meta_line += f' · <span class="lora">LoRA {e(lora)}</span>'
    bar = ""
    if progress is not None:
        bar = f'<div class="progress"><div style="width:{max(3, int(progress * 100))}%"></div></div>'
    return (f'<div class="song-title">{e(title)}</div><div class="song-tags">{tag_line}</div>'
            f'<div class="song-meta">{meta_line}</div>{bar}')


def now_playing_html(key: str | None = None, title: str = "", meta: str = "") -> str:
    if not key:
        return '<div class="now"><div class="cover big" style="background:#1c1c22">♪</div>' \
               '<div><div class="t">Chưa phát bài nào</div><div class="m">Bấm ▶ trên một bài để nghe</div></div></div>'
    return (f'<div class="now">{cover_html(key, big=True)}<div style="min-width:0">'
            f'<div class="t">{html.escape(title)}</div><div class="m">{html.escape(meta)}</div></div></div>')


BRAND_HTML = ('<div class="brand"><div class="logo">♪</div><b>Music Studio</b>'
              '<span class="sub">tạo nhạc theo phong cách riêng</span></div>')
INSTRUMENTAL_BADGE = '<span class="badge"><i></i>Instrumental · không lời</span>'
