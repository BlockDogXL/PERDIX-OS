"""Game recording: the obs-websocket 5 client against a stand-in server (handshake, password
challenge, requests, large and fragmented frames), the OBS profile PERDIX writes, the encoder choice,
the page, and (when OBS and Xvfb are installed) a real 60 fps recording through OBS."""

import base64
import configparser
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import threading
import time

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import obsws, recorder

app = QApplication.instance() or QApplication([])


# --- a stand-in obs-websocket server ----------------------------------------------------------------------


def server_frame(payload: bytes, opcode=0x1, final=True) -> bytes:
    head = bytes([(0x80 if final else 0) | opcode])
    n = len(payload)
    if n < 126:
        return head + bytes([n]) + payload
    if n < 1 << 16:
        return head + bytes([126]) + struct.pack("!H", n) + payload
    return head + bytes([127]) + struct.pack("!Q", n) + payload


def read_client_frame(conn) -> tuple[int, bytes]:
    def exact(n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data
    b1, b2 = exact(2)
    assert b2 & 0x80, "client frames must be masked"
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", exact(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", exact(8))[0]
    mask = exact(4)
    return b1 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(exact(n)))


class FakeOBS(threading.Thread):
    PASSWORD = "s3cret-password"

    def __init__(self, big=False):
        super().__init__(daemon=True)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.big = big
        self.requests = []
        self.recording = False

    def run(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            try:
                self.serve(conn)
            except (EOFError, OSError):
                pass
            conn.close()

    def serve(self, conn):
        head = b""
        while b"\r\n\r\n" not in head:
            head += conn.recv(4096)
        key = next(l.split(":", 1)[1].strip() for l in head.decode().split("\r\n") if l.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1((key + obsws.GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        salt, challenge = "c2FsdA==", "Y2hhbGxlbmdl"
        conn.sendall(server_frame(json.dumps({"op": 0, "d": {"obsWebSocketVersion": "5.3.4", "rpcVersion": 1,
                                                             "authentication": {"salt": salt, "challenge": challenge}}}).encode()))
        _op, payload = read_client_frame(conn)
        identify = json.loads(payload)["d"]
        if identify.get("authentication") != obsws.auth_answer(self.PASSWORD, salt, challenge):
            conn.sendall(server_frame(struct.pack("!H", 4009) + b"Authentication failed.", 0x8))
            return
        conn.sendall(server_frame(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}).encode()))
        conn.sendall(server_frame(b"", 0x9))  # a ping the client must answer
        while True:
            opcode, payload = read_client_frame(conn)
            if opcode == 0x8:
                return
            if opcode == 0xA:
                continue
            d = json.loads(payload)["d"]
            self.requests.append(d["requestType"])
            ok, data = True, {}
            if d["requestType"] == "GetRecordStatus":
                data = {"outputActive": self.recording, "outputDuration": 61500}
            elif d["requestType"] == "StartRecord":
                self.recording = True
            elif d["requestType"] == "StopRecord":
                self.recording, data = False, {"outputPath": "/home/kiosk/Videos/PERDIX/2026-10-12_21-00-00.mkv"}
            elif d["requestType"] == "GetStats":
                data = {"activeFps": 60.0, "renderSkippedFrames": 1, "outputSkippedFrames": 2, "outputTotalFrames": 3690}
            elif d["requestType"] == "GetVersion" and self.big:
                data = {"availableRequests": ["X" * 50] * 3000}  # > 64 KiB: 8-byte length
            elif d["requestType"] == "Bogus":
                ok = False
            reply = json.dumps({"op": 7, "d": {"requestType": d["requestType"], "requestId": d["requestId"],
                                               "requestStatus": {"result": ok, "code": 100 if ok else 204,
                                                                 "comment": None if ok else "Unknown request"},
                                               "responseData": data}}).encode()
            # send the reply in two fragments to exercise continuation frames
            half = len(reply) // 2
            conn.sendall(server_frame(reply[:half], 0x1, final=False) + server_frame(reply[half:], 0x0))

    def close(self):
        self.listener.close()


@pytest.fixture
def fake_obs():
    server = FakeOBS(big=True)
    server.start()
    yield server
    server.close()


def test_client_password_requests_and_errors(fake_obs):
    with pytest.raises(obsws.OBSError, match="refused the password"):
        obsws.Client("127.0.0.1", fake_obs.port, "wrong").connect()
    with obsws.Client("127.0.0.1", fake_obs.port, FakeOBS.PASSWORD) as client:
        assert client.version == "5.3.4"
        assert len(client.call("GetVersion")["availableRequests"]) == 3000
        assert client.call("StartRecord") == {}
        with pytest.raises(obsws.OBSError, match="Bogus failed: Unknown request"):
            client.call("Bogus")


def test_client_without_obs():
    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    number = port.getsockname()[1]
    port.close()
    with pytest.raises(obsws.OBSError, match="not running"):
        obsws.Client("127.0.0.1", number, "x", timeout=1).connect()


def test_frame_lengths():
    for n in (0, 125, 126, 65535, 65536):
        frame = obsws.encode_frame(b"a" * n)
        assert frame[0] == 0x81 and frame[1] & 0x80
        assert len(frame) == n + 2 + 4 + (2 if 126 <= n < 65536 else 8 if n >= 65536 else 0)


def test_recorder_toggles_through_obs(fake_obs, monkeypatch):
    monkeypatch.setattr(recorder, "PORT", fake_obs.port)
    monkeypatch.setattr(recorder, "password", lambda path=None: FakeOBS.PASSWORD)
    monkeypatch.setattr(recorder, "settings", lambda: dict(recorder.DEFAULTS))
    monkeypatch.setattr(recorder.Recorder, "quit_obs", staticmethod(lambda: None))
    r = recorder.Recorder()
    r.encoder = "ffmpeg_vaapi"
    assert r.toggle() == "Recording started (VA-API, AMD / Intel (GPU))."
    status = r.status()
    assert status.text() == "Recording 01:01 · 60 fps · 3 dropped" and status.total == 3690
    assert r.toggle() == "Saved 2026-10-12_21-00-00.mkv."
    assert r.status().text() == "Not recording."


# --- configuration and encoder choice ------------------------------------------------------------------------


def test_prepare_config(tmp_path):
    values = dict(recorder.DEFAULTS, fps=60, quality="max")
    recorder.prepare_config("ffmpeg_vaapi", values, "k" * 32, (2560, 1440), str(tmp_path / "Videos"), str(tmp_path))
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str
    ini.read(tmp_path / "basic" / "profiles" / "PERDIX" / "basic.ini")
    assert dict(ini["Video"]) == {"BaseCX": "2560", "BaseCY": "1440", "OutputCX": "2560", "OutputCY": "1440",
                                  "FPSType": "0", "FPSCommon": "60"}
    assert ini["Output"]["Mode"] == "Advanced" and ini["AdvOut"]["RecEncoder"] == "ffmpeg_vaapi"
    assert ini["AdvOut"]["RecFormat2"] == "mkv" and ini["AdvOut"]["RecFilePath"] == str(tmp_path / "Videos")
    assert "BaseCX=2560" in (tmp_path / "basic" / "profiles" / "PERDIX" / "basic.ini").read_text()  # OBS style
    encoder = json.loads((tmp_path / "basic" / "profiles" / "PERDIX" / "recordEncoder.json").read_text())
    assert encoder == {"rate_control": "CBR", "bitrate": 80000, "keyint_sec": 2}
    glob_ini = configparser.ConfigParser(interpolation=None)
    glob_ini.optionxform = str
    glob_ini.read(tmp_path / "global.ini")
    assert glob_ini["OBSWebSocket"]["AuthRequired"] == "true" and glob_ini["OBSWebSocket"]["ServerPassword"] == "k" * 32
    assert glob_ini["Basic"]["Profile"] == "PERDIX"
    ws = json.loads((tmp_path / "plugin_config" / "obs-websocket" / "config.json").read_text())
    assert ws["auth_required"] and ws["server_password"] == "k" * 32
    assert oct(os.stat(tmp_path / "global.ini").st_mode & 0o777) == "0o600"


def test_prepare_config_keeps_other_obs_settings(tmp_path):
    (tmp_path / "global.ini").write_text("[General]\nLastVersion=503316482\n\n[BasicWindow]\nGeometry=AAAA\n")
    recorder.prepare_config("obs_x264", dict(recorder.DEFAULTS), "k" * 32, (1920, 1080), str(tmp_path / "V"),
                            str(tmp_path))
    text = (tmp_path / "global.ini").read_text()
    assert "LastVersion=503316482" in text and "Geometry=AAAA" in text and "FirstRun=true" in text


def test_password_is_private_and_stable(tmp_path):
    path = str(tmp_path / "obs-websocket.key")
    first = recorder.password(path)
    assert len(first) >= 32 and recorder.password(path) == first
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_detect_encoder_prefers_the_gpu():
    registered = {"jim_nvenc", "ffmpeg_vaapi", "obs_x264"}
    has = registered.__contains__
    assert recorder.detect_encoder(has, lambda enc: enc == "h264_nvenc") == "jim_nvenc"
    assert recorder.detect_encoder(has, lambda enc: enc == "h264_vaapi") == "ffmpeg_vaapi"
    assert recorder.detect_encoder(has, lambda enc: False) == "obs_x264"
    tested = []
    recorder.detect_encoder(has, lambda enc: tested.append(enc) or False)
    assert tested == ["h264_nvenc", "h264_vaapi"]  # each GPU engine tested once
    assert recorder.detect_encoder({"obs_nvenc_h264_tex", "jim_nvenc"}.__contains__,
                                   lambda enc: True) == "obs_nvenc_h264_tex"


def test_obs_has_encoder(tmp_path):
    (tmp_path / "obs-ffmpeg.so").write_bytes(b"\x7fELF...hevc_ffmpeg_vaapi\x00...")
    dirs = (str(tmp_path),)
    assert recorder.obs_has_encoder("ffmpeg_vaapi", dirs)  # the linker shares the tail of a longer name
    assert recorder.obs_has_encoder("hevc_ffmpeg_vaapi", dirs)
    assert not recorder.obs_has_encoder("jim_nvenc", dirs)


def test_settings_are_validated(monkeypatch):
    monkeypatch.setattr(recorder.hub_settings, "load", lambda *a: {"recording": {
        "hotkey": "KEY_A", "fps": 144, "quality": "ultra", "microphone": False}})
    values = recorder.settings()
    assert (values["hotkey"], values["fps"], values["quality"], values["microphone"]) == ("KEY_F9", 60, "high", False)


def test_recordings_newest_first(tmp_path):
    for i, name in enumerate(("a.mkv", "b.mp4", "notes.txt", "c.mkv")):
        (tmp_path / name).write_bytes(b"x" * (i + 1))
        os.utime(tmp_path / name, (1000 + i, 1000 + i))
    assert [os.path.basename(p) for p, _, _ in recorder.recordings(str(tmp_path))] == ["c.mkv", "b.mp4", "a.mkv"]


def test_our_obs_ignores_other_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "perdix-obs.pid").write_text(str(os.getpid()))  # a pid that is not OBS
    assert recorder.our_obs() is None
    (tmp_path / "perdix-obs.pid").write_text("not a pid")
    assert recorder.our_obs() is None


# --- the page ----------------------------------------------------------------------------------------------------


@pytest.fixture
def page(monkeypatch):
    monkeypatch.setattr(recorder, "settings", lambda: dict(recorder.DEFAULTS))
    monkeypatch.setattr(recorder, "recordings", lambda folder=None, limit=20: [
        ("/home/kiosk/Videos/PERDIX/2026-10-12_21-00-00.mkv", 1791838800, 512 * 2 ** 20)])
    monkeypatch.setattr(recorder, "detect_encoder", lambda: "jim_nvenc")
    monkeypatch.setattr(recorder.Recorder, "status", lambda self: recorder.Status(False))
    from kiosk_shell.perdix_hub import RecordingPage
    page = RecordingPage()
    yield page
    page.shutdown()


def settle(page):
    for _ in range(4):
        for w in (page._worker, page._status_worker, getattr(page, "_detect_worker", None)):
            if w is not None:
                w.wait(5000)
        app.processEvents()


def test_page_shows_recording_state(page):
    page.show_status(recorder.Status(True, 75.0, 59.8, 2, 4500))
    assert page.state_value.value.text() == "● REC" and page.time_value.value.text() == "01:15"
    assert page.fps_value.value.text() == "60" and page.dropped_value.value.text() == "2"
    assert page.record_button.text() == "Stop recording"
    page.show_status(recorder.Status(False))
    assert page.state_value.value.text() == "READY" and page.record_button.text() == "Start recording"


def test_page_detects_encoder_and_lists_files(page):
    page.show()
    settle(page)
    assert page.encoder_note.text() == "Automatic uses NVIDIA NVENC (GPU)."
    assert page.files.item(0).text().startswith("2026-10-12_21-00-00.mkv") and "512.0 MB" in page.files.item(0).text()


def test_page_start_button(page, monkeypatch):
    monkeypatch.setattr(recorder.Recorder, "start", lambda self: "Recording started (NVIDIA NVENC (GPU)).")
    page.record_button.click()
    settle(page)
    assert page.message.text() == "Recording started (NVIDIA NVENC (GPU))."


def test_page_saves_settings(page, monkeypatch):
    saved = {}
    monkeypatch.setattr(recorder.hub_settings, "save_section", lambda name, values: saved.update({name: values}))
    page.hotkey.setCurrentIndex(page.hotkey.findData("KEY_F10"))
    assert saved["recording"]["hotkey"] == "KEY_F10" and saved["recording"]["fps"] == 60


def test_recording_is_in_the_media_group():
    from kiosk_shell.perdix_hub import NAV
    assert ("record", "rec", "Game Recording", "OBS · hotkey F9 · 60 fps", "Media") in NAV


# --- real OBS ------------------------------------------------------------------------------------------------------


@pytest.mark.skipif(not (shutil.which("obs") and shutil.which("Xvfb") and shutil.which("ffprobe")),
                    reason="needs OBS Studio, Xvfb and ffprobe")
def test_real_obs_records_60_fps(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    server = subprocess.Popen(["Xvfb", ":96", "-screen", "0", "1280x720x24", "-nolisten", "tcp", "-noreset"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name, value in (("HOME", str(home)), ("XDG_CONFIG_HOME", str(home / ".config")), ("DISPLAY", ":96"),
                        ("XDG_RUNTIME_DIR", str(tmp_path))):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(recorder.hub_settings, "HUB_SETTINGS_PATH", str(home / ".config/perdix-os/settings.json"))
    monkeypatch.setattr(recorder, "settings", lambda: dict(recorder.DEFAULTS, encoder="obs_x264", quality="standard"))
    time.sleep(1)
    r = recorder.Recorder()
    try:
        assert r.start() == "Recording started (x264 (CPU))."
        time.sleep(3)
        assert r.status().recording
        message = r.stop()
        assert message.startswith("Saved ") and recorder.our_obs() is None
    finally:
        r.quit_obs()
        server.terminate()
        server.wait()
    (path, _, _), = recorder.recordings()
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,width,height,r_frame_rate", "-of", "json", path],
                           capture_output=True, text=True).stdout
    stream = json.loads(probe)["streams"][0]
    assert (stream["codec_name"], stream["width"], stream["height"], stream["r_frame_rate"]) == ("h264", 1280, 720, "60/1")


def test_openbox_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder.shutil, "which", lambda name: None)  # no Openbox to reload here
    system = tmp_path / "system-rc.xml"
    system.write_text('<?xml version="1.0"?>\n<openbox_config xmlns="http://openbox.org/3.4/rc">\n'
                      "<focus><focusNew>yes</focusNew></focus>\n<applications>\n<!-- examples -->\n"
                      "</applications>\n</openbox_config>\n")
    user = tmp_path / "openbox" / "rc.xml"
    assert recorder.ensure_openbox_rule(str(user), str(system))
    text = user.read_text()
    assert text.index('<application class="obs">') < text.index("</applications>")
    for tag in ("<focus>no</focus>", "<iconic>yes</iconic>", "<layer>below</layer>"):
        assert tag in text
    assert not recorder.ensure_openbox_rule(str(user), str(system))  # only once
    assert user.read_text().count("PERDIX: OBS") == 1
    import xml.dom.minidom
    xml.dom.minidom.parseString(user.read_text())  # still valid XML
    assert not recorder.ensure_openbox_rule(str(tmp_path / "x" / "rc.xml"), str(tmp_path / "missing.xml"))


def test_hotkey_daemon_toggles_on_f9_only(monkeypatch):
    ecodes = pytest.importorskip("evdev").ecodes
    from kiosk_shell.services import keywatch
    toggles, notes = [], []
    monkeypatch.setattr(keywatch, "evdev_available", lambda: (True, "Ready."))
    monkeypatch.setattr(recorder, "settings", lambda: dict(recorder.DEFAULTS))
    monkeypatch.setattr(recorder, "notify", notes.append)
    monkeypatch.setattr(recorder.Recorder, "toggle",  # starting OBS takes a moment
                        lambda self: toggles.append(1) or time.sleep(0.3) or "Recording started.")
    monkeypatch.setattr(recorder.Recorder, "status", lambda self: recorder.Status(False))
    monkeypatch.setattr(recorder.Recorder, "quit_obs", staticmethod(lambda: None))

    def fake_watch(on_event, should_stop, code=None, poll=0.2):
        assert not should_stop()
        on_event(ecodes.KEY_A, True)  # other keys: ignored
        on_event(ecodes.KEY_F9, False)  # releases: ignored
        on_event(ecodes.KEY_F9, True)
        on_event(ecodes.KEY_F9, True)  # pressed again while the first toggle runs: ignored
        deadline = time.monotonic() + 5
        while not notes and time.monotonic() < deadline:
            time.sleep(0.01)
    monkeypatch.setattr(keywatch, "watch", fake_watch)
    recorder.hotkey_daemon()
    assert toggles == [1] and notes == ["Recording started."]
