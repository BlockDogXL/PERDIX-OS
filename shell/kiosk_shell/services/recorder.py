"""Game recording with OBS Studio (libobs) running in the background, started and stopped with F9.

- OBS gets its own "PERDIX" profile and scene collection: the whole screen (X11 shared-memory
  capture, which also sees games inside gamescope), game sound and microphone (PulseAudio API, served
  by pipewire-pulse), 60 frames per second at the screen's own size, MKV files in ~/Videos/PERDIX
  (an MKV survives a crash; the cutter can turn it into MP4 without re-encoding).
- The encoder is the GPU's own video engine so the game keeps its frame rate: NVENC on NVIDIA,
  VA-API on AMD and Intel. Each is tried with a two-frame ffmpeg test before it is chosen, and only if
  the installed OBS registers that encoder; otherwise x264 (CPU, "veryfast").
- OBS is started hidden in the tray only for a recording and quit afterwards (SIGINT: clean exit), so it costs nothing
  while you only play. It is controlled through obs-websocket 5 (built into OBS) with a random
  password kept in ~/.config/perdix-os/obs-websocket.key.
- `python -m kiosk_shell.services.recorder --hotkey`, started by kiosk-session, watches F9 (or the
  chosen key) on every keyboard through evdev and toggles recording, also inside fullscreen games.
"""

from __future__ import annotations

import configparser
import functools
import glob
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

from kiosk_shell.services import hub_settings, obsws

PROFILE = "PERDIX"
SCENE = "PERDIX"
PORT = 4455
PLUGIN_DIRS = ("/usr/lib/obs-plugins", "/usr/lib/x86_64-linux-gnu/obs-plugins", "/usr/lib64/obs-plugins")
HOTKEYS = ("KEY_F9", "KEY_F10", "KEY_F11", "KEY_F12", "KEY_SCROLLLOCK", "KEY_PAUSE")
QUALITY_KBPS = {"standard": 20000, "high": 40000, "max": 80000}
DEFAULTS = {"hotkey": "KEY_F9", "fps": 60, "quality": "high", "microphone": True, "encoder": "auto"}

# OBS encoder id -> (what people call it, the ffmpeg encoder used for the hardware test). Ids differ
# between OBS versions and builds; the first one the installed OBS registers is used.
ENCODERS = {
    "obs_nvenc_h264_tex": ("NVIDIA NVENC (GPU)", "h264_nvenc"),  # OBS 31+
    "jim_nvenc": ("NVIDIA NVENC (GPU)", "h264_nvenc"),
    "ffmpeg_nvenc": ("NVIDIA NVENC (GPU)", "h264_nvenc"),
    "ffmpeg_vaapi_tex": ("VA-API, AMD / Intel (GPU)", "h264_vaapi"),  # zero-copy
    "ffmpeg_vaapi": ("VA-API, AMD / Intel (GPU)", "h264_vaapi"),
    "obs_x264": ("x264 (CPU)", ""),
}


class RecorderError(Exception):
    """A recording action that failed, with a message meant for the user."""


def obs_config_dir() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "obs-studio")


def videos_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Videos", "PERDIX")


def settings() -> dict:
    values = hub_settings.merged(DEFAULTS, hub_settings.load().get("recording"))
    if values["hotkey"] not in HOTKEYS:
        values["hotkey"] = DEFAULTS["hotkey"]
    if values["quality"] not in QUALITY_KBPS:
        values["quality"] = DEFAULTS["quality"]
    if values["fps"] not in (30, 60):
        values["fps"] = 60
    return values


def password(path: str | None = None) -> str:
    """The obs-websocket password, made once and readable only by the user."""
    path = path or os.path.join(os.path.dirname(hub_settings.HUB_SETTINGS_PATH), "obs-websocket.key")
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
        if len(value) >= 16:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(24)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    return value


# --- choosing the encoder ----------------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def obs_has_encoder(encoder: str, plugin_dirs=PLUGIN_DIRS) -> bool:
    """Whether the installed OBS registers this encoder id (looked up in its plugin files)."""
    needle = encoder.encode() + b"\0"
    for folder in plugin_dirs:
        for path in glob.glob(os.path.join(folder, "*.so")):
            try:
                with open(path, "rb") as f:
                    if needle in f.read():
                        return True
            except OSError:
                continue
    return False


def hardware_test(ffmpeg_encoder: str) -> bool:
    """Encode two tiny frames with the GPU encoder; True when the driver really can."""
    if shutil.which("ffmpeg") is None:
        return False
    source = ["-f", "lavfi", "-i", "testsrc2=size=256x144:rate=30"]
    if ffmpeg_encoder == "h264_vaapi":
        devices = sorted(glob.glob("/dev/dri/renderD*"))
        attempts = [["-vaapi_device", d] + source + ["-vf", "format=nv12,hwupload"] for d in devices]
    else:
        attempts = [source]
    for args in attempts:
        try:
            result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *args, "-frames:v", "2",
                                     "-c:v", ffmpeg_encoder, "-f", "null", "-"], capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


