"""Recovery center: Timeshift system snapshots through `pkexec perdix-tune snapshot ...`.

A snapshot holds the system (programs, drivers, settings in /etc), never /home: restoring one
puts PERDIX OS back the way it was without touching games, saves or documents. Snapshots are taken
before every update (perdix-tune update), daily and weekly by perdix-snapshot.timer, and on demand.
Restoring the running system restarts the PC.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

TUNE = "/usr/local/bin/perdix-tune"
LIVE_MARKER = "/run/archiso"
TAG_NAMES = {"O": "Manual", "B": "At start-up", "H": "Hourly", "D": "Daily", "W": "Weekly", "M": "Monthly"}


class RecoveryError(Exception):
    """A snapshot action that failed, with a message meant for the user."""


@dataclass
class Snapshot:
    name: str
    tags: str
    description: str

    @property
    def taken(self) -> datetime | None:
        try:
            return datetime.strptime(self.name, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            return None

    @property
    def kind(self) -> str:
        if "PERDIX: before update" in self.description:
            return "Before update"
        return ", ".join(TAG_NAMES.get(t, t) for t in self.tags) or "Manual"


@dataclass
class State:
    configured: bool
    device: str = ""
    mode: str = ""
    status: str = ""
    free: str = ""
    snapshots: list[Snapshot] = field(default_factory=list)

    @property
    def newest(self) -> Snapshot | None:
        return max(self.snapshots, key=lambda s: s.name) if self.snapshots else None


def is_live(marker: str = LIVE_MARKER) -> bool:
    return os.path.exists(marker)


def run(*args: str, timeout: int = 6 * 3600) -> str:
    if shutil.which("pkexec") is None:
        raise RecoveryError("pkexec (polkit) is not installed.")
    try:
        result = subprocess.run(["pkexec", TUNE, "snapshot", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RecoveryError("Timeshift took too long.") from None
    if result.returncode in (126, 127):
        raise RecoveryError("Not allowed: polkit refused perdix-tune.")
    if result.returncode != 0:
        message = (result.stderr or "").strip().splitlines()
        text = message[-1].replace("perdix-tune: ", "") if message else f"perdix-tune failed ({result.returncode})"
        raise RecoveryError(text[:1].upper() + text[1:] + ("" if text.endswith(".") else "."))
    return result.stdout


def parse_state(text: str) -> State:
    data = json.loads(text or "{}")
    return State(bool(data.get("configured")), data.get("device", ""), data.get("mode", ""), data.get("status", ""),
                 data.get("free", ""), [Snapshot(s["name"], s.get("tags", ""), s.get("description", ""))
                                        for s in data.get("snapshots", [])])


def state() -> State:
    if shutil.which("timeshift") is None:
        raise RecoveryError("Timeshift is not installed.")
    try:
        return parse_state(run("list", timeout=180))
    except ValueError:
        raise RecoveryError("Could not read the snapshot list.") from None


def setup() -> str:
    run("setup", timeout=60)
    return "Snapshots are set up: one before every update, daily and weekly ones, and any you take."


def create(comment: str) -> str:
    run("create", comment.strip() or "Manual")
    return "Snapshot taken."


def delete(snapshot: Snapshot) -> str:
    run("delete", snapshot.name)
    return "Snapshot deleted."


def restore(snapshot: Snapshot) -> str:
    run("restore", snapshot.name)
    return "Restored. The PC restarts now."
