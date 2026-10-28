"""Run inside ACE-Step's own Python environment; never import its packages into the UI."""
import argparse
import json
import sys
from pathlib import Path


def catalog(downloader):
    names = set(downloader.SUBMODEL_REGISTRY) | set(downloader.MAIN_MODEL_COMPONENTS)
    return [{"name": name, "kind": "LM" if name.startswith("acestep-5Hz-lm-") else "DiT"}
            for name in sorted(names) if name.startswith("acestep-")]


def download(name, root, downloader):
    if name not in {item["name"] for item in catalog(downloader)}:
        raise ValueError("Model không có trong danh sách của ACE-Step đang cài.")
    if name in downloader.SUBMODEL_REGISTRY:
        # force only bypasses the directory-exists shortcut; the underlying hub resumes files.
        ok, message = downloader.download_submodel(name, checkpoints_dir=root, force=True)
        if not ok:
            raise RuntimeError(message)
        print(message, flush=True)
    else:
        from huggingface_hub import snapshot_download
        # Download only the selected bundled component, not the entire main package.
        snapshot_download(repo_id=downloader.MAIN_MODEL_REPO, local_dir=str(root),
                          allow_patterns=[f"{name}/*"])
        sync = getattr(downloader, "_sync_model_code_files", None)
        if sync:
            sync(name, root)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["catalog", "download"])
    parser.add_argument("--name")
    parser.add_argument("--dir")
    args = parser.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    from acestep import model_downloader
    if args.action == "catalog":
        print("MUSIC_STUDIO_CATALOG=" + json.dumps(catalog(model_downloader)))
    else:
        download(args.name, Path(args.dir), model_downloader)


if __name__ == "__main__":
    main()
