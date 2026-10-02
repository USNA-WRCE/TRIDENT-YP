"""In-memory offline speech recognition for browser-recorded audio."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any


class VoiceRecognitionError(RuntimeError):
    """Raised when audio cannot be decoded or recognized."""


class VoiceRecognitionUnavailable(VoiceRecognitionError):
    """Raised when the offline model or its runtime is not installed."""


_MODEL_PATH = Path(os.getenv("VOSK_MODEL_PATH", "/data/vosk_models/vosk-model-small-en-us-0.15"))
_model: Any = None
_model_lock = threading.Lock()
_recognition_lock = threading.Lock()
MAX_AUDIO_BYTES = 12 * 1024 * 1024


def _load_model() -> Any:
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        if not _MODEL_PATH.is_dir():
            raise VoiceRecognitionUnavailable(
                "Offline voice model is missing. Install the English Vosk model on the server."
            )
        try:
            import vosk
        except ImportError as error:
            raise VoiceRecognitionUnavailable("Offline voice recognition is not installed.") from error
        try:
            _model = vosk.Model(str(_MODEL_PATH))
        except Exception as error:
            raise VoiceRecognitionUnavailable("The offline voice model could not be loaded.") from error
    return _model


def transcribe_audio(audio: bytes) -> str:
    """Decode an uploaded recording and return transient text without writing it to disk."""
    if not audio:
        raise VoiceRecognitionError("No microphone audio was received.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceRecognitionError("Recording is too large. Keep voice commands brief.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VoiceRecognitionUnavailable("Audio decoding is not installed on the server.")

    try:
        decoded = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-t",
                "12",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                "pipe:1",
            ],
            input=audio,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise VoiceRecognitionError("Audio decoding timed out. Try a shorter command.") from error
    if decoded.returncode != 0 or not decoded.stdout:
        raise VoiceRecognitionError("The browser recording format could not be decoded.")

    model = _load_model()
    try:
        import vosk

        with _recognition_lock:
            recognizer = vosk.KaldiRecognizer(model, 16000)
            recognizer.AcceptWaveform(decoded.stdout)
            result = json.loads(recognizer.FinalResult())
        return str(result.get("text") or "").strip()
    except Exception as error:
        raise VoiceRecognitionError("The voice recording could not be recognized.") from error