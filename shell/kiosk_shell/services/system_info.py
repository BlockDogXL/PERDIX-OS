"""Facts for the About System page. Every lookup falls back to "Unknown" instead of failing."""

import os
import platform
import shlex
import subprocess
from dataclasses import dataclass, field

from kiosk_shell import __version__
from kiosk_shell.config import read_shell_vars

RELEASE_FILE = "/etc/perdix-release"  # written by the ISO build scripts / installer
UNKNOWN = "Unknown"
GPU_CLASSES = ("VGA compatible controller", "3D controller", "Display controller")


@dataclass
class SystemInfo:
    version: str = UNKNOWN
    edition: str = UNKNOWN
    kernel: str = UNKNOWN
    architecture: str = UNKNOWN
    processor: str = UNKNOWN
    graphics: list[str] = field(default_factory=list)
    memory: str = UNKNOWN
    desktop: str = __version__


def read_release(path: str = RELEASE_FILE) -> tuple[str, str]:
    release = read_shell_vars(path)
    return release.get("VERSION", "Development build"), release.get("EDITION", "Kiosk")


def cpu_model(path: str = "/proc/cpuinfo") -> str:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition(":")
                if key.strip() == "model name":
                    return " ".join(value.split())
    except OSError:
        pass
    return platform.processor() or UNKNOWN


def memory_total(path: str = "/proc/meminfo") -> str:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kib = int(line.split()[1])
                    return f"{kib / 1024 / 1024:.1f} GB"
    except (OSError, ValueError, IndexError):
        pass
    return UNKNOWN


def parse_lspci(output: str) -> list[str]:
    """GPU names from `lspci -mm` output: slot "class" "vendor" "device" ..."""
    gpus = []
    for line in output.splitlines():
        try:
            fields = shlex.split(line)
        except ValueError:
            continue
        if len(fields) >= 4 and fields[1] in GPU_CLASSES:
            vendor = fields[2].replace(" Corporation", "").replace(", Inc.", "").replace(" Inc.", "")
            gpus.append(f"{vendor} {fields[3]}")
    return gpus


def graphics() -> list[str]:
    try:
        result = subprocess.run(["lspci", "-mm"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_lspci(result.stdout)


def collect() -> SystemInfo:
    version, edition = read_release()
    uname = os.uname()
    return SystemInfo(
        version=version,
        edition=edition,
        kernel=f"Linux {uname.release}",
        architecture=uname.machine or UNKNOWN,
        processor=cpu_model(),
        graphics=graphics(),
        memory=memory_total(),
    )
