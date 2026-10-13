"""Root-level tuning through `pkexec /usr/local/bin/perdix-tune` (see that script for the
allowed commands), plus the read-only system state the UI shows next to it."""

import os
import subprocess

TUNE = "/usr/local/bin/perdix-tune"
GOVERNOR_FILE = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
TCP_FILE = "/proc/sys/net/ipv4/tcp_congestion_control"


def run_root(*args: str, timeout: int = 30) -> tuple[bool, str]:
    if not os.path.exists(TUNE):
        return False, f"{TUNE} is missing."
    try:
        result = subprocess.run(["pkexec", TUNE, *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "pkexec is not installed (package 'polkit')."
    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for perdix-tune."
    if result.returncode in (126, 127):
        return False, "Not allowed: polkit refused perdix-tune."
    if result.returncode != 0:
        return False, result.stderr.strip() or f"perdix-tune failed ({result.returncode})."
    return True, "OK"


def read_first_line(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return ""


def current_governor() -> str:
    return read_first_line(GOVERNOR_FILE)


def current_tcp_algorithm() -> str:
    return read_first_line(TCP_FILE)


def default_interface() -> str:
    """Interface of the default IPv4 route, e.g. 'enp5s0', or '' if offline."""
    try:
        output = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True,
                                text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return parse_default_interface(output)


def parse_default_interface(output: str) -> str:
    for line in output.splitlines():
        fields = line.split()
        if fields[:1] == ["default"] and "dev" in fields:
            return fields[fields.index("dev") + 1]
    return ""


def qos_active(interface: str) -> bool:
    if not interface:
        return False
    try:
        output = subprocess.run(["tc", "qdisc", "show", "dev", interface], capture_output=True,
                                text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "qdisc prio 1:" in output and "qdisc ingress ffff:" in output
