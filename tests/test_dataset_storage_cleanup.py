import io
import json
import zipfile
from unittest.mock import patch

import pytest

from shared.dataset_files import FolderWriter, copy_dataset
from worker.backends.mock import MockBackend
from .test_mock_backend import FakeCtx
from .test_dataset_append import add_track


def test_folder_snapshot_cleans_sources_and_trains_without_zip(client, worker, settings, tmp_path):
    ids = [add_track(client, name) for name in ('a.wav', 'b.wav')]
    storage = client.app.state.storage
    original = [storage.resolve(client.get(f'/tracks/{key}').json()['path']) for key in ids]
    ds = client.post('/datasets', json={'name': 'direct', 'track_ids': ids, 'cleanup_sources': True}).json()
    folder = storage.resolve(ds['export_path'])
    assert folder.is_dir() and len(ds['removed_track_ids']) == 2
    assert client.get('/tracks').json() == [] and all(not file.exists() for file in original)
    assert not list((storage.root / 'datasets').glob('*.zip'))
    request = client.post('/train', json={'dataset_id': ds['id'], 'base_model': 'acestep-v15-turbo', 'style_name': 'direct'})
    assert request.status_code == 200, request.text
    job = client.get(f"/jobs/{request.json()['job_id']}").json()
    assert job['payload']['dataset_format'] == 'directory'
    work = tmp_path / 'worker'
    work.mkdir()
    ctx = FakeCtx(work, job['payload'])  # No download mapping: trying to fetch a ZIP would fail.
    assert MockBackend(settings, sleep=lambda _: None).train(ctx).files
    download = client.get(f"/datasets/{ds['id']}/download")
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert len(json.loads(archive.read('manifest.json'))['tracks']) == 2
    assert not list((storage.root / 'datasets').glob('*.zip'))


def test_export_failure_keeps_uploads_and_removes_incomplete_folder(client):
    key = add_track(client, 'a.wav')
    storage = client.app.state.storage
    path = storage.resolve(client.get(f'/tracks/{key}').json()['path'])
    with patch.object(FolderWriter, 'write', side_effect=OSError('disk full')):
        with pytest.raises(OSError, match='disk full'):
            client.post('/datasets', json={'name': 'failed', 'track_ids': [key], 'cleanup_sources': True})
    assert path.is_file() and client.get(f'/tracks/{key}').status_code == 200
    assert list((storage.root / 'datasets').iterdir()) == []
    assert client.get('/datasets').json() == []


def test_active_jobs_block_deletion_and_skip_automatic_cleanup(client):
    key = add_track(client, 'a.wav')
    track = client.get(f'/tracks/{key}').json()
    db = client.app.state.db
    job_id = db.create_job('autolabel', {'track_id': key, 'file': track['path']})
    assert client.delete(f'/tracks/{key}').status_code == 409
    ds = client.post('/datasets', json={'name': 'busy', 'track_ids': [key], 'cleanup_sources': True}).json()
    assert not ds['removed_track_ids'] and ds['cleanup_warnings']
    assert client.get(f'/tracks/{key}').status_code == 200
    db.finish_job(job_id, 'failed', error='test stopped')
    assert client.delete(f'/tracks/{key}').status_code == 200
    train = db.create_job('train', {'dataset_id': ds['id'], 'dataset_file': ds['export_path']})
    assert client.delete(f"/datasets/{ds['id']}").status_code == 409
    db.finish_job(train, 'failed', error='test stopped')
    assert client.delete(f"/datasets/{ds['id']}").status_code == 200


def test_deleting_version_keeps_other_snapshot_and_lora_and_never_reuses_version(client):
    storage, db = client.app.state.storage, client.app.state.db
    first, second, third = [add_track(client, name) for name in ('a.wav', 'b.wav', 'c.wav')]
    a = client.post('/datasets', json={'name': 'set', 'track_ids': [first], 'cleanup_sources': True}).json()
    b = client.post('/datasets', json={'base_dataset_id': a['id'], 'track_ids': [second], 'cleanup_sources': True}).json()
    lora = db.create_lora(dict(name='adapter', style_name='set', base_model='base', dataset_id=b['id'], path='loras/test.safetensors'))
    assert client.delete(f"/datasets/{a['id']}").status_code == 200
    assert (storage.resolve(b['export_path']) / '0001_a.wav').is_file()
    assert client.delete(f"/datasets/{b['id']}").status_code == 200
    assert db.get_lora(lora)['dataset_id'] == b['id']
    assert not storage.resolve(b['export_path']).exists()
    assert client.get(f"/datasets/{b['id']}/download").status_code == 404
    # Another version can be created after every previous version was removed.
    c = client.post('/datasets', json={'name': 'set', 'track_ids': [third]}).json()
    assert c['version'] == 3


def test_legacy_zip_can_be_extended_to_folder_and_unsafe_zip_is_rejected(client, tmp_path):
    key = add_track(client, 'a.wav')
    db, storage = client.app.state.db, client.app.state.storage
    track = db.get_track(key)
    legacy = storage.export_dataset('legacy', 1, [track])
    old_id = db.create_dataset('legacy', 1, [key], storage.rel(legacy))
    new_key = add_track(client, 'b.wav')
    response = client.post('/datasets', json={'base_dataset_id': old_id, 'track_ids': [new_key]})
    assert response.status_code == 200, response.text
    assert storage.resolve(response.json()['export_path']).is_dir()
    dangerous = tmp_path / 'bad.zip'
    with zipfile.ZipFile(dangerous, 'w') as archive:
        archive.writestr('../outside.txt', 'bad')
    with pytest.raises(ValueError):
        copy_dataset(dangerous, tmp_path / 'unpack')
    assert not (tmp_path / 'outside.txt').exists()