def detect_encoder(has_encoder=obs_has_encoder, test=hardware_test) -> str:
    tested: dict[str, bool] = {}
    for encoder, (_label, ffmpeg_encoder) in ENCODERS.items():
        if not ffmpeg_encoder or not has_encoder(encoder):
            continue
        if ffmpeg_encoder not in tested:
            tested[ffmpeg_encoder] = test(ffmpeg_encoder)
        if tested[ffmpeg_encoder]:
            return encoder
    return "obs_x264"


def encoder_settings(encoder: str, kbps: int) -> dict:
    values = {"rate_control": "CBR", "bitrate": kbps, "keyint_sec": 2}
    if encoder == "obs_x264":
        values["preset"] = "veryfast"
    return values


# --- OBS configuration ----------------------------------------------------------------------------------------


def _ini(path: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # OBS keys are case sensitive
    try:
        parser.read(path, encoding="utf-8-sig")
    except configparser.Error:
        pass
    return parser


def _update_ini(path: str, values: dict[str, dict[str, object]]) -> None:
    parser = _ini(path)
    for section, items in values.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in items.items():
            parser.set(section, key, str(value))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        parser.write(f, space_around_delimiters=False)


def screen_size() -> tuple[int, int]:
    try:
        from Xlib import display
        d = display.Display()
        screen = d.screen()
        size = screen.width_in_pixels, screen.height_in_pixels
        d.close()
        return size
    except Exception:
        return 1920, 1080


def prepare_config(encoder: str, values: dict, key: str, size: tuple[int, int], folder: str,
                   base: str | None = None) -> None:
    """Write the PERDIX profile and the obs-websocket settings before OBS starts."""
    base = base or obs_config_dir()
    width, height = size
    _update_ini(os.path.join(base, "global.ini"), {
        "General": {"FirstRun": "true", "ConfirmOnExit": "false"},  # no wizard, no "Exit OBS?" question
        "Basic": {"Profile": PROFILE, "ProfileDir": PROFILE, "SceneCollection": SCENE, "SceneCollectionFile": SCENE},
        "OBSWebSocket": {"FirstLoad": "false", "ServerEnabled": "true", "ServerPort": PORT, "AlertsEnabled": "false",
                         "AuthRequired": "true", "ServerPassword": key},
    })
    # OBS 31 and later read the WebSocket settings from here instead.
    websocket_dir = os.path.join(base, "plugin_config", "obs-websocket")
    os.makedirs(websocket_dir, exist_ok=True)
    with open(os.path.join(websocket_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"alerts_enabled": False, "auth_required": True, "first_load": False, "server_enabled": True,
                   "server_password": key, "server_port": PORT}, f, indent=2)
    os.chmod(os.path.join(websocket_dir, "config.json"), 0o600)
    os.chmod(os.path.join(base, "global.ini"), 0o600)

    profile = os.path.join(base, "basic", "profiles", PROFILE)
    _update_ini(os.path.join(profile, "basic.ini"), {
        "General": {"Name": PROFILE},
        "Video": {"BaseCX": width, "BaseCY": height, "OutputCX": width, "OutputCY": height, "FPSType": 0,
                  "FPSCommon": values["fps"]},
        "Output": {"Mode": "Advanced"},
        "AdvOut": {"RecType": "Standard", "RecFilePath": folder, "RecFormat2": "mkv", "RecEncoder": encoder,
                   "RecTracks": 1, "RecFileNameWithoutSpace": "true"},
        "SimpleOutput": {"FilePath": folder, "RecFormat2": "mkv"},
    })
    with open(os.path.join(profile, "recordEncoder.json"), "w", encoding="utf-8") as f:
        json.dump(encoder_settings(encoder, QUALITY_KBPS[values["quality"]]), f, indent=2)
    os.makedirs(folder, exist_ok=True)


# --- keeping OBS out of the game's way ------------------------------------------------------------------------

OPENBOX_MARK = "PERDIX: OBS"
OPENBOX_RULE = f"""  <!-- {OPENBOX_MARK} records in the background; its windows never cover the game or take focus. -->
  <application class="obs">
    <focus>no</focus>
    <iconic>yes</iconic>
    <layer>below</layer>
    <skip_taskbar>yes</skip_taskbar>
    <skip_pager>yes</skip_pager>
  </application>
"""


def ensure_openbox_rule(user_rc: str | None = None, system_rc: str = "/etc/xdg/openbox/rc.xml") -> bool:
    """PERDIX has no system tray for OBS to hide in, so Openbox gets a rule for OBS's windows: start
    minimised, below everything, without focus. Added to the user's rc.xml (made from the system one
    the first time); Openbox is told to reload. True when the file was changed."""
    user_rc = user_rc or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                      "openbox", "rc.xml")
    source = user_rc if os.path.exists(user_rc) else system_rc
    try:
        with open(source, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    if OPENBOX_MARK in text or "</applications>" not in text:
        return False
    at = text.rindex("</applications>")
    text = text[:at] + OPENBOX_RULE + text[at:]
    os.makedirs(os.path.dirname(user_rc), exist_ok=True)
    with open(user_rc, "w", encoding="utf-8") as f:
        f.write(text)
    if shutil.which("openbox"):
        subprocess.run(["openbox", "--reconfigure"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, check=False)
    return True


# --- talking to OBS --------------------------------------------------------------------------------------------


def connect(key: str, timeout: float = 3.0) -> obsws.Client:
    return obsws.Client("127.0.0.1", PORT, key, timeout).connect()


def setup_scene(client: obsws.Client, values: dict, size: tuple[int, int]) -> None:
    """The PERDIX scene: the screen, game sound and (optionally) the microphone."""
    width, height = size
    client.call("SetVideoSettings", {"fpsNumerator": values["fps"], "fpsDenominator": 1, "baseWidth": width,
                                     "baseHeight": height, "outputWidth": width, "outputHeight": height})
    scenes = [s["sceneName"] for s in client.call("GetSceneList")["scenes"]]
    if SCENE not in scenes:
        client.call("CreateScene", {"sceneName": SCENE})
    client.call("SetCurrentProgramScene", {"sceneName": SCENE})
    inputs = {i["inputName"] for i in client.call("GetInputList")["inputs"]}
    wanted = [("Screen", "xshm_input", {"screen": 0, "show_cursor": True}),
              ("Game sound", "pulse_output_capture", {"device_id": "default"})]
    if values["microphone"]:
        wanted.append(("Microphone", "pulse_input_capture", {"device_id": "default"}))
    for name, kind, input_settings in wanted:
        if name not in inputs:
            client.call("CreateInput", {"sceneName": SCENE, "inputName": name, "inputKind": kind,
                                        "inputSettings": input_settings, "sceneItemEnabled": True})
    if "Microphone" in inputs and not values["microphone"]:
        client.call("RemoveInput", {"inputName": "Microphone"})
    client.call("SetRecordDirectory", {"recordDirectory": videos_dir()})


@dataclass
class Status:
    recording: bool
    seconds: float = 0.0
    fps: float = 0.0
    dropped: int = 0  # frames OBS could not render or encode in time
    total: int = 0
    path: str = ""

    def text(self) -> str:
        if not self.recording:
            return "Not recording."
        minutes, seconds = divmod(int(self.seconds), 60)
        dropped = f"{self.dropped} dropped" if self.dropped else "no dropped frames"
        return f"Recording {minutes:02d}:{seconds:02d} · {self.fps:.0f} fps · {dropped}"


def pid_file() -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "perdix-obs.pid")


def our_obs():
    """The OBS that PERDIX started (the hub and the F9 watcher share it), or None."""
    import psutil
    try:
        with open(pid_file(), encoding="utf-8") as f:
            process = psutil.Process(int(f.read().strip()))
        alive = process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        return process if alive and process.name() == "obs" else None
    except (OSError, ValueError, psutil.Error):
        return None


def other_obs_running() -> bool:
    import psutil
    mine = our_obs()
    return any(p.info["name"] == "obs" and p.info["status"] != psutil.STATUS_ZOMBIE
               and (mine is None or p.pid != mine.pid) for p in psutil.process_iter(["name", "status"]))


class Recorder:
    """Starts OBS when a recording begins and quits it when the recording ends."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.encoder = ""

    def key(self) -> str:
        return password()

    def _choose_encoder(self, values: dict) -> str:
        if values["encoder"] in ENCODERS and values["encoder"] != "auto":
            return values["encoder"]
        if not self.encoder:
            self.encoder = detect_encoder()
        return self.encoder

    def _client(self) -> obsws.Client | None:
        try:
            return connect(self.key(), timeout=2.0)
        except obsws.OBSError:
            return None

    def _launch(self, values: dict) -> obsws.Client:
        if shutil.which("obs") is None:
            raise RecorderError("OBS Studio is not installed.")
        if other_obs_running():
            raise RecorderError("OBS is already open. Record from its own window, or close it and press again.")
        size = screen_size()
        prepare_config(self._choose_encoder(values), values, self.key(), size, videos_dir())
        ensure_openbox_rule()
        process = subprocess.Popen(
            ["obs", "--profile", PROFILE, "--collection", SCENE, "--minimize-to-tray", "--disable-shutdown-check"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        with open(pid_file(), "w", encoding="utf-8") as f:
            f.write(str(process.pid))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RecorderError("OBS closed right after starting (see ~/.config/obs-studio/logs).")
            client = self._client()
            if client is not None:
                try:
                    setup_scene(client, values, size)
                    return client
                except obsws.OBSError as error:  # the WebSocket answers before OBS has finished starting
                    client.close()
                    if "not ready" not in str(error).lower():
                        raise RecorderError(f"OBS could not be set up: {error}") from None
            time.sleep(0.3)
        self.quit_obs()
        raise RecorderError("OBS did not start in time.")

    def start(self) -> str:
        with self.lock:
            values = settings()
            client = self._client() or self._launch(values)
            try:
                if not client.call("GetRecordStatus")["outputActive"]:
                    client.call("StartRecord")
            except obsws.OBSError as error:
                raise RecorderError(f"OBS could not start recording: {error}") from None
            finally:
                client.close()
            return f"Recording started ({ENCODERS.get(self.encoder or values['encoder'], ('OBS',))[0]})."

    def stop(self) -> str:
        with self.lock:
            client = self._client()
            if client is None:
                return "Not recording."
            try:
                status = client.call("GetRecordStatus")
                path = client.call("StopRecord").get("outputPath", "") if status["outputActive"] else ""
            except obsws.OBSError as error:
                raise RecorderError(f"OBS could not stop recording: {error}") from None
            finally:
                client.close()
            self.quit_obs()  # if PERDIX started OBS for this recording, close it again
            return f"Saved {os.path.basename(path)}." if path else "Not recording."

    def toggle(self) -> str:
        return self.stop() if self.status().recording else self.start()

    def status(self) -> Status:
        client = self._client()
        if client is None:
            return Status(False)
        try:
            record = client.call("GetRecordStatus")
            stats = client.call("GetStats") if record["outputActive"] else {}
        except obsws.OBSError:
            return Status(False)
        finally:
            client.close()
        return Status(record["outputActive"], record.get("outputDuration", 0) / 1000, stats.get("activeFps", 0.0),
                      int(stats.get("renderSkippedFrames", 0) + stats.get("outputSkippedFrames", 0)),
                      int(stats.get("outputTotalFrames", 0)))

    @staticmethod
    def quit_obs() -> None:
        """Close the OBS that PERDIX started. OBS saves its settings and exits cleanly on SIGINT
        (SIGTERM kills it without saving). An OBS the user opened is left alone."""
        import psutil
        process = our_obs()
        if process is not None:
            try:
                process.send_signal(signal.SIGINT)
                process.wait(15)
            except psutil.TimeoutExpired:
                process.kill()
            except psutil.Error:
                pass
        try:
            os.remove(pid_file())
        except OSError:
            pass


def recordings(folder: str | None = None, limit: int = 20) -> list[tuple[str, float, int]]:
    """(path, modified time, size) of the newest recordings."""
    folder = folder or videos_dir()
    found = []
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        if name.lower().endswith((".mkv", ".mp4", ".mov", ".flv")):
            path = os.path.join(folder, name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            found.append((path, info.st_mtime, info.st_size))
    return sorted(found, key=lambda r: r[1], reverse=True)[:limit]


def notify(text: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "-a", "PERDIX", "-i", "media-record", "Game recording", text],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def hotkey_daemon() -> None:
    """Toggle recording on the hotkey for the whole session (started by kiosk-session)."""
    from kiosk_shell.services import keywatch
    ok, reason = keywatch.evdev_available()
    if not ok:
        print(f"recording hotkey disabled: {reason}", file=sys.stderr)
        return
    from evdev import ecodes
    recorder = Recorder()
    parent = os.getppid()
    busy = threading.Event()

    def toggle():
        try:
            notify(recorder.toggle())
        except RecorderError as error:
            notify(str(error))
        finally:
            busy.clear()

    hotkey = {"code": None, "read": 0.0}

    def hotkey_code() -> int | None:
        if time.monotonic() - hotkey["read"] > 5:  # pick up a key changed in the Control Center
            hotkey["code"], hotkey["read"] = ecodes.ecodes.get(settings()["hotkey"]), time.monotonic()
        return hotkey["code"]

    def on_event(code: int, pressed: bool) -> None:
        if pressed and not busy.is_set() and code == hotkey_code():
            busy.set()
            threading.Thread(target=toggle, daemon=True).start()

    try:
        keywatch.watch(on_event, lambda: os.getppid() != parent)
    finally:
        if recorder.status().recording:
            recorder.stop()
        recorder.quit_obs()


if __name__ == "__main__" and sys.argv[1:] == ["--hotkey"]:
    hotkey_daemon()
