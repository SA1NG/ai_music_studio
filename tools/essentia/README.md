# Essentia Music Extractor

Installed by `scripts/setup_essentia.py` from the official UPF host:

https://essentia.upf.edu/extractors/essentia-extractors-v2.1_beta5-356-g673b6a14-win-i686/essentia_streaming_extractor_music.exe

SHA-256: `c7847bd4c6e1c3a737b8cd5f94e7889792b086e368a6d71e2890229733b8bda1`.

This is the official standalone Windows build, not a Python wheel. No WSL, Docker,
TensorFlow models or GPU are needed. The executable is excluded from source control.

Upstream documentation: https://essentia.upf.edu/streaming_extractor_music.html

Upstream source and licensing (AGPLv3 and third-party components):
https://github.com/MTG/essentia and https://essentia.upf.edu/licensing_information.html
Review upstream terms when distributing the application. This integration does not
download pretrained high-level classification models.
