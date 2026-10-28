"""Cấu hình đăng bản: github_repo tự sinh địa chỉ kênh cập nhật."""
import json
from types import SimpleNamespace

import pytest

from scripts import make_release


def config_with(tmp_path, monkeypatch, data):
    path = tmp_path / "release.config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(make_release, "CONFIG_FILE", path)
    return make_release.load_config(SimpleNamespace(feed_url=None, download_url=None))


def test_github_repo_derives_stable_feed(tmp_path, monkeypatch):
    config = config_with(tmp_path, monkeypatch, {"github_repo": "acme/music-studio-releases"})
    assert config["feed_url"] == "https://github.com/acme/music-studio-releases/releases/latest/download/latest.json"
    assert config["download_url"].replace("{version}", "1.2.0") == \
        "https://github.com/acme/music-studio-releases/releases/download/v1.2.0/MusicStudio-1.2.0.zip"


def test_invalid_config_is_rejected(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        config_with(tmp_path, monkeypatch, {"github_repo": "not a repo"})
    with pytest.raises(SystemExit):
        config_with(tmp_path, monkeypatch, {"feed_url": "http://insecure.example.com/latest.json"})


def test_no_config_means_manual_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(make_release, "CONFIG_FILE", tmp_path / "missing.json")
    assert make_release.load_config(SimpleNamespace(feed_url=None, download_url=None))["feed_url"] == ""
