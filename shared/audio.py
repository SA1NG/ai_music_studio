"""Phân tích audio nhẹ. Chỉ dùng thư viện chuẩn; librosa là tùy chọn."""
from __future__ import annotations

import struct
import wave
from pathlib import Path
from typing import Optional

try:  # tùy chọn: không có thì tắt ước lượng BPM
    import librosa  # type: ignore
    HAS_LIBROSA = True
except Exception:  # pragma: no cover - phụ thuộc môi trường
    librosa = None
    HAS_LIBROSA = False

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".opus"}


def wav_info(path: Path) -> Optional[dict]:
    """Đọc header RIFF/WAVE: {format, channels, rate, bits, duration}. Đọc được cả WAV số thực
    (format 3, IEEE float — ACE-Step xuất loại này) mà module `wave` chuẩn không hỗ trợ."""
    try:
        with open(path, "rb") as f:
            riff = f.read(12)
            if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
                return None
            fmt = None
            while True:
                head = f.read(8)
                if len(head) < 8:
                    return None
                cid, size = head[:4], struct.unpack("<I", head[4:])[0]
                if cid == b"fmt ":
                    raw = f.read(size)
                    tag, ch, rate, _, block, bits = struct.unpack("<HHIIHH", raw[:16])
                    if tag == 0xFFFE and len(raw) >= 26:  # WAVE_FORMAT_EXTENSIBLE: format thật ở SubFormat
                        tag = struct.unpack("<H", raw[24:26])[0]
                    fmt = {"format": tag, "channels": ch, "rate": rate, "bits": bits, "block": block}
                elif cid == b"data":
                    if not fmt or not fmt["rate"] or not fmt["block"]:
                        return None
                    frames = size // fmt["block"]
                    return {**fmt, "duration": round(frames / float(fmt["rate"]), 3)}
                else:
                    f.seek(size + (size & 1), 1)
                if fmt is None and f.tell() > 1 << 20:
                    return None
    except (OSError, struct.error):
        return None


def wav_duration(path: Path) -> Optional[float]:
    """Thời lượng (giây) của file WAV (PCM hoặc float); None nếu không đọc được."""
    try:
        with wave.open(str(path), "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
            return round(frames / float(rate), 3) if rate else None
    except (wave.Error, EOFError, OSError):
        info = wav_info(path)
        return info["duration"] if info else None


def audio_duration(path: Path) -> Optional[float]:
    d = wav_duration(path)
    if d is None:
        try:
            import soundfile as sf
            d = round(float(sf.info(str(path)).duration), 3)
        except Exception:
            pass
    if d is None and HAS_LIBROSA:
        try:
            d = round(float(librosa.get_duration(path=str(path))), 3)
        except Exception:
            d = None
    return d


def estimate_bpm(path: Path) -> Optional[float]:
    """Ước lượng BPM bằng librosa (nếu có). Không có librosa -> None."""
    if not HAS_LIBROSA:
        return None
    try:
        y, sr = librosa.load(str(path), sr=22050, mono=True, duration=120)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
        return round(tempo, 1) if tempo > 0 else None
    except Exception:
        return None
