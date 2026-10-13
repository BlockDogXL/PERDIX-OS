"""Camera & Mic: v4l2-ctl parsers (fixtures in v4l2-ctl 1.26's printf formats; no camera here), the
real ffmpeg preview pipe on a test pattern, the microphone level maths, and the page."""

import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import audio, camera, mictest

app = QApplication.instance() or QApplication([])
DATA = Path(__file__).parent / "data"


def text(name):
    return (DATA / name).read_text()


# --- camera parsers ---------------------------------------------------------------------------------------


def test_parse_list_devices():
    assert camera.parse_list_devices(text("v4l2-list-devices.txt")) == [
        ("HD Pro Webcam C920", ["/dev/video0", "/dev/video1"]),
        ("Integrated Camera: Integrated C", ["/dev/video2", "/dev/video3"])]


def test_parse_formats_and_best_modes():
    modes = camera.parse_formats(text("v4l2-formats-c920.txt"))
    assert camera.Mode("YUYV", 640, 480, 30.0) in modes and camera.Mode("MJPG", 1920, 1080, 30.0) in modes
    best = camera.best_modes(modes)
    # 1080p and 720p: MJPEG gives 30 fps where YUYV gives 5/10; 480p: same fps, MJPEG wins the tie
    assert [(m.width, m.height, m.fourcc, m.fps) for m in best] == [
        (1920, 1080, "MJPG", 30.0), (1280, 720, "MJPG", 30.0), (640, 480, "MJPG", 30.0)]
    assert best[0].label == "1920×1080 · 30 fps · MJPEG"


def test_parse_controls():
    controls = {c.name: c for c in camera.parse_controls(text("v4l2-ctrls-c920.txt"))}
    b = controls["brightness"]
    assert (b.kind, b.minimum, b.maximum, b.step, b.default, b.value, b.inactive) == ("int", 0, 255, 1, 128, 140, False)
    assert controls["white_balance_temperature"].inactive and controls["exposure_time_absolute"].inactive
    assert controls["power_line_frequency"].menu == {0: "Disabled", 1: "50 Hz", 2: "60 Hz"}
    assert controls["auto_exposure"].menu == {1: "Manual Mode", 3: "Aperture Priority Mode"}
    assert controls["auto_exposure"].value == 3 and controls["focus_automatic_continuous"].kind == "bool"
    assert controls["white_balance_automatic"].label == "White balance automatic"


class FakeCtl:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, timeout=8):
        self.calls.append(args)
        if args == ("--list-devices",):
            return text("v4l2-list-devices.txt")
        if args[-1] == "--list-formats-ext":
            return text("v4l2-formats-c920.txt") if args[1] in ("/dev/video0", "/dev/video2") else (
                "ioctl: VIDIOC_ENUM_FMT\n\tType: Video Capture\n\n")
        if args[-1] == "--list-ctrls-menus":
            return text("v4l2-ctrls-c920.txt")
        return ""


@pytest.fixture
def ctl(monkeypatch):
    fake = FakeCtl()
    monkeypatch.setattr(camera, "run", fake)
    return fake


def test_cameras_skip_metadata_nodes(ctl):
    found = camera.cameras()
    assert [(c.name, c.path) for c in found] == [("HD Pro Webcam C920", "/dev/video0"),
                                                 ("Integrated Camera: Integrated C", "/dev/video2")]
    assert ("-d", "/dev/video1", "--list-formats-ext") not in ctl.calls  # first node already had formats


def test_set_and_reset_controls(ctl):
    camera.set_control("/dev/video0", "brightness", 150)
    assert ctl.calls[-1] == ("-d", "/dev/video0", "--set-ctrl=brightness=150")
    for bad_name, bad_path in (("brightness;reboot", "/dev/video0"), ("brightness", "/etc/passwd")):
        with pytest.raises(camera.CameraError):
            camera.set_control(bad_path, bad_name, 1)
    ctl.calls.clear()
    current = camera.controls("/dev/video0")
    current[-2].value = 1  # focus_automatic_continuous changed by hand... (default 1: unchanged)
    current[0].value = 140  # brightness 140, default 128
    message = camera.reset_controls("/dev/video0", current)
    assert message == "Camera settings are back to their defaults."
    assert ctl.calls[1:] == [("-d", "/dev/video0", "--set-ctrl=brightness=128")]


