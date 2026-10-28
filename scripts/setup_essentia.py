"""Install the official standalone Windows extractor, isolated in this project."""
from pathlib import Path
import hashlib
import os
import subprocess

ROOT = Path(__file__).resolve().parent.parent
URL = 'https://essentia.upf.edu/extractors/essentia-extractors-v2.1_beta5-356-g673b6a14-win-i686/essentia_streaming_extractor_music.exe'
SHA256 = 'c7847bd4c6e1c3a737b8cd5f94e7889792b086e368a6d71e2890229733b8bda1'


def install():
    if os.name != 'nt':
        raise RuntimeError('Bản cài tự động này dành cho Windows; đặt ESSENTIA_EXECUTABLE tới Music Extractor trên hệ điều hành khác.')
    target = ROOT / 'tools/essentia/essentia_streaming_extractor_music.exe'
    target.parent.mkdir(parents=True, exist_ok=True)
    def valid(path):
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256
    if valid(target):
        print('Essentia: already installed and checksum verified.')
        return target
    download = target.with_suffix('.download')
    # PowerShell uses the Windows certificate store; do not disable TLS verification.
    script = '$ProgressPreference="SilentlyContinue"; Invoke-WebRequest -UseBasicParsing -Uri $env:MUSIC_ESSENTIA_URL -OutFile $env:MUSIC_ESSENTIA_TARGET'
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env={**os.environ, 'MUSIC_ESSENTIA_URL': URL, 'MUSIC_ESSENTIA_TARGET': str(download)},
                            timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0 or not valid(download):
        raise RuntimeError('Không tải được Essentia hoặc checksum không khớp. File chưa được cài.')
    download.replace(target)
    print('Essentia: installed official Windows extractor (SHA-256 verified).')
    return target


if __name__ == '__main__':
    install()
