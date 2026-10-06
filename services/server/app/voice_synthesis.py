"""Offline, in-memory Piper text-to-speech synthesis."""

from __future__ import annotations

import io
import os
import threading
import wave
from pathlib import Path
from typing import Any


class VoiceSynthesisError(RuntimeError):
    """Raised when text cannot be synthesized."""


class VoiceSynthesisUnavailable(VoiceSynthesisError):
    """Raised when the offline Piper voice or runtime is unavailable."""


MAX_SPEECH_CHARACTERS = 400
_MODEL_PATH = Path(os.getenv("PIPER_MODEL_PATH", "/data/voice_models/en_US-lessac-medium.onnx"))
_voice: Any = None
_voice_lock = threading.Lock()
_synthesis_lock = threading.Lock()


def _load_voice() -> Any:
    global _voice
    if _voice is not None:
        return _voice
    with _voice_lock:
        if _voice is not None:
            return _voice
        if not _MODEL_PATH.is_file() or not Path(f"{_MODEL_PATH}.json").is_file():
            raise VoiceSynthesisUnavailable(
                "Offline speech voice is missing. Install the Piper voice files on the server."
            )
        try:
            from piper import PiperVoice

            _voice = PiperVoice.load(str(_MODEL_PATH))
        except ImportError as error:
            raise VoiceSynthesisUnavailable("Offline Piper speech is not installed.") from error
        except Exception as error:
            raise VoiceSynthesisUnavailable("The offline Piper voice could not be loaded.") from error
    return _voice


def synthesize_speech(text: str) -> bytes:
    """Synthesize a bounded utterance to a WAV byte string without disk I/O."""
    normalized = " ".join(str(text).split())
    if not normalized:
        raise VoiceSynthesisError("Speech text is empty.")
    if len(normalized) > MAX_SPEECH_CHARACTERS:
        raise VoiceSynthesisError("Speech text is too long.")

    voice = _load_voice()
    output = io.BytesIO()
    try:
        with _synthesis_lock:
            with wave.open(output, "wb") as wav_file:
                voice.synthesize_wav(normalized, wav_file)
    except Exception as error:
        raise VoiceSynthesisError("The spoken response could not be generated.") from error
    return output.getvalue()