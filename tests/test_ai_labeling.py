import json
from unittest.mock import patch

import httpx
import pytest
import respx

from shared.config import Settings
from worker.backends.acestep import AceStepBackend, AceStepClient, AceStepError
from .conftest import make_wav_bytes
from .test_mock_backend import FakeCtx
from ui import app

URL = 'http://ace-label.test'


def env(data):
    return {'data': data, 'code': 200, 'error': None}


@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('verification', [False, True])
@respx.mock
def test_real_ace_label_contract_and_failed_samples(tmp_path, failed, verification):
    settings = Settings(acestep_url=URL, acestep_work_dir=str(tmp_path / 'ace-work'), acestep_poll_interval=0,
                        essentia_enabled=verification, essentia_executable=str(tmp_path / 'missing.exe'))
    backend = AceStepBackend(settings, AceStepClient(URL), sleep=lambda _: None)
    respx.get(URL + '/health').respond(json=env({'status': 'ok'}))
    respx.get(URL + '/v1/model_inventory').respond(json=env({'llm_initialized': True}))
    scan = respx.post(URL + '/v1/dataset/scan').respond(json=env({'samples': [{'filename': 'source.wav'}]}))
    start = respx.post(URL + '/v1/dataset/auto_label_async').respond(json=env({'task_id': 'label1', 'total': 1}))
    sample = dict(labeled=not failed, caption='' if failed else 'acoustic guitar and vocals',
                  bpm=91, keyscale='D minor', timesignature='3/4', duration=12,
                  lyrics='[Verse] Hello', language='en')
    respx.get(URL + '/v1/dataset/auto_label_status/label1').mock(side_effect=[
        httpx.Response(200, json=env({'status': 'running', 'progress': 'Encoding 1/1'})),
        httpx.Response(200, json=env({'status': 'completed', 'result': {'samples': [sample], 'message': 'label failed' if failed else 'done'}})),
    ])
    source = tmp_path / 'song.wav'
    source.write_bytes(make_wav_bytes(12))
    ctx = FakeCtx(tmp_path, {'file': 'uploads/song.wav', 'is_instrumental': False, 'lyrics': '[Verse] Original'},
                  {'uploads/song.wav': source})
    if failed:
        with pytest.raises(AceStepError, match='caption'):
            backend.autolabel(ctx)
    else:
        labels = backend.autolabel(ctx).result['auto_labels']
        assert labels['source'] == 'ace' and labels['time_signature'] == '3'
        assert labels['lyrics'] == '[Verse] Hello' and labels['vocal_language'] == 'en'
        assert labels['bpm'] == 91 and labels['key_scale'] == 'D minor'
        if verification:
            assert labels['audio_validation']['status'] == 'unavailable'
    assert json.loads(scan.calls[0].request.content)['all_instrumental'] is False
    assert json.loads(start.calls[0].request.content)['transcribe_lyrics'] is True
    assert not (tmp_path / 'ace-work' / 'label_job000001').exists()


@respx.mock
def test_ace_labeling_requires_loaded_lm(tmp_path):
    backend = AceStepBackend(Settings(acestep_url=URL), AceStepClient(URL))
    respx.get(URL + '/health').respond(json=env({'status': 'ok'}))
    respx.get(URL + '/v1/model_inventory').respond(json=env({'llm_initialized': False}))
    with pytest.raises(AceStepError, match='LM'):
        backend.autolabel(FakeCtx(tmp_path, {}))


def test_ai_requests_cannot_be_served_as_mock(client, worker):
    track = client.post('/tracks?autolabel=false', files={'files': ('a.wav', make_wav_bytes())}).json()[0]
    url = f"/tracks/{track['id']}/autolabel"
    assert client.post(url, json={'require_ai': True}).status_code == 409
    registration = worker.registration(False)
    registration['backends']['autolabel'] = 'ace'
    registration['lm_models'] = [{'name': 'none'}]
    client.post('/worker/register', json=registration)
    assert client.post(url, json={'require_ai': True}).status_code == 409
    registration['lm_models'].append({'name': 'acestep-5Hz-lm-0.6B'})
    client.post('/worker/register', json=registration)
    request = client.post(url, json={'require_ai': True})
    assert request.status_code == 200
    worker.run_once()  # The backend instance is still mock despite its reported inventory.
    job = client.get(f"/jobs/{request.json()['job_id']}").json()
    assert job['status'] == 'failed' and 'ACE-Step' in job['error']
    current = client.get(f"/tracks/{track['id']}").json()
    assert current['auto_labels'] == {} and current['review_status'] == 'pending'


def test_ui_applies_only_real_suggestions_to_unchanged_track_and_empty_fields():
    labels = dict(source='ace', caption='AI caption', bpm=88, key_scale='C major', time_signature='4',
                  lyrics='Hello', vocal_language='en')
    done = dict(status='done', result={'auto_labels': labels})
    with patch.object(app.api, 'autolabel', return_value=7) as enqueue, \
         patch.object(app, 'wait_job', return_value=iter([done])):
        result = list(app.on_review_auto_label(3, False, '', 'unknown', True))
    assert enqueue.call_args.kwargs['require_ai'] is True
    suggestion = result[-1][1]
    applied = app.apply_review_ai_labels(suggestion, 3, 'My caption', '', 0, '', '', False, '', 'unknown')
    assert applied == ('My caption', '', 88, 'C major', '4', False, 'Hello', 'en')
    changed = app.apply_review_ai_labels(suggestion, 4, '', '', 0, '', '', False, '', 'unknown')
    assert all('value' not in value for value in changed)


def test_unfinished_label_task_blocks_another_gpu_job(tmp_path):
    backend = AceStepBackend(Settings())
    work = tmp_path / 'label-job'
    work.mkdir()
    (work / 'source.wav').write_bytes(b'audio still in use')
    backend._pending_label_task = ('still-running', work)
    with patch.object(backend.client, 'auto_label_status', return_value={'status': 'running'}):
        with pytest.raises(AceStepError, match='tác vụ trước'):
            backend.generate(FakeCtx(tmp_path, {}))
    assert work.exists()
    with patch.object(backend.client, 'auto_label_status', return_value={'status': 'completed'}):
        backend._guard_label_task()
    assert not work.exists() and backend._pending_label_task is None
