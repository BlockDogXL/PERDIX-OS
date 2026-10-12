"""Task Manager: CPU, RAM and GPU use, running programs, and ending them, including hung ones.

- CPU, memory and per-process figures come from psutil.
- GPU: AMD cards report load and VRAM through amdgpu's sysfs files; NVIDIA through nvidia-smi.
  Other GPUs show no load (Intel's counters need root).
- "Not responding" works like Windows' hung-window check: windows that support the EWMH
  _NET_WM_PING protocol are pinged; a program whose window does not answer is hung. Answers come
  back to the root window, where this module listens (python-xlib).
- Ending a task sends SIGTERM to the program and its children, then SIGKILL if it is still there;
  a hung program is killed at once (it cannot handle SIGTERM while frozen). Only the user's own
  processes can be ended, and the desktop, the window manager and the sound/graphics services are
  protected so the kiosk cannot be broken from here.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field

import psutil

# Never offered for ending: without them the kiosk session stops working.
PROTECTED_NAMES = {
    "Xorg", "X", "Xwayland", "openbox", "kiosk-session", "startx", "xinit", "systemd", "dbus-daemon",
    "dbus-broker", "pipewire", "pipewire-pulse", "wireplumber", "gamescope", "gamescope-wl", "bash", "sh", "login",
    "agetty", "polkitd",
}
NOT_RESPONDING_AFTER = 2  # missed pings in a row (about 3 s) before a window counts as hung


class TaskError(Exception):
    """A process that could not be ended, with a message meant for the user."""


# --- system figures --------------------------------------------------------------------------------


@dataclass
class GpuUsage:
    name: str
    load: float | None  # percent, None if the driver doesn't tell
    vram_used: int | None = None  # bytes
    vram_total: int | None = None


@dataclass
class SystemUsage:
    cpu: float  # percent of all cores
    per_core: list[float]
    memory_used: int
    memory_total: int
    swap_used: int
    swap_total: int
    gpus: list[GpuUsage] = field(default_factory=list)

    @property
    def memory_percent(self) -> float:
        return 100 * self.memory_used / self.memory_total if self.memory_total else 0.0


def read_int(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def amd_gpus(drm: str = "/sys/class/drm") -> list[GpuUsage]:
    gpus = []
    for device in sorted(glob.glob(os.path.join(drm, "card[0-9]*", "device"))):
        load = read_int(os.path.join(device, "gpu_busy_percent"))
        if load is None:
            continue  # not amdgpu
        gpus.append(GpuUsage(f"AMD GPU ({os.path.basename(os.path.dirname(device))})", float(load),
                             read_int(os.path.join(device, "mem_info_vram_used")),
                             read_int(os.path.join(device, "mem_info_vram_total"))))
    return gpus


def parse_nvidia_smi(output: str) -> list[GpuUsage]:
    """`nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits`"""
    gpus = []
    for line in output.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        name, load, used, total = parts
        number = lambda text: float(text) if text.replace(".", "", 1).isdigit() else None  # noqa: E731
        mib = lambda text: int(number(text) * 1024 * 1024) if number(text) is not None else None  # noqa: E731
        gpus.append(GpuUsage(name, number(load), mib(used), mib(total)))
    return gpus


def nvidia_gpus() -> list[GpuUsage]:
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        output = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_nvidia_smi(output)


def system_usage() -> SystemUsage:
    memory, swap = psutil.virtual_memory(), psutil.swap_memory()
    return SystemUsage(psutil.cpu_percent(), psutil.cpu_percent(percpu=True), memory.total - memory.available,
                       memory.total, swap.used, swap.total, amd_gpus() + nvidia_gpus())


# --- windows and the hung-window check -------------------------------------------------------------


@dataclass
class Window:
    id: int
    pid: int | None
    title: str
    pingable: bool  # supports _NET_WM_PING


class WindowWatcher:
    """Lists top-level windows and pings them. One X connection, reused between refreshes."""

    def __init__(self, display: str | None = None) -> None:
        from Xlib import X, display as xdisplay

        self._X = X
        self.d = xdisplay.Display(display)
        self.root = self.d.screen().root
        # Pong replies are sent to the root window with SubstructureNotify|Redirect: listen to them.
        self.root.change_attributes(event_mask=X.SubstructureNotifyMask)
        atom = self.d.intern_atom
        self.atoms = {name: atom(name) for name in (
            "_NET_CLIENT_LIST", "_NET_WM_PID", "_NET_WM_NAME", "UTF8_STRING", "WM_PROTOCOLS", "_NET_WM_PING")}

    def close(self) -> None:
        self.d.close()

    def _property(self, window, name: str, kind):
        try:
            prop = window.get_full_property(self.atoms[name] if isinstance(name, str) else name, kind)
        except Exception:  # the window is gone (BadWindow)
            return None
        return prop.value if prop is not None else None

    def windows(self) -> list[Window]:
        from Xlib import Xatom

        ids = self._property(self.root, "_NET_CLIENT_LIST", Xatom.WINDOW)
        if ids is None:  # no EWMH window manager: fall back to the root's children
            try:
                ids = [w.id for w in self.root.query_tree().children]
            except Exception:
                ids = []
        found = []
        for wid in ids:
            window = self.d.create_resource_object("window", int(wid))
            pid = self._property(window, "_NET_WM_PID", Xatom.CARDINAL)
            title = self._property(window, "_NET_WM_NAME", self.atoms["UTF8_STRING"])
            if title is None:
                title = self._property(window, Xatom.WM_NAME, Xatom.STRING)
            protocols = self._property(window, "WM_PROTOCOLS", Xatom.ATOM) or []
            if pid is None and title is None:
                continue  # helper windows without identity
            text = title.decode("utf-8", "replace") if isinstance(title, bytes) else (title or "")
            found.append(Window(int(wid), int(pid[0]) if pid is not None and len(pid) else None, text,
                                self.atoms["_NET_WM_PING"] in list(protocols)))
        return found

    def ping(self, windows: list[Window], timeout: float = 1.5) -> dict[int, bool]:
        """Window id -> answered within the timeout, for every pingable window."""
        from Xlib.protocol import event

        X = self._X
        waiting = {}
        for w in windows:
            if not w.pingable:
                continue
            window = self.d.create_resource_object("window", w.id)
            message = event.ClientMessage(window=window, client_type=self.atoms["WM_PROTOCOLS"],
                                          data=(32, [self.atoms["_NET_WM_PING"], X.CurrentTime, w.id, 0, 0]))
            try:
                window.send_event(message, event_mask=X.NoEventMask)
                waiting[w.id] = False
            except Exception:
                pass
        self.d.flush()
        deadline = time.monotonic() + timeout
        while not all(waiting.values()) and time.monotonic() < deadline:
            while self.d.pending_events():
                ev = self.d.next_event()
                if (ev.type == X.ClientMessage and ev.client_type == self.atoms["WM_PROTOCOLS"]
                        and ev.data[1][0] == self.atoms["_NET_WM_PING"] and ev.data[1][2] in waiting):
                    waiting[ev.data[1][2]] = True
            time.sleep(0.02)
        return waiting


# --- processes ------------------------------------------------------------------------------------


@dataclass
class ProcessInfo:
    pid: int
    name: str
    user: str
    cpu: float  # percent of one core, like top
    memory: int  # resident bytes
    status: str  # psutil status: running, sleeping, disk-sleep, stopped, zombie, ...
    window_title: str = ""  # set for programs with a window ("apps")
    responding: bool | None = None  # None: not checked (no pingable window)
    protected: bool = False

    @property
    def is_app(self) -> bool:
        return bool(self.window_title)


def is_protected(proc: psutil.Process, name: str, cmdline: list[str]) -> bool:
    if proc.pid in (os.getpid(), 1) or name in PROTECTED_NAMES:
        return True
    joined = " ".join(cmdline)
    return "kiosk_shell" in joined and "perdix_hub" not in joined  # the desktop itself


def processes(all_users: bool = False) -> list[ProcessInfo]:
    """Running processes (the user's own unless all_users), with CPU and memory. CPU figures need two
    calls a moment apart: psutil keeps the Process objects between calls."""
    me = psutil.Process().username()
    found = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info", "status", "cmdline"]):
        info = proc.info
        if not all_users and info["username"] != me:
            continue
        try:
            cpu = proc.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        name = info["name"] or "?"
        memory = info["memory_info"].rss if info["memory_info"] else 0
        if not info["cmdline"] and memory == 0:
            continue  # a kernel thread, not a program
        found.append(ProcessInfo(info["pid"], name, info["username"] or "?", cpu, memory, info["status"] or "?",
                                 protected=is_protected(proc, name, info["cmdline"] or [])))
    return found


@dataclass
class Snapshot:
    usage: SystemUsage
    processes: list[ProcessInfo]


class TaskMonitor:
    """Builds snapshots: usage, processes, windows and their ping answers. The Not responding verdict
    needs NOT_RESPONDING_AFTER missed pings in a row, so a short stall is not reported."""

    def __init__(self, display: str | None = None) -> None:
        self.display = display if display is not None else os.environ.get("DISPLAY")
        self._watcher: WindowWatcher | None = None
        self._misses: dict[int, int] = {}
        psutil.cpu_percent()  # first call only primes the counters

    def watcher(self) -> WindowWatcher | None:
        if self._watcher is None and self.display:
            try:
                self._watcher = WindowWatcher(self.display)
            except Exception:  # no X (tests, console): run without window information
                self.display = None
        return self._watcher

    def snapshot(self, all_users: bool = False, ping_timeout: float = 1.5) -> Snapshot:
        procs = processes(all_users)
        watcher = self.watcher()
        if watcher is not None:
            windows = watcher.windows()
            answers = watcher.ping(windows, ping_timeout)
            for wid, answered in answers.items():
                self._misses[wid] = 0 if answered else self._misses.get(wid, 0) + 1
            by_pid: dict[int, list[Window]] = {}
            for w in windows:
                if w.pid is not None:
                    by_pid.setdefault(w.pid, []).append(w)
            for p in procs:
                own = by_pid.get(p.pid, [])
                if own:
                    p.window_title = next((w.title for w in own if w.title), own[0].title) or p.name
                    checked = [w for w in own if w.id in answers]
                    if checked:
                        p.responding = not any(self._misses.get(w.id, 0) >= NOT_RESPONDING_AFTER for w in checked)
        return Snapshot(system_usage(), procs)

    def close(self) -> None:
        if self._watcher is not None:
            self._watcher.close()
            self._watcher = None


# --- ending processes ---------------------------------------------------------------------------------


def end_task(pid: int, force: bool = False, wait: float = 3.0) -> str:
    """End a program and its children. force: SIGKILL at once (for a hung program); otherwise
    SIGTERM, and SIGKILL for whatever is still running after `wait` seconds."""
    try:
        proc = psutil.Process(pid)
        name = proc.name()
        cmdline = proc.cmdline()
    except psutil.NoSuchProcess:
        return "It has already ended."
    except psutil.AccessDenied:
        raise TaskError("Not allowed: that process belongs to the system.") from None
    if is_protected(proc, name, cmdline):
        raise TaskError(f"{name} keeps PERDIX OS running and can't be ended here.")
    if proc.username() != psutil.Process().username():
        raise TaskError(f"{name} belongs to another user.")
    try:
        family = proc.children(recursive=True) + [proc]
    except psutil.NoSuchProcess:
        return "It has already ended."
    for p in family:
        try:
            p.kill() if force else p.terminate()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            raise TaskError(f"Not allowed to end {p.name()}.") from None
    _gone, alive = psutil.wait_procs(family, timeout=wait)
    if alive:
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        _gone, alive = psutil.wait_procs(alive, timeout=wait)
    if alive:
        raise TaskError(f"{name} did not end (it may be stuck in the kernel; a restart frees it).")
    return f"{name} was killed." if force else f"{name} was ended."


def human_bytes(size: int | None) -> str:
    if size is None:
        return "—"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
