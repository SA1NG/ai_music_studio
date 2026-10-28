from __future__ import annotations

import struct

import numpy as np

from shared.audio import wav_duration, wav_info

from .conftest import make_wav_bytes


def float_wav_bytes(seconds: float, rate: int = 48000, channels: int = 2) -> bytes:
    """WAV IEEE float 32-bit (format 3) — định dạng ACE-Step xuất ra; module wave chuẩn không đọc được."""
    data = np.zeros(int(seconds * rate) * channels, dtype="<f4").tobytes()
    block = channels * 4
    fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * block, block, 32)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"LIST" + struct.pack("<I", 4) + b"INFO"  # chunk phụ trước data
            + b"data" + struct.pack("<I", len(data)) + data)


def test_float_wav_duration(tmp_path):
    p = tmp_path / "f.wav"
    p.write_bytes(float_wav_bytes(2.5))
    info = wav_info(p)
    assert info["format"] == 3 and info["channels"] == 2 and info["rate"] == 48000 and info["bits"] == 32
    assert wav_duration(p) == 2.5


def test_pcm_wav_and_garbage(tmp_path):
    p = tmp_path / "p.wav"
    p.write_bytes(make_wav_bytes(1.5))
    assert wav_duration(p) == 1.5 and wav_info(p)["format"] == 1
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not a wav")
    assert wav_duration(bad) is None and wav_info(bad) is None
