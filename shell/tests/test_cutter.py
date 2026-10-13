"""Video Cutter: lossless cuts and joins with real ffmpeg on generated clips (H.264 + AAC, a keyframe
every second, B-frames on). "Lossless" is checked packet by packet: the first video packet of a cut
is byte-identical to the source's keyframe."""

import json
import shutil
import subprocess
import time

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import cutter

app = QApplication.instance() or QApplication([])
pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


def make_clip(path, seconds=6, size="320x180", rate=30, freq=440):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate={rate}", "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000",
                    "-t", str(seconds), "-c:v", "libx264", "-g", str(rate), "-keyint_min", str(rate), "-sc_threshold", "0",
                    "-bf", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", str(path)], check=True)
    return str(path)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    return make_clip(tmp_path_factory.mktemp("cut") / "game's clip.mkv")


def packets(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets", "-show_data_hash", "MD5",
                          "-show_entries", "packet=pts_time,flags,data_hash", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return [line.split(",") for line in out.splitlines()]


def decodes_cleanly(path):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"], capture_output=True,
                          text=True).stderr == ""


def test_probe(clip):
    m = cutter.probe(clip)
    assert (m.video_codec, m.width, m.height, m.fps, m.audio_codec, m.sample_rate, m.channels) == (
        "h264", 320, 180, 30.0, "aac", 48000, 2)
    assert m.keyframes == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0] and 5.9 < m.duration < 6.2
    assert m.keyframe_dts[2.0] < 2.0  # B-frames: a keyframe is decoded before it is shown
    assert m.summary().startswith("320×180 · 30 fps · H264 · AAC")


def test_keyframe_helpers():
    keys = [0.0, 1.0, 2.0, 3.0]
    assert cutter.keyframe_at_or_before(keys, 2.4) == 2.0 and cutter.keyframe_at_or_before(keys, 2.0) == 2.0
    assert cutter.next_keyframe(keys, 2.0) == 3.0 and cutter.next_keyframe(keys, 3.0) is None
    assert cutter.previous_keyframe(keys, 2.0) == 1.0 and cutter.previous_keyframe(keys, 2.5) == 2.0
    assert cutter.clock(3725.5) == "1:02:05.500" and cutter.clock(65.25) == "01:05.250"


@pytest.mark.parametrize("start,end", [(2.4, 4.0), (0.0, 1.5), (5.0, 6.0), (1.0, 5.5)])
def test_cut_begins_exactly_on_the_keyframe(clip, tmp_path, start, end):
    m = cutter.probe(clip)
    out, = cutter.export(m, [cutter.Segment(start, end)], join=False, folder=str(tmp_path))
    source_keys = {round(float(p[0]), 3): p[2] for p in packets(clip) if "K" in p[1]}
    keyframe = cutter.keyframe_at_or_before(m.keyframes, start)
    cut = packets(out)
    assert cut[0][2] == source_keys[keyframe]  # byte-identical: copied, not re-encoded
    expected = round((min(end, m.duration) - keyframe) * 30)
    assert expected <= len(cut) <= expected + 3  # at most a couple of reordered frames more
    assert decodes_cleanly(out)


def test_parts_joined_into_one_mp4(clip, tmp_path):
    m = cutter.probe(clip)
    out, = cutter.export(m, [cutter.Segment(3.0, 5.0), cutter.Segment(0.0, 1.5)], join=True, container="mp4",
                         folder=str(tmp_path))
    assert out.endswith(".mp4") and "edit-" in out
    o = cutter.probe(out)
    assert 3.4 < o.duration < 3.9 and o.video_codec == "h264" and decodes_cleanly(out)
    assert sorted(p.name for p in tmp_path.iterdir()) == [out.rsplit("/", 1)[1]]  # temporary parts removed


def test_join_whole_files(clip, tmp_path):
    out = cutter.join([clip, clip], folder=str(tmp_path))
    assert 11.8 < cutter.probe(out).duration < 12.4 and decodes_cleanly(out)


def test_join_refuses_different_videos(clip, tmp_path):
    other = make_clip(tmp_path / "other.mkv", seconds=2, size="640x360")
    with pytest.raises(cutter.CutError, match="differs from .* in width"):
        cutter.join([clip, other], folder=str(tmp_path))


