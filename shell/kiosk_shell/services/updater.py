"""The smart updater: what can be updated, what it touches, and installing it safely.

- Checking: `checkupdates` (pacman-contrib) syncs a private copy of the package databases, so a
  check never half-updates the system and needs no root.
- "Smart": updates are grouped (graphics driver, kernel, games, system, apps) and the ones that only
  take effect after a restart are named; nothing is checked, announced or installed while a game is
  running; installing starts with a Timeshift snapshot (perdix-tune update) and refreshes the
  keyring first.
- `python -m kiosk_shell.services.updater --watch`, started with the session, checks 10 minutes after
  login and then every 6 hours, and announces new updates with a PERDIX notification whose button
  opens the Control Center on this page.
- The live ISO runs from memory: it can check, but installing is refused there.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from kiosk_shell.services import hub_settings

TUNE = "/usr/local/bin/perdix-tune"
LIVE_MARKER = "/run/archiso"
FIRST_CHECK_SECONDS = 10 * 60
CHECK_EVERY_SECONDS = 6 * 3600

GROUPS = (  # (group, pattern on the package name), first match wins
    ("Graphics driver", r"^(lib32-)?(mesa|vulkan-.*|nvidia.*|libva.*|libdrm|xf86-video-.*|intel-media-driver|"
                        r"opencl-.*|llvm-libs|spirv-tools)$"),
    ("Kernel & firmware", r"^(linux(-lts|-zen|-hardened)?(-headers)?|linux-firmware.*|amd-ucode|intel-ucode)$"),
    ("Games", r"^(steam|gamescope|gamemode|lib32-gamemode|mangohud|lib32-mangohud|wine.*|proton.*|"
              r"lutris|heroic.*|obs-studio)$"),
    ("System", r"^(systemd.*|glibc|lib32-glibc|pacman.*|archlinux-keyring|openssl|bash|coreutils|util-linux|"
               r"pipewire.*|wireplumber|networkmanager|xorg-.*|polkit|dbus.*|sudo)$"),
)
RESTART = re.compile(r"^(linux(-lts|-zen|-hardened)?|linux-firmware.*|amd-ucode|intel-ucode|systemd|glibc|"
                     r"(lib32-)?mesa|nvidia.*|xorg-server)$")
GAME_PROCESSES = {"gamescope", "gamescope-wl", "wineserver", "wine64-preloader", "wine-preloader", "reaper"}


class UpdateError(Exception):
    """A check or update that failed, with a message meant for the user."""


@dataclass
class Update:
    name: str
    old: str
    new: str

    @property
    def group(self) -> str:
        for group, pattern in GROUPS:
            if re.match(pattern, self.name):
                return group
        return "Apps"

    @property
    def needs_restart(self) -> bool:
        return bool(RESTART.match(self.name))


def parse_checkupdates(text: str) -> list[Update]:
    """'mesa 1:25.2.4-1 -> 1:25.2.5-1' lines. Packages marked ' [ignored]' (IgnorePkg in pacman.conf) are
    left out: pacman will not update them."""
    found = []
    for line in text.splitlines():
        if line.rstrip().endswith("[ignored]"):
            continue
        match = re.match(r"^(\S+) (\S+) -> (\S+)", line.strip())
        if match:
            found.append(Update(*match.groups()))
    return found


def is_live(marker: str = LIVE_MARKER) -> bool:
    return os.path.exists(marker)


def game_running() -> bool:
    """A Steam game (its 'reaper' launcher), Proton/Wine or gamescope is running."""
    try:
        import psutil
        for process in psutil.process_iter(["name", "cmdline"]):
            name = process.info["name"] or ""
            cmdline = " ".join(process.info["cmdline"] or [])
            if name in GAME_PROCESSES or "SteamLaunch" in cmdline:
                return True
    except Exception:
        return False
    return False


def check(timeout: int = 300) -> list[Update]:
    if shutil.which("checkupdates") is None:
        raise UpdateError("The update checker (pacman-contrib) is not installed.")
    try:
        result = subprocess.run(["checkupdates"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise UpdateError("Checking for updates took too long. Is the internet connection working?") from None
    if result.returncode == 2:  # checkupdates: no updates
        return []
    if result.returncode != 0:
        message = (result.stderr or "").strip().splitlines()
        reason = message[-1].replace("==> ERROR:", "").strip() if message else ""
        raise UpdateError("Could not check for updates" + (f": {reason}" if reason else ". Is the internet "
                                                                                          "connection working?"))
    return parse_checkupdates(result.stdout)


def by_group(updates: list[Update]) -> list[tuple[str, list[Update]]]:
    order = [g for g, _ in GROUPS] + ["Apps"]
    groups = {g: [u for u in updates if u.group == g] for g in order}
    return [(g, items) for g, items in groups.items() if items]


def summary(updates: list[Update]) -> str:
    """'23 updates: Graphics driver 3, Kernel & firmware 1, Apps 19 · restart needed'"""
    if not updates:
        return "PERDIX OS is up to date."
    parts = ", ".join(f"{group} {len(items)}" for group, items in by_group(updates))
    restart = " · restart needed afterwards" if any(u.needs_restart for u in updates) else ""
    return f"{len(updates)} update{'s' if len(updates) != 1 else ''}: {parts}{restart}"


def highlights(updates: list[Update], limit: int = 3) -> str:
    """The most interesting updates for a notification: drivers, kernel and games first."""
    order = {g: i for i, (g, _) in enumerate(GROUPS)}
    picked = sorted(updates, key=lambda u: order.get(u.group, 99))[:limit]
    return "\n".join(f"{u.name} {u.new.split(':')[-1]}" for u in picked)


# --- installing -----------------------------------------------------------------------------------------------


def install_command() -> list[str]:
    return ["pkexec", TUNE, "update"]


@dataclass
class Progress:
    phase: str = "starting"  # snapshot, keyring, upgrade, done
    step: int = 0
    total: int = 0
    package: str = ""
    restart: bool = False

    @property
    def percent(self) -> int:
        order = {"starting": 0, "snapshot": 5, "keyring": 10, "upgrade": 15, "done": 100}
        if self.phase == "upgrade" and self.total:
            return 15 + int(85 * self.step / self.total)
        return order.get(self.phase, 0)

    def text(self) -> str:
        return {"starting": "Starting…", "snapshot": "Taking a safety snapshot…",
                "keyring": "Updating the package keys…",
                "done": "Done." + (" Restart to finish." if self.restart else "")}.get(
            self.phase, f"Installing {self.package} ({self.step}/{self.total})…" if self.total
            else "Downloading…")


STEP = re.compile(r"^\(\s*(\d+)/(\d+)\) (?:upgrading|installing|reinstalling|downgrading) (\S+)")


def read_line(progress: Progress, line: str) -> Progress:
    """Follow perdix-tune's 'PERDIX: …' markers and pacman's '(3/57) upgrading mesa' lines."""
    line = line.strip()
    if line.startswith("PERDIX: "):
        word = line[8:].split()
        progress.phase = word[0] if word else progress.phase
        progress.restart = progress.restart or "restart" in word[1:]
        return progress
    match = STEP.match(line)
    if match:
        progress.phase, progress.step, progress.total, progress.package = (
            "upgrade", int(match.group(1)), int(match.group(2)), match.group(3))
    return progress


def install(on_line=None) -> Progress:
    """Run `pkexec perdix-tune update`, feeding each output line to on_line(line, progress)."""
    if is_live():
        raise UpdateError("This is the live system: it runs from memory, so updates would be gone after a "
                          "restart. Install PERDIX OS to keep it up to date.")
    if game_running():
        raise UpdateError("A game is running. Close it first: updating can change files the game uses.")
    if shutil.which("pkexec") is None:
        raise UpdateError("pkexec (polkit) is not installed.")
    process = subprocess.Popen(install_command(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               bufsize=1)
    progress, last = Progress(), ""
    for line in process.stdout:
        read_line(progress, line)
        if line.strip():
            last = line.strip()
        if on_line:
            on_line(line.rstrip("\n"), progress)
    code = process.wait()
    if code == 126 or code == 127:
        raise UpdateError("Not allowed: polkit refused the update.")
    if code != 0:
        raise UpdateError(last.replace("perdix-tune: ", "").capitalize() if last else f"The update failed ({code}).")
    progress.phase = "done"
    return progress


# --- the background check -------------------------------------------------------------------------------------


def settings() -> dict:
    return hub_settings.merged({"auto_check": True, "last_check": 0.0, "last_count": 0, "announced": ""},
                               hub_settings.load().get("updates"))


def remember(updates: list[Update]) -> None:
    values = settings()
    values.update(last_check=time.time(), last_count=len(updates))
    hub_settings.save_section("updates", values)


def notify(updates: list[Update]) -> None:
    """A PERDIX notification with a button that opens the Control Center on the Updates page."""
    if shutil.which("notify-send") is None:
        return
    answer = subprocess.run(["notify-send", "-a", "PERDIX Updater", "-i", "system-software-update",
                             "-A", "open=Show updates", "-t", "15000", "-w", summary(updates).split(":")[0] + " ready",
                             highlights(updates)], capture_output=True, text=True)
    if answer.stdout.strip() == "open":
        subprocess.Popen([sys.executable, "-m", "kiosk_shell.perdix_hub", "--page", "updates"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def check_and_announce() -> list[Update] | None:
    """One background round: skipped during games; announces only what was not announced before."""
    if not settings()["auto_check"] or game_running():
        return None
    updates = check()
    remember(updates)
    key = ",".join(sorted(f"{u.name}={u.new}" for u in updates))
    if updates and key != settings()["announced"]:
        values = settings()
        values["announced"] = key
        hub_settings.save_section("updates", values)
        notify(updates)
    return updates


def watch() -> None:
    parent = os.getppid()
    wait = FIRST_CHECK_SECONDS
    while True:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if os.getppid() != parent:  # the session ended
                return
            time.sleep(15)
        try:
            check_and_announce()
            wait = CHECK_EVERY_SECONDS
        except UpdateError:
            wait = 30 * 60  # offline: try again in half an hour


if __name__ == "__main__" and sys.argv[1:] == ["--watch"]:
    watch()
