from unittest.mock import patch

from ui import app
from .conftest import make_wav_bytes


def register_ai_worker(client, worker):
    registration = worker.registration(False)
    registration['backends']['autolabel'] = 'ace'
    registration['lm_models'] = [{'name': 'none'}, {'name': 'acestep-5Hz-lm-0.6B'}]
    client.post('/worker/register', json=registration)


def test_upload_preflights_ai_and_enqueues_one_real_job_per_track(client, worker):
    params = dict(autolabel='true', require_ai='true', is_instrumental='false')
    files = [('files', ('a.wav', make_wav_bytes())), ('files', ('b.wav', make_wav_bytes()))]
    rejected = client.post('/tracks', params=params, files=files)
    assert rejected.status_code == 409
    assert client.get('/tracks').json() == [] and client.get('/jobs').json() == []
    register_ai_worker(client, worker)
    response = client.post('/tracks', params=params, files=files)
    assert response.status_code == 200, response.text
    tracks = response.json()
    assert len(tracks) == 2 and all(t['autolabel_job_id'] for t in tracks)
    jobs = client.get('/jobs').json()
    assert {j['id'] for j in jobs} == {t['autolabel_job_id'] for t in tracks}
    assert all(j['payload']['require_ai'] and j['payload']['transcribe_lyrics'] for j in jobs)
    assert all(not t['is_instrumental'] and t['review_status'] == 'pending' and not t['caption'] for t in tracks)


def test_manual_upload_still_works_without_loaded_engine(client):
    client.app.state.engine.state.update(managed=True, status='idle')
    response = client.post('/tracks?autolabel=false&is_instrumental=false', files={'files': ('song.wav', make_wav_bytes())})
    assert response.status_code == 200
    assert response.json()[0]['is_instrumental'] is False
    assert response.json()[0]['autolabel_job_id'] is None
    assert client.get('/jobs').json() == []


def test_batch_poll_fills_only_current_track_preserves_edits_and_does_not_repeat():
    pending = [{'job_id': 1, 'track_id': 10}, {'job_id': 2, 'track_id': 11}]
    labels = {'source': 'ace', 'caption': 'AI caption', 'bpm': 80, 'key_scale': 'D minor', 'time_signature': '4', 'is_instrumental': True}
    jobs = {1: {'status': 'done', 'result': {'auto_labels': labels}}, 2: {'status': 'queued'}}
    fields = ('My caption', '', 0, '', '', True, '', 'unknown')
    with patch.object(app.api, 'job', side_effect=lambda key: jobs[key]):
        update = app.poll_upload_labels(pending, 10, *fields)
        assert update[2] == 'My caption' and update[4] == 80
        assert update[0][0]['applied'] is True
        again = app.poll_upload_labels(update[0], 10, *fields)
        assert all('value' not in value for value in again[2:])
        jobs[2] = {'status': 'failed', 'error': 'GPU error'}
        complete = app.poll_upload_labels(update[0], 11, *fields)
        assert complete[0] == [] and 'GPU error' in complete[1]
        assert all('value' not in value for value in complete[2:])


def test_opening_track_restores_ready_suggestions_without_approving():
    track = dict(path='uploads/a.wav', filename='a.wav', duration=12, caption='', tags=[], bpm=None,
                 key_scale='', time_signature='', is_instrumental=True, lyrics='', vocal_language='unknown',
                 auto_labels={'source': 'ace', 'caption': 'piano', 'bpm': 90, 'time_signature': '3', 'is_instrumental': True})
    with patch.object(app.api, 'track', return_value=track), patch.object(app.api, 'fetch', return_value='a.wav'), \
         patch.object(app.api, 'update_track') as save:
        result = app.on_review_track(1)
    assert result[1] == 'piano' and result[3] == 90 and result[5] == '3'
    save.assert_not_called()