def test_concat_list_quotes_names():
    assert cutter.concat_list(["/v/it's.mkv"]) == "file '/v/it'\\''s.mkv'\n"


def test_output_path_never_overwrites(tmp_path):
    (tmp_path / "a-cut.mkv").write_text("x")
    assert cutter.output_path(str(tmp_path / "a.mkv"), "cut") == str(tmp_path / "a-cut-2.mkv")
    assert cutter.output_path(str(tmp_path / "a.mkv"), "cut", "mp4") == str(tmp_path / "a-cut.mp4")


def test_nothing_to_keep(clip):
    with pytest.raises(cutter.CutError, match="Mark at least one part"):
        cutter.export(cutter.probe(clip), [cutter.Segment(7.0, 8.0)], join=False)


def test_frame_png(clip):
    data = cutter.frame_png(clip, 2.5, 160)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_parse_probe_skips_cover_art():
    text = json.dumps({"format": {"duration": "10.0", "size": "100", "start_time": "0.5"}, "streams": [
        {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        {"codec_type": "video", "codec_name": "hevc", "width": 2560, "height": 1440, "avg_frame_rate": "60000/1001"},
        {"codec_type": "audio", "codec_name": "opus", "sample_rate": "48000", "channels": 2}]})
    m = cutter.parse_probe("x.mkv", text)
    assert (m.video_codec, m.width, round(m.fps, 2), m.audio_codec, m.start) == ("hevc", 2560, 59.94, "opus", 0.5)


# --- the page ------------------------------------------------------------------------------------------------


@pytest.fixture
def page():
    from kiosk_shell.perdix_hub import CutterPage
    page = CutterPage()
    yield page
    page.shutdown()


def settle(page, seconds=0.5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for w in (page._worker, page._frame_worker):
            if w is not None:
                w.wait(10000)
        app.processEvents()
        time.sleep(0.01)


def test_page_marks_and_exports(page, clip, monkeypatch, tmp_path):
    page.load(clip)
    settle(page)
    assert page.media is not None and "6 keyframes" in page.file_label.text()
    assert page.preview.pixmap() is not None and not page.preview.pixmap().isNull()
    page.seek(2.6)
    page.mark_start()
    assert page.mark_in == 2.0 and "moved back to the keyframe at 00:02.000" in page.status.text()
    page.seek(4.0)
    page.mark_end()
    page.keep_part()
    page.jump(1)
    assert page.position == 5.0
    page.mark_start()
    page.seek(5.8)
    page.mark_end()
    page.keep_part()
    assert [(p.start, p.end) for p in page.parts] == [(2.0, 4.0), (5.0, 5.8)]
    assert page.parts_list.count() == 2 and page.timeline.parts == page.parts
    calls = []
    monkeypatch.setattr(cutter, "export", lambda media, parts, join, container: calls.append(
        (parts, join, container)) or [str(tmp_path / "x-edit.mkv")])
    page.export_button.click()
    settle(page)
    assert calls == [(page.parts, True, "same")] and page.status.text() == "Saved: x-edit.mkv"


def test_page_end_before_start_is_refused(page, clip):
    page.load(clip)
    settle(page)
    page.seek(3.0)
    page.mark_start()
    page.seek(1.0)
    page.mark_end()
    assert page.status.text() == "The end has to be after the start." and page.mark_out > page.mark_in


def test_recording_page_opens_the_cutter(clip, monkeypatch):
    from kiosk_shell import perdix_hub
    from kiosk_shell.services import recorder
    monkeypatch.setattr(recorder, "recordings", lambda folder=None, limit=20: [(clip, 0, 1)])
    monkeypatch.setattr(recorder.Recorder, "status", lambda self: recorder.Status(False))
    window = perdix_hub.HubWindow()
    try:
        window.record.fill_files()
        window.record.files.setCurrentRow(0)
        window.record.cut_button.click()
        settle(window.cutter)
        assert window.pages.currentIndex() == [e[0] for e in perdix_hub.NAV].index("cutter")
        assert window.cutter.media is not None and window.cutter.media.path == clip
    finally:
        window.close()


def test_cutter_is_in_the_media_group():
    from kiosk_shell.perdix_hub import NAV
    assert ("cutter", "scissors", "Video Cutter", "Lossless cut · join", "Media") in NAV
