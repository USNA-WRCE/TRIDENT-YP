#!/usr/bin/env python3
"""Download and install the small English Vosk model for offline voice commands."""

from __future__ import annotations

import argparse
import tempfile
import urllib.request
import zipfile
from pathlib import Path


MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/vosk_models"))
    args = parser.parse_args()
    destination = args.out.resolve() / MODEL_NAME
    if destination.exists():
        raise SystemExit(f"Model already exists: {destination}")

    args.out.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryFile(dir=args.out):
            pass
    except OSError as error:
        raise SystemExit(f"Model directory is not writable: {args.out.resolve()}: {error}") from error
    print(f"Downloading {MODEL_NAME} from {MODEL_URL}")
    with tempfile.TemporaryFile() as archive_file:
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "TRIDENT-YP/voice-model-setup"})
        with urllib.request.urlopen(request, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                archive_file.write(chunk)
        archive_file.seek(0)
        with zipfile.ZipFile(archive_file) as archive:
            output_root = args.out.resolve()
            for member in archive.infolist():
                target = (output_root / member.filename).resolve()
                if not target.is_relative_to(output_root):
                    raise SystemExit(f"Unsafe path in model archive: {member.filename}")
            archive.extractall(output_root)
    if not destination.is_dir():
        raise SystemExit(f"Expected model directory was not created: {destination}")
    print(f"Installed offline speech model in {destination}")


if __name__ == "__main__":
    main()