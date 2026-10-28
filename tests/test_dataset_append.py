import json
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from .conftest import make_wav_bytes
from ui import app
from ui.client import StudioClient


def add_track(client, name, approved=True):
    track = client.post('/tracks?autolabel=false', files={'files': (name, make_wav_bytes())}).json()
    assert isinstance(track, list), track
    track = track[0]
    client.put(f"/tracks/{track['id']}", json={'caption': f'original {name}', 'review_status': 'approved' if approved else 'pending'})
    return track['id']


def test_append_preserves_snapshot_and_deduplicates(client):
    first, second, third = [add_track(client, name) for name in ('a.wav', 'b.wav', 'c.wav')]
    original = client.post('/datasets', json={'name': 'lofi', 'track_ids': [first]}).json()
    storage = client.app.state.storage
    original_path = storage.resolve(original['export_path'])
    original_files = {file.name: file.read_bytes() for file in original_path.iterdir()}
    client.put(f'/tracks/{first}', json={'caption': 'edited later', 'review_status': 'rejected'})
    merged = client.post('/datasets', json={'base_dataset_id': original['id'], 'track_ids': [first, second, second]})
    assert merged.status_code == 200, merged.text
    merged = merged.json()
    assert merged['name'] == 'lofi' and merged['version'] == 2 and merged['track_ids'] == [first, second]
    assert {file.name: file.read_bytes() for file in original_path.iterdir()} == original_files
    with zipfile.ZipFile(io.BytesIO(client.get(f"/datasets/{merged['id']}/download").content)) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['version'] == 2
        assert manifest['tracks'][0]['caption'] == 'original a.wav'
        assert archive.read('0001_a.caption.txt').decode() == 'original a.wav'
        assert manifest['tracks'][1]['track_id'] == second
    assert client.post('/datasets', json={'base_dataset_id': merged['id'], 'track_ids': [first, second]}).status_code == 400
    assert client.post('/datasets', json={'base_dataset_id': original['id'], 'track_ids': [third]}).status_code == 409
    assert len(client.get('/datasets').json()) == 2


def test_append_requires_approved_new_tracks_and_valid_original_zip(client):
    first = add_track(client, 'a.wav')
    second = add_track(client, 'b.wav', approved=False)
    original = client.post('/datasets', json={'name': 'set', 'track_ids': [first]}).json()
    assert client.post('/datasets', json={'base_dataset_id': original['id'], 'track_ids': [second]}).status_code == 400
    assert client.post('/datasets', json={'base_dataset_id': 999, 'track_ids': [first]}).status_code == 404
    client.app.state.storage.remove_dataset(original['export_path'])
    assert client.post('/datasets', json={'base_dataset_id': original['id'], 'track_ids': [second]}).status_code == 410
    assert len(client.get('/datasets').json()) == 1


def test_ui_append_selects_merged_version_for_finetune(client, tmp_path):
    first, second = [add_track(client, name) for name in ('a.wav', 'b.wav')]
    studio = StudioClient('', cache_dir=tmp_path / 'cache', client=client)
    with patch.object(app, 'api', studio):
        initial = app.on_dataset_to_finetune('my-set', [first])
        result = app.on_dataset_to_finetune('ignored hidden name', [second], 'append', initial[3]['value'])
        assert result[3]['value'] == 2 and result[4] == 'my-set' and result[6] == 'finetune'
        assert app.existing_dataset_choices() == [('my-set · v2 · 2 bài', 2)]
        assert app.on_dataset_to_finetune('my-set', [second])[2].startswith('⚠️')
        assert app.on_dataset_to_finetune('', [second], 'append', None)[2].startswith('⚠️')
    assert studio.datasets()[0]['track_ids'] == [first, second]


def test_simultaneous_appends_do_not_overwrite_version_files(client):
    ids = [add_track(client, name) for name in ('a.wav', 'b.wav', 'c.wav')]
    original = client.post('/datasets', json={'name': 'set', 'track_ids': ids[:1]}).json()
    def append(track):
        return client.post('/datasets', json={'base_dataset_id': original['id'], 'track_ids': [track]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(append, ids[1:]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    datasets = client.get('/datasets').json()
    assert len(datasets) == 2 and datasets[0]['version'] == 2
    with zipfile.ZipFile(io.BytesIO(client.get(f"/datasets/{datasets[0]['id']}/download").content)) as archive:
        assert archive.testzip() is None
        assert len(json.loads(archive.read('manifest.json'))['tracks']) == 2
