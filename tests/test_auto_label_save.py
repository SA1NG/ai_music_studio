import json
from unittest.mock import patch

from ui import app
from .conftest import make_wav_bytes
from .test_upload_auto_labels import register_ai_worker


def complete_label(client, worker, *, instrumental=True, labels=None, edits=None):
    register_ai_worker(client, worker)
    track = client.post('/tracks?autolabel=true&require_ai=true&is_instrumental=' + str(instrumental).lower(),
                        files={'files': ('a.wav', make_wav_bytes())}).json()[0]
    job = client.post('/worker/claim', json={'worker_id': worker.worker_id, 'types': ['autolabel']}).json()
    if edits:
        client.put(f"/tracks/{track['id']}", json=edits)
    labels = labels if labels is not None else dict(source='ace', caption='piano', bpm=80, key_scale='D minor',
                                                   time_signature='3', is_instrumental=instrumental)
    result = client.post(f"/worker/jobs/{job['id']}/complete", data={'result': json.dumps({'auto_labels': labels})})
    assert result.status_code == 200, result.text
    return client.get(f"/tracks/{track['id']}").json(), result.json()['result']


def test_successful_ai_labels_are_saved_and_immediately_usable_in_dataset(client, worker):
    track, result = complete_label(client, worker)
    assert track['caption'] == 'piano' and track['bpm'] == 80 and track['key_scale'] == 'D minor'
    assert track['time_signature'] == '3' and track['review_status'] == 'approved'
    assert result['dataset_ready'] is True
    assert client.post('/datasets', json={'name': 'ready', 'track_ids': [track['id']]}).status_code == 200


def test_auto_save_preserves_manual_labels_and_exclusions(client, worker):
    track, _ = complete_label(client, worker, edits={'caption': 'My description', 'bpm': 120})
    assert track['caption'] == 'My description' and track['bpm'] == 120
    excluded, result = complete_label(client, worker, edits={'review_status': 'rejected'})
    assert excluded['review_status'] == 'rejected' and excluded['caption'] == ''
    assert result['dataset_ready'] is False


def test_vocal_labels_need_lyrics_and_store_language(client, worker):
    incomplete, result = complete_label(client, worker, instrumental=False,
                                        labels={'source': 'ace', 'caption': 'vocals', 'is_instrumental': False})
    assert incomplete['caption'] == 'vocals' and incomplete['review_status'] == 'pending'
    assert result['dataset_ready'] is False
    ready, result = complete_label(client, worker, instrumental=False,
                                  labels={'source': 'ace', 'caption': 'vocals', 'is_instrumental': False,
                                          'lyrics': '[Verse] Hello', 'vocal_language': 'en'})
    assert ready['lyrics'] == '[Verse] Hello' and ready['vocal_language'] == 'en'
    assert result['dataset_ready'] is True


def test_mock_suggestions_and_voice_mode_changes_do_not_auto_approve(client, worker):
    track, result = complete_label(client, worker, labels={'source': 'mock', 'caption': 'fake caption'})
    assert track['caption'] == '' and result['dataset_ready'] is False
    changed, result = complete_label(client, worker, edits={'is_instrumental': False},
                                    labels={'source': 'ace', 'caption': 'instrumental', 'is_instrumental': True})
    assert changed['caption'] == '' and changed['review_status'] == 'pending'


def test_availability_refresh_adds_new_ready_tracks_without_reselecting_old_ones():
    tracks = [dict(id=1, filename='a.wav', review_status='approved'), dict(id=2, filename='b.wav', review_status='pending')]
    with patch.object(app.api, 'tracks', return_value=tracks):
        initial = app.refresh_label_availability(2, [1], '')
        tracks[1]['review_status'] = 'approved'
        update = app.refresh_label_availability(2, [], initial[-1])
        assert update[2]['value'] == [2]  # Track 1 was deliberately unchecked.
        assert 'value' not in update[0]  # Changing choice labels must not reset form inputs.
        unchanged = app.refresh_label_availability(2, [], update[-1])
        assert all('value' not in value for value in unchanged[:3])