def test_preview_command():
    mode = camera.Mode("MJPG", 1920, 1080, 30.0)
    argv = camera.preview_command("/dev/video0", mode)
    assert argv[:6] == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2"]
    assert argv[argv.index("-input_format") + 1] == "mjpeg" and argv[argv.index("-video_size") + 1] == "1920x1080"
    assert argv[-5:] == ["-pix_fmt", "rgb24", "-f", "rawvideo", "-"]


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg is needed")
def test_preview_thread_with_real_ffmpeg():
    """The camera input swapped for ffmpeg's test pattern: frames arrive at the preview size."""
    from kiosk_shell.perdix_hub import PreviewThread
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i",
            "testsrc2=size=1280x720:rate=30"] + camera.output_args()
    thread = PreviewThread(argv, camera.PREVIEW_SIZE)
    frames = []
    thread.frame.connect(frames.append)
    thread.start()
    deadline = time.monotonic() + 10
    while len(frames) < 3 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    thread.stop()
    assert len(frames) >= 3 and (frames[0].width(), frames[0].height()) == camera.PREVIEW_SIZE
    assert not thread.isRunning() and thread.process.poll() is not None


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg is needed")
def test_preview_thread_reports_a_busy_camera():
    from kiosk_shell.perdix_hub import PreviewThread
    argv = [sys.executable, "-c", "import sys; sys.stderr.write('/dev/video0: Device or resource busy\\n')"]
    thread = PreviewThread(argv, camera.PREVIEW_SIZE)
    errors = []
    thread.failed.connect(errors.append)
    thread.start()
    thread.wait(5000)
    app.processEvents()
    assert errors == ["The camera is in use by another app."]


# --- microphone -------------------------------------------------------------------------------------------------


def tone(amplitude, n=2400):
    return (amplitude * 32767 * np.sin(np.linspace(0, 40 * np.pi, n))).astype("<i2")


def test_level_db():
    peak, rms = mictest.level_db(tone(0.125))
    assert peak == pytest.approx(-18.06, abs=0.05) and rms == pytest.approx(-21.07, abs=0.05)
    assert mictest.level_db(np.zeros(100, dtype="<i2")) == (mictest.SILENCE_DB, mictest.SILENCE_DB)
    assert mictest.level_db(np.array([], dtype="<i2")) == (mictest.SILENCE_DB, mictest.SILENCE_DB)


def test_advice_and_gain_db():
    assert [mictest.advice(db) for db in (-60, -40, -25, -10, -1)] == [
        "Nothing heard. Is the right microphone selected and unmuted?", "Very quiet: raise the gain or move closer.",
        "A bit quiet for voice chat: raise the gain a little.", "Good level.", "Too loud, it will distort: lower the gain."]
    assert mictest.gain_db(1.0) == 0 and mictest.gain_db(0.5) == pytest.approx(-18.06, abs=0.01)
    assert mictest.gain_db(1.5) == pytest.approx(10.57, abs=0.01)


def test_set_gain_clamps(monkeypatch):
    calls = []
    monkeypatch.setattr(audio, "run", lambda *argv, **kw: calls.append(argv) or "")
    mictest.set_gain(2.0, "57")
    mictest.set_gain(-1, "57")
    assert calls == [("wpctl", "set-volume", "57", "1.50"), ("wpctl", "set-volume", "57", "0.00")]


def test_record_sample_stops_after_the_time(monkeypatch, tmp_path):
    """A stand-in pw-record that writes a WAV until SIGTERM, like the real one."""
    script = tmp_path / "pw-record"
    script.write_text(f"#!{sys.executable}\n" + """import signal, sys, wave
path = sys.argv[-1]
w = wave.open(path, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
def stop(*_):
    w.close(); sys.exit(0)
signal.signal(signal.SIGTERM, stop)
import time
while True:
    w.writeframes(b"\\x00\\x10" * 4800); time.sleep(0.1)
""")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    started = time.monotonic()
    path = mictest.record_sample("usb-mic", 1, str(tmp_path / "sample.wav"))
    assert 1 <= time.monotonic() - started < 4
    with wave.open(path) as w:
        assert w.getframerate() == 48000 and w.getnframes() >= 4800


