"""Kênh cập nhật online: so phiên bản, tải gói có kiểm tra SHA-256, giải nén an toàn."""
import hashlib
import io
import json
import zipfile

import httpx
import pytest
import respx

from shared import updates

FEED = "https://updates.example.com/latest.json"
ZIP_URL = "https://updates.example.com/MusicStudio-1.2.0.zip"


def package_bytes(entries=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in (entries or {"MusicStudio-1.2.0/release.json": "{}",
                                          "MusicStudio-1.2.0/VERSION": "1.2.0"}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def manifest(data, version="1.2.0"):
    return {"version": version, "url": ZIP_URL, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def test_version_comparison():
    assert updates.is_newer("1.0.10", "1.0.9")
    assert updates.is_newer("v2.0", "1.9.9")
    assert not updates.is_newer("1.0.0", "1.0.0")
    assert not updates.is_newer("1.0.0", "dev")  # bản dev không bị nhắc cập nhật


@respx.mock
def test_check_reports_only_newer_versions():
    respx.get(FEED).mock(return_value=httpx.Response(200, json=manifest(b"x")))
    assert updates.check_for_update(FEED, current="1.1.0")["version"] == "1.2.0"
    assert updates.check_for_update(FEED, current="1.2.0") is None
    assert updates.check_for_update("", current="0.1") is None


@respx.mock
def test_check_rejects_bad_feed():
    respx.get(FEED).mock(return_value=httpx.Response(200, json={"version": "9.9"}))
    with pytest.raises(updates.UpdateError, match="sha256"):
        updates.check_for_update(FEED, current="1.0.0")
    with pytest.raises(updates.UpdateError, match="https"):
        updates.check_for_update("http://updates.example.com/latest.json", current="1.0.0")


@respx.mock
def test_download_verifies_checksum(tmp_path):
    data = package_bytes()
    respx.get(ZIP_URL).mock(return_value=httpx.Response(200, content=data))
    archive = updates.download_update(manifest(data), tmp_path)
    assert archive.read_bytes() == data
    package = updates.extract_package(archive, tmp_path / "package")
    assert (package / "VERSION").read_text() == "1.2.0"

    tampered = manifest(data) | {"sha256": "0" * 64}
    with pytest.raises(updates.UpdateError, match="SHA-256"):
        updates.download_update(tampered, tmp_path / "again")
    assert not list((tmp_path / "again").iterdir())  # không để lại file hỏng


def test_extract_blocks_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(package_bytes({"../escape.txt": "x", "pkg/release.json": "{}"}))
    with pytest.raises(updates.UpdateError, match="không hợp lệ"):
        updates.extract_package(archive, tmp_path / "out")
    assert not (tmp_path / "escape.txt").exists()


def test_feed_url_falls_back_to_release_file(tmp_path, monkeypatch):
    release = tmp_path / "release.json"
    release.write_text(json.dumps({"update_url": FEED}), encoding="utf-8")
    monkeypatch.setattr(updates, "RELEASE_FILE", release)
    assert updates.feed_url("") == FEED
    assert updates.feed_url("https://other/latest.json") == "https://other/latest.json"
