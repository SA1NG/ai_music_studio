"""Independent audio measurements. Essentia agreement is evidence, not ground truth."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXECUTABLE = ROOT / 'tools/essentia/essentia_streaming_extractor_music.exe'
PROFILE = 'outputFormat: json\noutputFrames: 0\nanalysisSampleRate: 44100\nrhythm:\n  method: multifeature\nhighlevel:\n  compute: 0\n'


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def musical_key(value):
    value = str(value or '').replace('♯', '#').replace('♭', 'b').strip()
    match = re.fullmatch(r'([A-Ga-g])([#b]?)(?:\s*(major|minor|maj|min|m))?', value, flags=re.IGNORECASE)
    if not match:
        return None
    note, accidental, scale = match.groups()
    accidental, scale = accidental.lower(), (scale or 'major').lower()
    pitch = (dict(C=0, D=2, E=4, F=5, G=7, A=9, B=11)[note.upper()] + {'#': 1, 'b': -1, '': 0}[accidental]) % 12
    return pitch, 'minor' if scale in ('minor', 'min', 'm') else 'major'


def analyze_audio(path, executable=None, timeout=180):
    executable = Path(executable or DEFAULT_EXECUTABLE).resolve()
    if not executable.is_file():
        raise FileNotFoundError('Chưa có Essentia. Chạy scripts/setup_essentia.py để cài công cụ đối chiếu.')
    # ASCII filenames also work with the older official Windows extractor.
    with tempfile.TemporaryDirectory(prefix='music-essentia-') as folder:
        root = Path(folder)
        audio = root / ('input' + path.suffix.lower())
        shutil.copy2(path, audio)
        (root / 'profile.yaml').write_text(PROFILE, encoding='utf-8')
        output = root / 'analysis.json'
        result = subprocess.run([str(executable), str(audio), str(output), str(root / 'profile.yaml')],
                                capture_output=True, timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError('Essentia không phân tích được audio: ' + result.stderr.decode('utf-8', errors='replace')[-600:])
        data = json.loads(output.read_text(encoding='utf-8'))
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    rhythm, tonal = data.get('rhythm', {}), data.get('tonal', {})
    positions = [number(t) for t in rhythm.get('beats_position', [])]
    intervals = [b-a for a, b in zip(positions, positions[1:]) if a is not None and b is not None and b > a]
    variation = statistics.pstdev(intervals) / statistics.mean(intervals) if len(intervals) >= 2 else None
    profiles = []
    for name in ('key_edma', 'key_krumhansl', 'key_temperley'):
        profile = tonal.get(name, {})
        key = f"{profile.get('key', '')} {profile.get('scale', '')}".strip()
        strength = number(profile.get('strength'))
        if musical_key(key) is not None and strength is not None:
            profiles.append({'profile': name, 'key_scale': key, 'strength': strength})
    votes = Counter(musical_key(p['key_scale']) for p in profiles)
    winner, count = votes.most_common(1)[0] if votes else (None, 0)
    winning = [p for p in profiles if musical_key(p['key_scale']) == winner]
    key = max(winning, key=lambda p: p['strength']) if winning else {}
    return {'bpm': number(rhythm.get('bpm')), 'beat_count': len(positions),
            'tempo_peak_weight': number(rhythm.get('bpm_histogram_first_peak_weight')),
            'beat_interval_cv': variation, 'onset_rate': number(rhythm.get('onset_rate')),
            'key_scale': key.get('key_scale'), 'key_strength': key.get('strength'),
            'key_profile_votes': count, 'key_profiles': profiles,
            'version': data.get('metadata', {}).get('version', {}), 'audio_sha256': digest.hexdigest()}


def compare_labels(labels, measured):
    updated = dict(labels)
    lm = {key: labels.get(key) for key in ('bpm', 'key_scale', 'time_signature')}
    warnings = []
    notes = []
    bpm = number(measured.get('bpm'))
    cv = number(measured.get('beat_interval_cv'))
    onsets = number(measured.get('onset_rate'))
    bpm_reliable = (bpm is not None and 30 <= bpm <= 300 and measured.get('beat_count', 0) >= 16
                    and cv is not None and cv <= .12
                    and onsets is not None and onsets >= .2)
    strength = number(measured.get('key_strength'))
    key_reliable = (musical_key(measured.get('key_scale')) is not None and strength is not None and strength >= .6
                    and measured.get('key_profile_votes', 0) >= 2)
    if bpm_reliable:
        updated.update(bpm=round(bpm, 1), bpm_source='essentia')
        original = number(lm['bpm'])
        if original is not None and abs(original-bpm) > max(5, .05*bpm):
            factor = any(abs(original-bpm*ratio) <= max(3, .03*bpm*ratio) for ratio in (.5, 2))
            warnings.append('BPM khác LM; có thể là nhịp nửa/gấp đôi.' if factor else 'BPM đo từ audio khác LM.')
        elif original is not None and abs(original-bpm) > max(1, .01*bpm):
            notes.append('BPM chênh nhẹ, nằm trong ngưỡng chấp nhận; dùng BPM đo từ audio.')
    else:
        warnings.append('BPM chưa đủ ổn định để xác nhận; giữ giá trị LM và cần xem lại.')
    if key_reliable:
        original_key = musical_key(lm['key_scale'])
        measured_key = musical_key(measured['key_scale'])
        relative = False
        if original_key and measured_key and original_key[1] != measured_key[1]:
            major = original_key if original_key[1] == 'major' else measured_key
            minor = original_key if original_key[1] == 'minor' else measured_key
            relative = (major[0] + 9) % 12 == minor[0]
        if relative:
            updated['key_scale_source'] = 'ace_relative_key_accepted'
            notes.append('Hai kết quả là major/minor tương đối, dùng cùng bộ nốt; chấp nhận và giữ key LM, không tự đổi tâm điệu.')
        else:
            updated.update(key_scale=measured['key_scale'], key_scale_source='essentia')
        if lm['key_scale'] and original_key != measured_key and not relative:
            warnings.append('Key đo từ audio khác LM.')
    else:
        warnings.append('Key chưa đủ rõ hoặc các profile không thống nhất; cần xem lại.')
    updated['time_signature_source'] = 'ace_unverified'
    updated['audio_validation'] = {
        'status': 'needs_review' if warnings else 'verified', 'source': 'essentia',
        'lm': lm, 'measured': measured, 'warnings': warnings, 'notes': notes,
        'tolerance': {'bpm_absolute': 5, 'bpm_relative': .05, 'relative_keys': 'accept_and_keep_lm'},
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'BPM và key; không xác minh caption, lời hát hoặc nhịp.',
        'bpm_reliable': bpm_reliable, 'key_reliable': key_reliable,
    }
    return updated


def verify_labels(path, labels, settings):
    try:
        measured = analyze_audio(path, settings.essentia_executable or None, settings.essentia_timeout)
        return compare_labels(labels, measured)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {**labels, 'audio_validation': {'status': 'unavailable', 'source': 'essentia',
                'lm': {key: labels.get(key) for key in ('bpm', 'key_scale', 'time_signature')},
                'measured': {}, 'warnings': ['Chưa đối chiếu được audio: ' + str(exc)[:700]],
                'scope': 'BPM và key; nhãn LM chưa được xác minh.'}}
