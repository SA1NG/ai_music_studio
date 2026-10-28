"""Tổng hợp nhạc đơn giản bằng numpy + module `wave` (cho MockBackend).
Seed quyết định vòng hợp âm, giai điệu, âm sắc -> cùng seed = cùng file, khác seed = khác nhạc."""
from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 22050
NOTE_INDEX = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]
# Bậc hợp âm (0-based) theo giọng
PROGRESSIONS = {
    "major": [[0, 4, 5, 3], [0, 5, 3, 4], [0, 3, 4, 3], [0, 3, 5, 4], [5, 3, 0, 4]],
    "minor": [[0, 5, 2, 6], [0, 3, 4, 0], [0, 6, 5, 6], [0, 3, 6, 2], [0, 5, 3, 4]],
}


def parse_key(key_scale: str) -> tuple[int, str]:
    """'F# minor' / 'Bb major' / 'Am' / '' -> (pitch class gốc, 'major'|'minor')."""
    m = re.match(r"^\s*([A-Ga-g])([#b♯♭]?)\s*(.*)$", key_scale or "")
    if not m:
        return 0, "major"
    root = NOTE_INDEX[m.group(1).upper()] + {"#": 1, "♯": 1, "b": -1, "♭": -1}.get(m.group(2), 0)
    rest = m.group(3).strip().lower()
    mode = "minor" if rest.startswith("min") or rest == "m" else "major"
    return root % 12, mode


def beats_per_bar(time_signature: str) -> int:
    try:
        n = int(str(time_signature).split("/")[0])
        return n if 2 <= n <= 7 else 4
    except ValueError:
        return 4


def midi_to_hz(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def _tone(freq: float, n: int, harmonics: list[float]) -> np.ndarray:
    t = np.arange(n) / SAMPLE_RATE
    out = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        out += amp * np.sin(2 * np.pi * freq * i * t)
    return out


def synthesize(duration: float, bpm: int, key_scale: str, time_signature: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    root, mode = parse_key(key_scale)
    scale = MAJOR if mode == "major" else MINOR
    prog = PROGRESSIONS[mode][rng.integers(len(PROGRESSIONS[mode]))]
    bpb = beats_per_bar(time_signature)
    beat_n = int(SAMPLE_RATE * 60.0 / bpm)
    total = int(SAMPLE_RATE * duration)
    pad_timbre = [1.0, rng.uniform(0.1, 0.5), rng.uniform(0.0, 0.3)]
    lead_timbre = [1.0, rng.uniform(0.2, 0.6), rng.uniform(0.1, 0.4), rng.uniform(0.0, 0.2)]
    base = 48 + root  # C3 + gốc

    def degree_note(deg: int, octave: int = 0) -> int:
        return base + scale[deg % 7] + 12 * (deg // 7 + octave)

    bar_n = beat_n * bpb
    # Dư 2 ô nhịp: nốt cuối của ô nhịp cuối có thể kéo dài quá ô nhịp đó (cắt về `total` ở dưới).
    out = np.zeros(total + 2 * bar_n)
    bar_idx = 0
    pos = 0
    melody_density = rng.uniform(0.45, 0.85)
    while pos < total:
        deg = prog[bar_idx % len(prog)]
        # Pad: hợp âm ba, envelope mềm cả ô nhịp
        env = np.minimum(1.0, np.linspace(0, 6, bar_n)) * np.linspace(1.0, 0.7, bar_n)
        for d in (deg, deg + 2, deg + 4):
            out[pos:pos + bar_n] += 0.12 * env * _tone(midi_to_hz(degree_note(d, 1)), bar_n, pad_timbre)
        # Bass: nốt gốc mỗi phách
        for b in range(bpb):
            s = pos + b * beat_n
            decay = np.exp(-np.arange(beat_n) / (beat_n * 0.5))
            out[s:s + beat_n] += 0.22 * decay * _tone(midi_to_hz(degree_note(deg, -1)), beat_n, [1.0, 0.3])
        # Kick nhẹ ở phách 1 (và giữa ô nhịp nếu chẵn)
        for b in ([0, bpb // 2] if bpb % 2 == 0 else [0]):
            s = pos + b * beat_n
            n = min(beat_n, int(0.15 * SAMPLE_RATE))
            t = np.arange(n) / SAMPLE_RATE
            out[s:s + n] += 0.35 * np.exp(-t * 30) * np.sin(2 * np.pi * (50 + 80 * np.exp(-t * 40)) * t)
        # Giai điệu: nốt móc đơn theo thang âm, ưu tiên nốt thuộc hợp âm
        half = beat_n // 2
        for k in range(bpb * 2):
            if rng.random() > melody_density:
                continue
            choice = deg + int(rng.choice([0, 2, 4, 1, 3, 5, 7], p=[.22, .2, .2, .1, .1, .08, .1]))
            n = half * int(rng.choice([1, 2]))
            s = pos + k * half
            decay = np.exp(-np.arange(n) / (n * 0.35))
            out[s:s + n] += 0.16 * decay * _tone(midi_to_hz(degree_note(choice, 2)), n, lead_timbre)
        pos += bar_n
        bar_idx += 1

    out = out[:total]
    fade = min(total // 4, int(SAMPLE_RATE * 1.5))
    if fade > 0:
        out[:fade] *= np.linspace(0, 1, fade)
        out[-fade:] *= np.linspace(1, 0, fade)
    peak = np.max(np.abs(out)) or 1.0
    return out / peak * 0.85


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
