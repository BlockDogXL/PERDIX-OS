"""Sound: PipeWire volume and devices (wpctl, pw-dump) and one-click effects through EasyEffects.

PipeWire and its session manager WirePlumber run as the kiosk user's services; `wpctl` sets
volumes, mute and the default device, and `pw-dump` lists the devices.

Effects (volume boost, bass boost, microphone noise suppression) are EasyEffects presets that this
module writes as ~/.local/share/easyeffects/output/perdix-output.json and input/perdix-mic.json and
loads with `easyeffects -l <name>` into EasyEffects running in service mode (no window). The two
presets have different names because EasyEffects 7 loads only the input one when both share a name. With every effect
off, EasyEffects is quit, so sound goes straight to the card with no added latency. The preset
keys come from EasyEffects' own preset code (bass_enhancer, limiter and rnnoise plugins).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

SINK = "@DEFAULT_AUDIO_SINK@"
SOURCE = "@DEFAULT_AUDIO_SOURCE@"
TIMEOUT = 5
OUTPUT_PRESET = "perdix-output"
INPUT_PRESET = "perdix-mic"
SERVICE_START_WAIT = 15  # seconds EasyEffects may take to come up in service mode


class AudioError(Exception):
    """A failed sound command, with a message meant for the user."""


@dataclass
class Device:
    id: int
    name: str  # node.name, e.g. alsa_output.pci-0000_00_1f.3.analog-stereo
    description: str  # what people read, e.g. "Built-in Audio Analog Stereo"
    is_default: bool = False


@dataclass
class Volume:
    level: float  # 0.0-1.0 (1.0 = 100 %)
    muted: bool


# --- PipeWire ------------------------------------------------------------------------------------


def available() -> bool:
    return shutil.which("wpctl") is not None


def run(*argv: str, timeout: int = TIMEOUT) -> str:
    if shutil.which(argv[0]) is None:
        raise AudioError(f"{argv[0]} is not installed.")
    try:
        result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AudioError(f"{argv[0]} did not answer in time.") from None
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if "connect" in message.lower() or "pipewire" in message.lower():
            raise AudioError("The sound service (PipeWire) is not running.")
        raise AudioError(message or f"{argv[0]} failed (exit code {result.returncode}).")
    return result.stdout


def parse_volume(text: str) -> Volume:
    """'Volume: 0.45 [MUTED]' -> Volume(0.45, True)."""
    match = re.search(r"Volume:\s*([0-9.]+)", text)
    if match is None:
        raise AudioError("No sound device found.")
    return Volume(float(match.group(1)), "[MUTED]" in text)


def get_volume(target: str = SINK) -> Volume:
    return parse_volume(run("wpctl", "get-volume", target))


def set_volume(level: float, target: str = SINK) -> None:
    """0.0-1.0. Louder than 100 % is the Volume Boost effect's job (it can't clip)."""
    run("wpctl", "set-volume", target, f"{max(0.0, min(1.0, level)):.2f}")


def set_muted(muted: bool, target: str = SINK) -> None:
    run("wpctl", "set-mute", target, "1" if muted else "0")


def parse_devices(dump: list, media_class: str) -> list[Device]:
    """Sinks ("Audio/Sink") or sources ("Audio/Source") from `pw-dump` output. EasyEffects' own
    virtual devices are left out: they are part of the effects, not a place to send sound."""
    key = "default.audio.sink" if media_class == "Audio/Sink" else "default.audio.source"
    default = ""
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Metadata" and obj.get("props", {}).get("metadata.name") == "default":
            for entry in obj.get("metadata") or []:
                if entry.get("key") == key and isinstance(entry.get("value"), dict):
                    default = entry["value"].get("name", "")
    found = []
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = (obj.get("info") or {}).get("props") or {}
        name = props.get("node.name", "")
        node_class = props.get("media.class", "")
        # Sources include virtual microphones ("Audio/Source/Virtual"), as wpctl lists them too.
        matches = node_class == media_class or (media_class == "Audio/Source" and node_class.startswith("Audio/Source/"))
        if not matches or name.startswith("easyeffects_"):
            continue
        description = props.get("node.description") or props.get("node.nick") or name
        found.append(Device(obj["id"], name, description, name == default))
    return sorted(found, key=lambda d: (not d.is_default, d.description.lower()))


def devices(media_class: str) -> list[Device]:
    try:
        dump = json.loads(run("pw-dump", timeout=TIMEOUT))
    except ValueError:
        raise AudioError("Could not read the device list from PipeWire.") from None
    return parse_devices(dump, media_class)


def set_default(device: Device) -> None:
    run("wpctl", "set-default", str(device.id))


# --- EasyEffects -----------------------------------------------------------------------------------

BOOST_DB = {"off": 0, "low": 3, "medium": 6, "high": 9}  # Volume Boost: extra loudness in dB
BASS_AMOUNT = {"off": 0, "low": 3, "medium": 6, "high": 10}  # Bass Boost: Calf bass enhancer amount (dB)


@dataclass
class Effects:
    volume_boost: str = "off"  # a key of BOOST_DB
    bass_boost: str = "off"  # a key of BASS_AMOUNT
    noise_suppression: bool = False

    @classmethod
    def from_dict(cls, data: object) -> "Effects":
        effects = cls()
        if isinstance(data, dict):
            if data.get("volume_boost") in BOOST_DB:
                effects.volume_boost = data["volume_boost"]
            if data.get("bass_boost") in BASS_AMOUNT:
                effects.bass_boost = data["bass_boost"]
            if isinstance(data.get("noise_suppression"), bool):
                effects.noise_suppression = data["noise_suppression"]
        return effects

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def any_output(self) -> bool:
        return self.volume_boost != "off" or self.bass_boost != "off"

    @property
    def any(self) -> bool:
        return self.any_output or self.noise_suppression


def output_preset(effects: Effects) -> dict:
    """Bass enhancer (optional), then a limiter that adds the boost and keeps peaks at -1 dBFS,
    so neither effect can clip."""
    order, section = [], {"blocklist": []}
    if effects.bass_boost != "off":
        order.append("bass_enhancer#0")
        section["bass_enhancer#0"] = {
            "bypass": False, "input-gain": 0.0, "output-gain": 0.0,
            "amount": float(BASS_AMOUNT[effects.bass_boost]),
            "harmonics": 8.5, "scope": 100.0, "floor": 20.0, "floor-active": False, "blend": 0.0,
        }
    if effects.any_output:
        order.append("limiter#0")
        section["limiter#0"] = {
            "bypass": False, "input-gain": float(BOOST_DB[effects.volume_boost]), "output-gain": 0.0,
            "threshold": -1.0, "gain-boost": False, "lookahead": 5.0, "attack": 5.0, "release": 5.0,
            "mode": "Herm Thin", "oversampling": "None", "dithering": "None", "sidechain-type": "Internal",
        }
    section["plugins_order"] = order
    return {"output": section}


def input_preset(effects: Effects) -> dict:
    """RNNoise, the neural-network noise suppressor, on the microphone."""
    section = {"blocklist": [], "plugins_order": []}
    if effects.noise_suppression:
        section["plugins_order"] = ["rnnoise#0"]
        section["rnnoise#0"] = {
            "bypass": False, "input-gain": 0.0, "output-gain": 0.0, "use-standard-model": True,
            "enable-vad": False, "vad-thres": 50.0, "wet": 0.0, "release": 20.0,
        }
    return {"input": section}


def presets_dir() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(data_home) / "easyeffects"


def write_presets(effects: Effects, base: Path | None = None) -> None:
    base = base or presets_dir()
    for kind, name, preset in (("output", OUTPUT_PRESET, output_preset(effects)),
                               ("input", INPUT_PRESET, input_preset(effects))):
        folder = base / kind
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f".{name}.json.tmp"
        tmp.write_text(json.dumps(preset, indent=4) + "\n", encoding="utf-8")
        os.replace(tmp, folder / f"{name}.json")


def easyeffects_installed() -> bool:
    return shutil.which("easyeffects") is not None


def server_socket() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "EasyEffectsServer"


def easyeffects_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "-u", str(os.getuid()), "easyeffects"],
                              capture_output=True, timeout=TIMEOUT).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def start_service() -> None:
    """EasyEffects in service mode (no window), detached so it outlives whoever started it."""
    subprocess.Popen(["easyeffects", "--service-mode"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + SERVICE_START_WAIT
    while time.monotonic() < deadline:
        if server_socket().exists() and easyeffects_running():
            return
        time.sleep(0.25)
    raise AudioError("EasyEffects did not start.")


def apply_effects(effects: Effects) -> str:
    """Make the effects match the settings; returns a message for the user. Blocks for up to a few
    seconds the first time (EasyEffects starting), so call it off the UI thread."""
    if not easyeffects_installed():
        raise AudioError("EasyEffects is not installed.")
    write_presets(effects)
    if not effects.any:
        if easyeffects_running():
            run("easyeffects", "-q", timeout=10)
        return "Effects off: sound goes straight to the speakers, with no added latency."
    if not easyeffects_running():
        start_service()
    for name in (OUTPUT_PRESET, INPUT_PRESET):
        if "does not exist" in run("easyeffects", "-l", name, timeout=10):
            raise AudioError(f"EasyEffects could not find the {name} preset.")
    # Each effect adds a few milliseconds; with all of them off, EasyEffects is stopped (see above).
    return "Effects on: " + ", ".join(describe(effects)) + ". Turn them all off for the lowest latency."


def describe(effects: Effects) -> list[str]:
    parts = []
    if effects.volume_boost != "off":
        parts.append(f"volume boost +{BOOST_DB[effects.volume_boost]} dB")
    if effects.bass_boost != "off":
        parts.append(f"bass boost ({effects.bass_boost})")
    if effects.noise_suppression:
        parts.append("microphone noise suppression")
    return parts


def open_easyeffects() -> None:
    """The full EasyEffects window (equalizer and every other effect). A running service shows its
    window; otherwise EasyEffects starts normally."""
    if not easyeffects_installed():
        raise AudioError("EasyEffects is not installed.")
    subprocess.Popen(["easyeffects"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
