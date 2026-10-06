#!/usr/bin/env python3
"""Download the en_US Lessac medium Piper voice for offline status feedback."""

from __future__ import annotations

import argparse
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


VOICE_NAME = "en_US-lessac-medium"
VOICE_BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
    "en/en_US/lessac/medium"
)
FILES = (
    f"{VOICE_NAME}.onnx",
    f"{VOICE_NAME}.onnx.json",
)


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "TRIDENT-YP/tts-voice-setup"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except (OSError, urllib.error.URLError) as error:
        destination.unlink(missing_ok=True)
        raise SystemExit(f"Unable to download {url}: {error}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/piper_voice"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryFile(dir=args.out):
            pass
    except OSError as error:
        raise SystemExit(f"Voice model directory is not writable: {args.out.resolve()}: {error}") from error

    for filename in FILES:
        destination = args.out / filename
        if destination.exists():
            raise SystemExit(f"Voice model file already exists: {destination}")

    with tempfile.TemporaryDirectory(dir=args.out) as temporary_directory:
        temporary_root = Path(temporary_directory)
        for filename in FILES:
            temporary_file = temporary_root / filename
            print(f"Downloading {filename}")
            download(f"{VOICE_BASE_URL}/{filename}", temporary_file)
        for filename in FILES:
            (temporary_root / filename).replace(args.out / filename)
    print(f"Installed Piper voice in {args.out.resolve()}")


if __name__ == "__main__":
    main()