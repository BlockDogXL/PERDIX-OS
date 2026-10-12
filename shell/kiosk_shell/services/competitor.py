"""ULTRA FPS / COMPETITOR MODE: GameMode on, CPU governor 'performance', low-latency game env.

On:  1. starts `gamemoded -r` (keeps GameMode requested until it is stopped),
     2. `cpupower frequency-set -g performance` through perdix-tune (needs root),
     3. pauses background services and timers (virus definition downloads, index rebuilds…);
        perdix-tune logs which ones to /var/log/perdix/game_mode.log,
     4. writes ~/.config/perdix-os/competitor.env, which the desktop adds to Steam's
        environment the next time it starts Steam (games inherit it).
Off: stops that gamemoded, restores the previous governor, restarts the paused services and
removes the env file. The on/off state is also recorded in /etc/perdix/perdix.conf.

The state (gamemoded PID, previous governor, boot ID) is kept in
~/.config/perdix-os/competitor.json, so the mode survives closing the Control Center but not
a reboot.
"""

import json
import os
import signal
import subprocess

from kiosk_shell import paths
from kiosk_shell.services import tuning

STATE_PATH = paths.COMPETITOR_STATE
ENV_PATH = paths.COMPETITOR_ENV
BOOT_ID_FILE = "/proc/sys/kernel/random/boot_id"

# PERDIX runs Xorg + Openbox with no compositor, so games already draw straight to the
# screen. These variables cover what is left: no vsync wait in Mesa and NVIDIA OpenGL, and
# SDL games asking any compositor (should one ever run) to step aside.
COMPETITOR_ENV = {
    "vblank_mode": "0",                              # Mesa: don't wait for vblank
    "__GL_SYNC_TO_VBLANK": "0",                      # NVIDIA: don't wait for vblank
    "SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR": "1",  # SDL: set _NET_WM_BYPASS_COMPOSITOR
}


def _load_state(path: str = STATE_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_gamemoded(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip() == "gamemoded"
    except OSError:
        return False


def is_active(path: str = STATE_PATH) -> bool:
    """On, and turned on during this boot (a reboot ends game mode)."""
    state = _load_state(path)
    return bool(state) and state.get("boot_id") == tuning.read_first_line(BOOT_ID_FILE) and os.path.exists(ENV_PATH)


def game_env() -> dict[str, str]:
    """Variables to add to Steam's environment: Competitor Mode's while it is on, else none."""
    from kiosk_shell.config import read_shell_vars
    return read_shell_vars(ENV_PATH) if is_active() else {}


def write_env(path: str = ENV_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Written by PERDIX Control Center: Competitor Mode. Added to Steam's environment.\n")
        f.writelines(f"{key}={value}\n" for key, value in COMPETITOR_ENV.items())


def activate() -> list[tuple[str, bool, str]]:
    """Turn the mode on. Returns (step, ok, message) for each step."""
    results = []
    state = _load_state()
    pid = state.get("gamemoded_pid")
    if not _is_gamemoded(pid):
        try:
            process = subprocess.Popen(["gamemoded", "-r"], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
            pid = process.pid
            results.append(("GameMode", True, "requested (gamemoded -r)"))
        except FileNotFoundError:
            pid = None
            results.append(("GameMode", False, "gamemoded is not installed"))
    else:
        results.append(("GameMode", True, "already requested"))

    previous = state.get("previous_governor") or tuning.current_governor()
    ok, message = tuning.run_root("governor", "performance")
    results.append(("CPU governor", ok, "performance" if ok else message))

    ok, message = tuning.run_root("services", "pause")
    results.append(("Background services", ok, "paused (see game_mode.log)" if ok else message))

    write_env()
    results.append(("Game environment", True, "vsync off, compositor bypass (next Steam start)"))
    tuning.run_root("game-mode", "on")

    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"gamemoded_pid": pid, "previous_governor": previous,
                   "boot_id": tuning.read_first_line(BOOT_ID_FILE)}, f)
    return results


def deactivate() -> list[tuple[str, bool, str]]:
    results = []
    state = _load_state()
    pid = state.get("gamemoded_pid")
    if _is_gamemoded(pid):
        os.kill(pid, signal.SIGTERM)
        results.append(("GameMode", True, "request ended"))
    previous = state.get("previous_governor")
    if previous and previous != "performance":
        ok, message = tuning.run_root("governor", previous)
        results.append(("CPU governor", ok, previous if ok else message))
    ok, message = tuning.run_root("services", "resume")
    results.append(("Background services", ok, "restarted" if ok else message))
    tuning.run_root("game-mode", "off")
    for path in (ENV_PATH, STATE_PATH):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    results.append(("Game environment", True, "back to defaults (next Steam start)"))
    return results
