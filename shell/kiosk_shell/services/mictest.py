"""Microphone test: a live level meter, the input gain, and a record-and-play-back check.

- Level: `pw-record --target <mic> -` streams raw 16-bit mono samples; peak and RMS are measured
  in dBFS (0 dB = the loudest a digital signal can be) every 50 ms.
- Gain: the microphone's PipeWire volume through wpctl (audio.get_volume / set_volume), up to 150 %.
- Test recording: a few seconds into a WAV file in the runtime directory, then played back.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile

from kiosk_shell.services import audio

RATE = 48000
CHUNK_SECONDS = 0.05
SILENCE_DB = -60.0
MAX_GAIN = 1.5  # 150 %
TEST_SECONDS = 5


class MicError(Exception):
    """A microphone test that failed, with a message meant for the user."""


def level_db(samples) -> tuple[float, float]:
    """(peak, RMS) of int16 samples in dBFS, floored at SILENCE_DB."""
    import numpy as np
    if samples.size == 0:
        return SILENCE_DB, SILENCE_DB
    x = samples.astype(np.float64) / 32768.0
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x * x)))
    to_db = lambda v: max(SILENCE_DB, 20 * math.log10(v)) if v > 0 else SILENCE_DB  # noqa: E731
    return to_db(peak), to_db(rms)


def advice(peak_db: float) -> str:
    """What a speaking-voice peak level means, in plain words."""
    if peak_db <= -50:
        return "Nothing heard. Is the right microphone selected and unmuted?"
    if peak_db < -30:
        return "Very quiet: raise the gain or move closer."
    if peak_db < -20:
        return "A bit quiet for voice chat: raise the gain a little."
    if peak_db < -3:
        return "Good level."
    return "Too loud, it will distort: lower the gain."


def gain_db(level: float) -> float:
    """wpctl volumes are cubic: 50 % is -18 dB, 150 % is +10.6 dB."""
    return 60 * math.log10(level) if level > 0 else float("-inf")


def set_gain(level: float, target: str) -> None:
    """Microphone gain, 0-150 % (above 100 % PipeWire amplifies in software)."""
    audio.run("wpctl", "set-volume", target, f"{max(0.0, min(MAX_GAIN, level)):.2f}")


def chunk_bytes() -> int:
    return int(RATE * CHUNK_SECONDS) * 2


def record_command(target: str, path: str = "-") -> list[str]:
    """pw-record of one mono 16-bit stream from `target` (a node name), to a file or stdout."""
    return ["pw-record", "--target", target, "--rate", str(RATE), "--channels", "1", "--format", "s16",
            "-P", "media.name=PERDIX microphone test", path]


def start_meter(target: str) -> subprocess.Popen:
    if shutil.which("pw-record") is None:
        raise MicError("pw-record (PipeWire tools) is not installed.")
    return subprocess.Popen(record_command(target), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def sample_path() -> str:
    folder = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(folder, "perdix-mic-test.wav")


def record_sample(target: str, seconds: int = TEST_SECONDS, path: str | None = None) -> str:
    """Record `seconds` of the microphone into a WAV file; returns its path."""
    path = path or sample_path()
    if shutil.which("pw-record") is None:
        raise MicError("pw-record (PipeWire tools) is not installed.")
    process = subprocess.Popen(record_command(target, path), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        process.wait(seconds)
    except subprocess.TimeoutExpired:
        process.terminate()  # pw-record finishes the WAV header on SIGTERM
        process.wait(5)
    else:
        raise MicError("Recording stopped early: " + (process.stderr.read().decode(errors="replace").strip()
                                                      or "is PipeWire running?"))
    if not os.path.exists(path) or os.path.getsize(path) <= 44:
        raise MicError("Nothing was recorded.")
    return path


def play(path: str) -> None:
    if shutil.which("pw-play") is None:
        raise MicError("pw-play (PipeWire tools) is not installed.")
    result = subprocess.run(["pw-play", path], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise MicError(result.stderr.strip() or "Playback failed.")