def test_record_command():
    assert mictest.record_command("alsa_input.usb-mic") == [
        "pw-record", "--target", "alsa_input.usb-mic", "--rate", "48000", "--channels", "1", "--format", "s16",
        "-P", "media.name=PERDIX microphone test", "-"]


# --- the page -------------------------------------------------------------------------------------------------------


@pytest.fixture
def page(ctl, monkeypatch, tmp_path):
    mics = [audio.Device(57, "alsa_input.usb-mic", "USB Mic", True), audio.Device(58, "perdix-stream-mix",
                                                                                 "PERDIX Stream Mix", False)]
    monkeypatch.setattr(audio, "devices", lambda kind: mics)
    monkeypatch.setattr(audio, "get_volume", lambda target: audio.Volume(0.8, False))
    # The meter reads a stand-in recorder that sends a -18 dBFS tone.
    feeder = tmp_path / "feed.py"
    feeder.write_text("import sys, numpy as np, time\n"
                      "x = (0.125 * 32767 * np.sin(np.linspace(0, 40 * np.pi, 2400))).astype('<i2').tobytes()\n"
                      "while True:\n    sys.stdout.buffer.write(x); sys.stdout.flush(); time.sleep(0.05)\n")
    monkeypatch.setattr(mictest, "start_meter", lambda target: subprocess.Popen(
        [sys.executable, str(feeder)], stdout=subprocess.PIPE))
    from kiosk_shell.perdix_hub import CameraMicPage
    page = CameraMicPage()
    yield page
    page.shutdown()


def settle(page, seconds=0.3):
    if page._worker is not None:
        page._worker.wait(5000)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def test_page_lists_cameras_modes_and_controls(page):
    page.show()
    settle(page)
    assert [page.camera_combo.itemText(i) for i in range(page.camera_combo.count())] == [
        "HD Pro Webcam C920", "Integrated Camera: Integrated C"]
    assert page.mode_combo.itemText(0) == "1920×1080 · 30 fps · MJPEG"
    assert page.control_widget("brightness").value() == 140
    assert not page.control_widget("exposure_time_absolute").isEnabled()  # auto exposure is on
    assert page.control_widget("power_line_frequency").currentText() == "60 Hz"


def test_page_changes_a_control(page, ctl):
    page.show()
    settle(page)
    page.control_widget("brightness").setValue(170)
    page.control_widget("brightness").setValue(180)  # only the last value is sent
    settle(page, 0.4)
    page._control_worker.wait(5000)
    sent = [c for c in ctl.calls if c[-1].startswith("--set-ctrl")]
    assert sent == [("-d", "/dev/video0", "--set-ctrl=brightness=180")]


def test_page_meter_and_gain(page, monkeypatch):
    gains = []
    monkeypatch.setattr(mictest, "set_gain", lambda level, target: gains.append((level, target)))
    page.show()
    settle(page, 0.6)
    assert page.mic_combo.currentText() == "USB Mic  (default)" and page.gain.value() == 80
    assert page.meter.peak == pytest.approx(-18.06, abs=0.1) and page.advice.text() == "Good level."
    page.gain.slider.setValue(120)
    settle(page, 0.3)
    assert gains == [(1.2, "57")]
    page.hide()
    assert page.meter_thread is None and page.preview_thread is None


def test_page_without_camera(page, monkeypatch):
    monkeypatch.setattr(camera, "cameras", lambda: [])
    page.show()
    settle(page)
    assert page.camera_status.text() == "No camera found. Plug in a USB webcam."
    assert not page.preview_button.isEnabled() and page.picture.isHidden()


def test_camera_mic_is_in_the_media_group():
    from kiosk_shell.perdix_hub import NAV
    assert ("cammic", "camera", "Camera & Mic", "Preview · level · gain", "Media") in NAV
