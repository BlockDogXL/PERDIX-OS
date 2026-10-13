"""Lossless video cutting and joining, the LosslessCut way: ffmpeg copies the compressed video and
sound as they are (`-c copy`), so nothing is re-encoded, quality stays identical and an hour of
recording is cut in seconds.

The one rule of copying: a cut can only start on a keyframe (a picture stored whole; the frames
after it are stored as changes). ffmpeg starts a copied cut at the keyframe at or before the chosen
time, so the page shows the keyframes and snaps the start to them.

Joining uses ffmpeg's concat demuxer, which needs every file to have the same kind of video and
sound (codec, size, frame rate, sample rate, channels); different files are refused with the reason.
"""

from __future__ import annotations

import bisect
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

CONTAINERS = {"same": "", "mp4": ".mp4", "mkv": ".mkv"}


class CutError(Exception):
    """A cut or join that cannot be done, with a message meant for the user."""


def _run(argv: list[str], timeout: float = 600) -> str:
    if shutil.which(argv[0]) is None:
        raise CutError(f"{argv[0]} is not installed (ffmpeg).")
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CutError(f"{argv[0]} took too long.") from None
    if result.returncode != 0:
        lines = (result.stderr or "").strip().splitlines()
        raise CutError(lines[-1] if lines else f"{argv[0]} failed.")
    return result.stdout


# --- reading a video ----------------------------------------------------------------------------------


@dataclass
class Media:
    path: str
    duration: float
    size: int
    video_codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    start: float = 0.0  # timestamp of the first frame; positions on the page are timestamps
    keyframes: list[float] = field(default_factory=list)
    keyframe_dts: dict[float, float] = field(default_factory=dict)  # keyframe time -> its decode time

    def summary(self) -> str:
        parts = [f"{self.width}×{self.height}" if self.width else "", f"{self.fps:g} fps" if self.fps else "",
                 self.video_codec.upper(), self.audio_codec.upper(), clock(self.duration)]
        return " · ".join(p for p in parts if p)

    def compatibility(self) -> tuple:
        """What has to match for files to be joined without re-encoding."""
        return (self.video_codec, self.width, self.height, round(self.fps, 2), self.audio_codec, self.sample_rate,
                self.channels)


def _rate(text: str) -> float:
    num, _, den = (text or "0/1").partition("/")
    try:
        return float(num) / float(den or 1) if float(den or 1) else 0.0
    except ValueError:
        return 0.0


def parse_probe(path: str, text: str) -> Media:
    data = json.loads(text or "{}")
    fmt = data.get("format") or {}
    media = Media(path, float(fmt.get("duration") or 0), int(fmt.get("size") or 0))
    media.start = float(fmt.get("start_time") or 0)
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and not media.video_codec and not (
                stream.get("disposition") or {}).get("attached_pic"):
            media.video_codec = stream.get("codec_name", "")
            media.width, media.height = int(stream.get("width") or 0), int(stream.get("height") or 0)
            media.fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate"))
            if stream.get("start_time") not in (None, "N/A"):
                media.start = float(stream["start_time"])  # the container's can be earlier (audio priming)
            if not media.duration:
                media.duration = float(stream.get("duration") or 0)
        elif stream.get("codec_type") == "audio" and not media.audio_codec:
            media.audio_codec = stream.get("codec_name", "")
            media.sample_rate = int(stream.get("sample_rate") or 0)
            media.channels = int(stream.get("channels") or 0)
    return media


def parse_keyframes(text: str) -> tuple[list[float], dict[float, float]]:
    """`ffprobe -show_entries packet=pts_time,dts_time,flags -of csv=p=0`: 'pts,dts,K__' lines for
    keyframes -> (keyframe times, keyframe time -> decode time)."""
    times, dts = [], {}
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3 or "K" not in parts[2]:
            continue
        try:
            pts = float(parts[0])
        except ValueError:
            continue
        times.append(pts)
        try:
            dts[round(pts, 3)] = float(parts[1])
        except ValueError:
            pass
    return sorted(set(times)), dts


def probe(path: str) -> Media:
    if not os.path.isfile(path):
        raise CutError(f"File not found: {path}")
    media = parse_probe(path, _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                                   timeout=60))
    if not media.video_codec:
        raise CutError("This file has no video.")
    media.keyframes, media.keyframe_dts = parse_keyframes(_run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,dts_time,flags",
         "-of", "csv=p=0", path], timeout=300))
    return media


