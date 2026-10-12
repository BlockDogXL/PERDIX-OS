"""perf.log: one line of FPS and latency figures per game session.

MangoHud (with the PERDIX HUD config) logs every session to /var/log/perdix/mangohud/ as CSV.
collect() summarises each finished log into /var/log/perdix/perf.log:

  2026-10-12T21:04:11+03:00 game=cs2 duration=42m10s avg_fps=287.4 low1_fps=201.3 \
      frametime_avg_ms=3.48 frametime_p99_ms=4.97

"Latency" here is frame time: how long each frame took to render. Summarised logs are renamed
to *.csv.done and deleted after a week. The desktop calls collect() when Steam exits.
"""

import csv
import os
import time
from datetime import datetime

from kiosk_shell import paths

KEEP_DONE_SECONDS = 7 * 24 * 3600


def summarize(path: str) -> dict | None:
    """Figures for one MangoHud CSV, or None if it holds no frame data."""
    frametimes, fps_values = [], []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        columns = None
        for row in csv.reader(f):
            cells = [c.strip().lower() for c in row]
            if columns is None:
                if "fps" in cells and "frametime" in cells:
                    columns = (cells.index("fps"), cells.index("frametime"),
                               cells.index("elapsed") if "elapsed" in cells else None)
                    elapsed = []
                continue
            try:
                fps_values.append(float(row[columns[0]]))
                frametimes.append(float(row[columns[1]]))
                if columns[2] is not None:
                    elapsed.append(float(row[columns[2]]))
            except (ValueError, IndexError):
                continue
    frametimes = [t for t in frametimes if t > 0]
    if not frametimes:
        return None
    ordered = sorted(frametimes)
    p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
    duration = (max(elapsed) - min(elapsed)) / 1e9 if columns[2] is not None and len(elapsed) > 1 else 0.0
    return {
        "avg_fps": sum(fps_values) / len(fps_values) if fps_values else 1000 / (sum(frametimes) / len(frametimes)),
        "low1_fps": 1000 / p99,
        "frametime_avg_ms": sum(frametimes) / len(frametimes),
        "frametime_p99_ms": p99,
        "duration_s": duration,
    }


def game_name(filename: str) -> str:
    """'cs2_2026-10-12_21-04-11.csv' -> 'cs2' (MangoHud names logs <program>_<date>_<time>)."""
    stem = filename[: -len(".csv")] if filename.endswith(".csv") else filename
    parts = stem.split("_")
    return "_".join(parts[:-2]) if len(parts) > 2 else stem


def format_line(game: str, figures: dict, when: datetime) -> str:
    minutes, seconds = divmod(int(figures["duration_s"]), 60)
    return (f"{when.astimezone().isoformat(timespec='seconds')} game={game.replace(' ', '_')} "
            f"duration={minutes}m{seconds:02d}s avg_fps={figures['avg_fps']:.1f} "
            f"low1_fps={figures['low1_fps']:.1f} frametime_avg_ms={figures['frametime_avg_ms']:.2f} "
            f"frametime_p99_ms={figures['frametime_p99_ms']:.2f}")


def collect(log_dir: str = paths.MANGOHUD_LOG_DIR, perf_log: str = paths.PERF_LOG) -> int:
    """Summarise new MangoHud logs into perf.log. Returns how many sessions were added."""
    try:
        names = sorted(os.listdir(log_dir))
    except OSError:
        return 0
    added = []
    for name in names:
        path = os.path.join(log_dir, name)
        if name.endswith(".csv.done"):
            if time.time() - os.path.getmtime(path) > KEEP_DONE_SECONDS:
                os.remove(path)
            continue
        if not name.endswith(".csv") or name.endswith("_summary.csv"):
            continue
        try:
            figures = summarize(path)
        except OSError:
            continue
        if figures:
            added.append(format_line(game_name(name), figures, datetime.fromtimestamp(os.path.getmtime(path))))
        os.replace(path, path + ".done")
    if added:
        with open(perf_log, "a", encoding="utf-8") as f:
            f.write("\n".join(added) + "\n")
    return len(added)


def last_entries(count: int = 3, perf_log: str = paths.PERF_LOG) -> list[str]:
    try:
        with open(perf_log, encoding="utf-8") as f:
            return [line.rstrip("\n") for line in f.readlines()[-count:]]
    except OSError:
        return []
