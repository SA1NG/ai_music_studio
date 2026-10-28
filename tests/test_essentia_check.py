import json
import os
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from shared.config import Settings
from shared.essentia_check import DEFAULT_EXECUTABLE, analyze_audio, compare_labels, musical_key, verify_labels
from .test_auto_label_save import complete_label
from ui import app


def measurements(**changes):
    return dict(bpm=120, beat_count=50, beat_interval_cv=.01, onset_rate=2,
                key_scale='C major', key_strength=.75, key_profile_votes=3, **changes)


def test_agreement_uses_audio_values_and_keeps_original_lm_evidence():
    lm = dict(source='ace', caption='piano', bpm=119, key_scale='C Major', time_signature='4', is_instrumental=True)
    labels = compare_labels(lm, measurements())
    assert labels['bpm'] == 120 and labels['bpm_source'] == 'essentia'
    assert labels['key_scale_source'] == 'essentia' and labels['time_signature_source'] == 'ace_unverified'
    assert labels['audio_validation']['status'] == 'verified'
    assert labels['audio_validation']['lm']['bpm'] == 119
    assert lm['bpm'] == 119  # The original LM result is not mutated.
    assert musical_key('G# Minor') == musical_key('Ab minor')
    assert musical_key('Am') == musical_key('A minor')


def test_disagreement_and_uncertainty_are_not_silently_accepted():
    labels = compare_labels({'bpm': 60, 'key_scale': 'D minor'}, measurements())
    assert labels['audio_validation']['status'] == 'needs_review'
    assert len(labels['audio_validation']['warnings']) == 2
    weak = measurements()
    weak.update(beat_interval_cv=.4, key_profile_votes=1)
    labels = compare_labels({'bpm': 80, 'key_scale': 'D minor'}, weak)
    assert labels['bpm'] == 80 and labels['key_scale'] == 'D minor'
    assert labels['audio_validation']['status'] == 'needs_review'


@pytest.mark.parametrize('lm_bpm,expected', [(115, 'verified'), (114, 'verified'), (113.9, 'needs_review')])
def test_small_bpm_differences_are_accepted(lm_bpm, expected):
    result = compare_labels({'bpm': lm_bpm, 'key_scale': 'C major'}, measurements())
    assert result['audio_validation']['status'] == expected
    if expected == 'verified':
        assert result['audio_validation']['notes']


def test_relative_major_minor_is_accepted_without_changing_lm_key():
    result = compare_labels({'bpm': 120, 'key_scale': 'A minor'}, measurements())
    assert result['audio_validation']['status'] == 'verified'
    assert result['key_scale'] == 'A minor'
    assert result['audio_validation']['measured']['key_scale'] == 'C major'
    assert result['audio_validation']['notes']


def test_missing_extractor_is_explicitly_unverified(tmp_path):
    labels = verify_labels(tmp_path / 'a.wav', {'caption': 'piano', 'bpm': 80},
                           Settings(essentia_executable=str(tmp_path / 'missing.exe')))
    assert labels['bpm'] == 80 and labels['audio_validation']['status'] == 'unavailable'


def test_review_gate_and_audit_survive_dataset_export(client, worker):
    labels = compare_labels(dict(source='ace', caption='piano', bpm=80, key_scale='D minor', is_instrumental=True), measurements())
    track, result = complete_label(client, worker, labels=labels)
    assert track['caption'] == 'piano' and track['bpm'] == 120
    assert track['review_status'] == 'pending' and result['dataset_ready'] is False
    assert client.post('/datasets', json={'name': 'blocked', 'track_ids': [track['id']]}).status_code == 400
    with patch.object(app.api, 'track', return_value=track):
        note = app.on_audio_validation_note(track['id'])
        assert '| BPM | 80 | 120 |' in note and 'Essentia' in note
    client.put(f"/tracks/{track['id']}", json={'review_status': 'approved'})
    ds = client.post('/datasets', json={'name': 'reviewed', 'track_ids': [track['id']]}).json()
    folder = client.app.state.storage.resolve(ds['export_path'])
    meta = json.loads((folder / '0001_a.json').read_text(encoding='utf-8'))
    assert meta['audio_validation']['admin_accepted'] is True
    assert meta['audio_validation']['lm']['bpm'] == 80
    agreed = compare_labels(dict(source='ace', caption='piano', bpm=120, key_scale='C major', is_instrumental=True), measurements())
    _, result = complete_label(client, worker, labels=agreed)
    assert result['dataset_ready'] is True


@pytest.mark.skipif(os.name != 'nt' or not DEFAULT_EXECUTABLE.is_file(), reason='Official Windows extractor is not installed')
def test_actual_extractor_on_known_tempo_and_chord(tmp_path):
    sr = 44100
    t = np.arange(sr * 25) / sr
    audio = .12 * sum(np.sin(2*np.pi*frequency*t) for frequency in (261.6256, 329.6276, 391.9954))
    rng = np.random.default_rng(42)
    for start in np.arange(0, 25, .5):
        i = int(start*sr)
        n = min(2205, len(audio)-i)
        audio[i:i+n] += .5*rng.standard_normal(n)*np.exp(-np.arange(n)/300)
    path = tmp_path / 'clicks.wav'
    sf.write(path, audio, sr)
    measured = analyze_audio(path)
    assert abs(measured['bpm'] - 120) < 2
    assert musical_key(measured['key_scale']) == musical_key('C major')
    assert measured['key_profile_votes'] >= 2
    assert compare_labels({'bpm': 120, 'key_scale': 'C major'}, measured)['audio_validation']['status'] == 'verified'