def clock(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return (f"{int(hours)}:" if hours >= 1 else "") + f"{int(minutes):02d}:{secs:06.3f}"


# --- keyframes and segments ---------------------------------------------------------------------------------


def keyframe_at_or_before(keyframes: list[float], t: float) -> float:
    if not keyframes:
        return 0.0
    i = bisect.bisect_right(keyframes, t + 1e-6)
    return keyframes[max(0, i - 1)]


def next_keyframe(keyframes: list[float], t: float) -> float | None:
    i = bisect.bisect_right(keyframes, t + 1e-3)
    return keyframes[i] if i < len(keyframes) else None


def previous_keyframe(keyframes: list[float], t: float) -> float:
    i = bisect.bisect_left(keyframes, t - 1e-3)
    return keyframes[max(0, i - 1)] if keyframes else 0.0


@dataclass
class Segment:
    start: float
    end: float

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


def snapped(media: Media, segments: list[Segment]) -> list[Segment]:
    """Segments as a lossless cut really makes them: starts on keyframes, inside the video, in order."""
    result = []
    for s in sorted(segments, key=lambda s: s.start):
        if media.duration and s.start >= media.start + media.duration:
            continue  # past the end of the video
        start = keyframe_at_or_before(media.keyframes, max(media.start, s.start))
        end = min(s.end, media.start + media.duration) if media.duration else s.end
        if end - start > 0.05:
            result.append(Segment(start, end))
    return result


# --- output ---------------------------------------------------------------------------------------------------


def output_path(source: str, suffix: str, container: str = "same", folder: str | None = None) -> str:
    stem, ext = os.path.splitext(os.path.basename(source))
    ext = CONTAINERS.get(container) or ext or ".mkv"
    folder = folder or os.path.dirname(os.path.abspath(source))
    path = os.path.join(folder, f"{stem}-{suffix}{ext}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem}-{suffix}-{n}{ext}")
        n += 1
    return path


def cut_command(source: str, segment: Segment, out: str, media: "Media | None" = None) -> list[str]:
    """A copied cut that begins exactly on the keyframe at `segment.start` and ends at `segment.end`.

    Measured with ffmpeg 6.1: `-ss` before `-i` alone begins one keyframe early in video with B-frames
    (ffmpeg seeks a fixed 3/23 s further back); `-ss` after `-i` with stream copy begins at the first
    keyframe whose decode time is not before it. So: a fast input seek to well before the keyframe,
    original timestamps kept (-copyts), then the output trimmed from just before the keyframe's decode
    time (-ss) to the end (-to)."""
    keyframes = media.keyframes if media else []
    first = not keyframes or segment.start <= keyframes[0] + 1e-6
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if not first:
        argv += ["-ss", f"{max(0.0, segment.start - 3.0):.3f}"]
    argv += ["-i", source, "-copyts"]
    if not first:
        dts = media.keyframe_dts.get(round(segment.start, 3), segment.start) if media else segment.start
        argv += ["-ss", f"{min(dts, segment.start) - 0.001:.3f}"]
    argv += ["-to", f"{segment.end:.3f}", "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
             "-avoid_negative_ts", "make_zero"]
    if out.lower().endswith(".mp4"):
        argv += ["-movflags", "+faststart"]
    return argv + [out]


def concat_list(paths: list[str]) -> str:
    """The concat demuxer's list file; a ' inside a name is written as '\\''."""
    return "".join("file '" + os.path.abspath(p).replace("'", "'\\''") + "'\n" for p in paths)


def concat_command(list_file: str, out: str) -> list[str]:
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0",
            "-i", list_file, "-map", "0:v:0", "-map", "0:a?", "-c", "copy"]
    if out.lower().endswith(".mp4"):
        argv += ["-movflags", "+faststart"]
    return argv + [out]


def check_joinable(items: list[Media]) -> None:
    first = items[0]
    names = ("video codec", "width", "height", "frame rate", "sound codec", "sample rate", "sound channels")
    for other in items[1:]:
        for name, a, b in zip(names, first.compatibility(), other.compatibility()):
            if a != b:
                raise CutError(f"{os.path.basename(other.path)} differs from {os.path.basename(first.path)} in "
                               f"{name} ({b} vs {a}); such files can only be joined by re-encoding.")


def export(media: Media, segments: list[Segment], join: bool, container: str = "same",
           folder: str | None = None) -> list[str]:
    """Keep `segments` of the video: one file per segment, or all of them joined into one file."""
    parts = snapped(media, segments)
    if not parts:
        raise CutError("Mark at least one part to keep.")
    stamp = time.strftime("%H%M%S")
    if not join or len(parts) == 1:
        outs = []
        for i, part in enumerate(parts, 1):
            out = output_path(media.path, f"cut{i if len(parts) > 1 else ''}-{stamp}", container, folder)
            _run(cut_command(media.path, part, out, media))
            outs.append(out)
        return outs
    out = output_path(media.path, f"edit-{stamp}", container, folder)
    ext = os.path.splitext(out)[1]
    with tempfile.TemporaryDirectory(prefix="perdix-cut-", dir=os.path.dirname(out)) as tmp:
        pieces = []
        for i, part in enumerate(parts):
            piece = os.path.join(tmp, f"part{i:03d}{ext}")
            _run(cut_command(media.path, part, piece, media))
            pieces.append(piece)
        list_file = os.path.join(tmp, "list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            f.write(concat_list(pieces))
        _run(concat_command(list_file, out))
    return [out]


def join(paths: list[str], container: str = "same", folder: str | None = None) -> str:
    """Join whole files end to end without re-encoding."""
    if len(paths) < 2:
        raise CutError("Choose at least two videos to join.")
    items = [probe(p) for p in paths]
    check_joinable(items)
    out = output_path(paths[0], f"joined-{time.strftime('%H%M%S')}", container, folder)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(concat_list(paths))
        list_file = f.name
    try:
        _run(concat_command(list_file, out))
    finally:
        os.unlink(list_file)
    return out


def frame_png(path: str, t: float, width: int = 640) -> bytes:
    """One picture at `t` seconds (fast seek), scaled to `width`, as PNG bytes."""
    if shutil.which("ffmpeg") is None:
        raise CutError("ffmpeg is not installed.")
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}",
                             "-i", path, "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe",
                             "-vcodec", "png", "-"], capture_output=True, timeout=30)
    if result.returncode != 0 or not result.stdout:
        raise CutError("Could not show this moment of the video.")
    return result.stdout


def play_command(path: str, start: float) -> list[str]:
    return ["ffplay", "-hide_banner", "-loglevel", "error", "-autoexit", "-ss", f"{max(0.0, start):.3f}",
            "-window_title", f"PERDIX · {os.path.basename(path)}", path]
