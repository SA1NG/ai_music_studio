import json
import io
import zipfile
from unittest.mock import patch

from ui import app
from ui.client import StudioClient
from .conftest import make_wav_bytes


def test_manual_review_to_selected_dataset_without_loaded_engine(client, tmp_path):
    client.app.state.engine.state.update(managed=True, status="idle")
    assert client.post('/tracks?autolabel=true', files={'files': ('blocked.wav', make_wav_bytes())}).status_code == 409
    studio = StudioClient("", cache_dir=tmp_path / "cache", client=client)
    files = [tmp_path / name for name in ("piano.wav", "song.wav")]
    for path in files:
        path.write_bytes(make_wav_bytes())
    with patch.object(app, "api", studio):
        uploaded = app.on_review_upload([str(path) for path in files])
        assert uploaded[0]["value"] == 1
        assert client.get('/jobs').json() == []
        assert app.on_review_track(1)[1] == ""
        failed = app.on_review_save(1, "", "", 0, "", "", True, "", "unknown")
        assert "caption" in failed[-1]
        assert studio.track(1)['review_status'] == 'pending'
        saved = app.on_review_save(1, "soft piano, instrumental", "", 80, "C major", "4", True, "", "unknown")
        assert saved[0]['value'] == 2 and saved[2]['value'] == [1]
        invalid = app.on_review_save(2, "acoustic song", "", 0, "", "", False, "", "en")
        assert studio.track(2)['review_status'] == 'pending'
        assert invalid[-1].startswith('⚠️')
        app.on_review_save(2, "acoustic song", "", 90, "D minor", "3", False, "[Verse]\nHello", "en")
        transfer = app.on_dataset_to_finetune('test-set', [2])
        assert transfer[3]['value'] == 1 and transfer[6] == 'finetune'
        dataset = studio.datasets()[0]
        assert dataset['track_ids'] == [2]
        assert client.get('/jobs').json() == []  # Transfer selects a dataset; it does not train.
        assert studio.track(1)['review_status'] == 'approved'
        assert client.get('/tracks/2').status_code == 404  # Source was moved into the dataset snapshot.
        with zipfile.ZipFile(io.BytesIO(client.get(f"/datasets/{dataset['id']}/download").content)) as archive:
            names = archive.namelist()
            lyrics = next(name for name in names if name.endswith('.lyrics.txt'))
            assert archive.read(lyrics).decode() == '[Verse]\nHello'
            metadata = json.loads(archive.read(lyrics.replace('.lyrics.txt', '.json')))
            assert metadata['language'] == 'en' and metadata['bpm'] == 90
        # A mixed dataset keeps instrumental songs free of lyrics/language sidecars.
        mixed = studio.create_dataset('test-set', [1], base_dataset_id=dataset['id'])
        with zipfile.ZipFile(io.BytesIO(client.get(f"/datasets/{mixed['id']}/download").content)) as archive:
            assert '0001_piano.lyrics.txt' not in archive.namelist()
            assert 'language' not in json.loads(archive.read('0001_piano.json'))
        assert app.on_dataset_to_finetune('empty', [])[2].startswith('⚠️')


def test_exclude_track_advances_without_overwriting_labels(client, tmp_path):
    studio = StudioClient('', cache_dir=tmp_path / 'cache', client=client)
    for name in ('a.wav', 'b.wav'):
        client.post('/tracks?autolabel=false', files={'files': (name, make_wav_bytes())})
    studio.update_track(1, {'caption': 'keep this caption'})
    with patch.object(app, 'api', studio):
        result = app.on_review_save(1, '', '', 0, '', '', True, '', 'unknown', exclude=True)
    assert result[0]['value'] == 2 and result[2]['value'] == []
    assert studio.track(1)['review_status'] == 'rejected'
    assert studio.track(1)['caption'] == 'keep this caption'


def test_dataset_rejects_vocal_track_without_lyrics(client):
    track = client.post('/tracks?autolabel=false', files={'files': ('a.wav', make_wav_bytes())}).json()[0]
    client.put(f"/tracks/{track['id']}", json=dict(caption='vocals', review_status='approved', is_instrumental=False))
    assert client.post('/datasets', json={'name': 'invalid'}).status_code == 400
