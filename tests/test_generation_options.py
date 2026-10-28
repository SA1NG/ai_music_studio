from unittest.mock import patch

import pytest

from shared.schemas import GenerateRequest
from worker.backends.acestep import AceStepBackend
from .test_mock_backend import FakeCtx
from .conftest import make_wav_bytes, DIT_SMALL


def test_source_upload_and_edit_validation(client, worker):
    upload = client.post('/generate/audio', files={'file': ('source.wav', make_wav_bytes(12), 'audio/wav')})
    assert upload.status_code == 200
    source = upload.json()['path']
    # Creating source audio does not put it into the labeling/training library.
    assert client.get('/tracks').json() == []
    body = dict(prompt='jazz piano', dit_model=DIT_SMALL, task_type='repaint',
                source_audio=source, repainting_start=3, repainting_end=7, bpm=90,
                instrumental=False, lyrics='[Verse]\nHello world', vocal_language='en')
    result = client.post('/generate', json=body)
    assert result.status_code == 200, result.text
    job = client.get(f"/jobs/{result.json()['job_id']}").json()
    assert job['payload']['duration'] == pytest.approx(12)
    assert job['payload']['lyrics'] == body['lyrics']
    assert job['payload']['bpm'] == 90
    assert worker.run_once()
    saved = client.get('/generations').json()[0]['params']
    assert saved['task_type'] == 'repaint' and saved['repainting_end'] == 7
    assert saved['lyrics'] == body['lyrics'] and saved['source_audio'] == source
    assert client.post('/generate', json={**body, 'repainting_end': 15}).status_code == 400
    assert client.post('/generate', json={**body, 'repainting_end': 2}).status_code == 422
    assert client.post('/generate', json={**body, 'source_audio': '../outside.wav'}).status_code == 400
    assert client.post('/generate', json={**body, 'source_audio': 'datasets/private.wav'}).status_code == 400
    assert client.post('/generate', json={**body, 'source_audio': None}).status_code == 422
    assert client.post('/generate', json={**body, 'lyrics': ''}).status_code == 422
    assert client.post('/generate/audio', files={'file': ('bad.exe', b'bad')}).status_code == 400


def test_backend_options_preserve_vocals_and_model_defaults(settings):
    backend = AceStepBackend(settings)
    req = GenerateRequest(prompt='piano', dit_model='acestep-v15-base', instrumental=False,
                          lyrics='[Chorus]\nHello', vocal_language='en', lm_model='lm',
                          thinking=False, use_cot_caption=True, lm_temperature=0.5,
                          lm_negative_prompt='distorted vocals', bpm=80, key_scale='D minor', time_signature='3')
    body = backend.build_release_body(req.model_dump())
    assert body['lyrics'] == req.lyrics and body['vocal_language'] == 'en'
    assert body['inference_steps'] == 32
    assert body['thinking'] is False and body['use_cot_caption'] is True
    assert body['time_signature'] == '3' and body['lm_temperature'] == 0.5
    assert body['lm_negative_prompt'] == 'distorted vocals'
    with pytest.raises(ValueError, match='Turbo'):
        GenerateRequest(prompt='piano', dit_model=DIT_SMALL, inference_steps=32)


def test_ui_passes_controls_into_job_and_uploads_once_for_variants():
    from ui import app
    jobs = []
    def generate(body):
        jobs.append(body)
        return len(jobs)
    with patch.object(app.api, 'generate', side_effect=generate), \
         patch.object(app.api, 'upload_generation_audio', return_value={'path': 'uploads/source.wav'}) as upload, \
         patch.object(app.api, 'job', return_value={'status': 'done'}), \
         patch.object(app, 'feed_items', return_value=[]):
        list(app.on_create('Song', 'jazz', DIT_SMALL, 'none', 0, 1, 180, 10, 2, task='cover', source='local.wav', bpm=90,
                           instrumental=False, lyrics='Hello', language='en', cover_strength=0.4))
    upload.assert_called_once_with('local.wav')
    assert len(jobs) == 2 and [j['seed'] for j in jobs] == [10, 11]
    assert all(j['source_audio'] == 'uploads/source.wav' and j['bpm'] == 90
               and j['lyrics'] == 'Hello' and j['audio_cover_strength'] == 0.4 for j in jobs)


def test_worker_downloads_edit_inputs_before_engine_call(settings, tmp_path):
    backend = AceStepBackend(settings)
    source = tmp_path / 'source.wav'
    source.write_bytes(make_wav_bytes(12))
    request = GenerateRequest(prompt='piano', dit_model=DIT_SMALL, task_type='repaint',
                              source_audio='uploads/source.wav', repainting_start=2, repainting_end=5)
    ctx = FakeCtx(tmp_path, request.model_dump(), {'uploads/source.wav': source})
    with patch.object(backend, 'status', return_value=None), \
         patch.object(backend, '_apply_lora_state', return_value={}), \
         patch.object(backend.client, 'release_task', side_effect=RuntimeError('stop before inference')) as release:
        with pytest.raises(RuntimeError, match='stop before inference'):
            backend.generate(ctx)
    body = release.call_args.args[0]
    assert body['src_audio_path'] == str(source.resolve())
    assert body['chunk_mask_mode'] == 'explicit'
    assert body['repainting_start'] == 2 and body['repainting_end'] == 5


def test_flac_source_duration_without_librosa(client, worker, monkeypatch):
    import io
    import numpy as np
    import soundfile as sf
    import shared.audio
    monkeypatch.setattr(shared.audio, 'HAS_LIBROSA', False)
    data = io.BytesIO()
    sf.write(data, np.zeros(8000 * 12), 8000, format='FLAC')
    upload = client.post('/generate/audio', files={'file': ('source.flac', data.getvalue())})
    assert upload.status_code == 200 and upload.json()['duration'] == 12
    response = client.post('/generate', json=dict(prompt='piano', dit_model=DIT_SMALL,
                                                task_type='cover', source_audio=upload.json()['path']))
    assert response.status_code == 200, response.text
