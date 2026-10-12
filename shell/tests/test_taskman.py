"""Task Manager: GPU readers, processes, ending real processes, the hung-window check on a real X
server (with tests/fake_x_client.py), and the page."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import taskman

app = QApplication.instance() or QApplication([])
HERE = Path(__file__).parent


# --- GPU readers --------------------------------------------------------------------------------------


def test_parse_nvidia_smi():
    output = "NVIDIA GeForce RTX 4070, 37, 2048, 12282\nTesla T4, [N/A], 0, 15360\n"
    rtx, tesla = taskman.parse_nvidia_smi(output)
    assert (rtx.name, rtx.load, rtx.vram_used, rtx.vram_total) == (
        "NVIDIA GeForce RTX 4070", 37.0, 2048 * 2 ** 20, 12282 * 2 ** 20)
    assert tesla.load is None


def test_amd_sysfs(tmp_path):
    device = tmp_path / "card1" / "device"
    device.mkdir(parents=True)
    (device / "gpu_busy_percent").write_text("63\n")
    (device / "mem_info_vram_used").write_text(str(3 * 2 ** 30))
    (device / "mem_info_vram_total").write_text(str(8 * 2 ** 30))
    (tmp_path / "card0" / "device").mkdir(parents=True)  # an Intel card: no gpu_busy_percent
    (gpu,) = taskman.amd_gpus(str(tmp_path))
    assert (gpu.load, gpu.vram_used, gpu.vram_total) == (63.0, 3 * 2 ** 30, 8 * 2 ** 30)


def test_system_usage_is_sane():
    usage = taskman.system_usage()
    assert 0 <= usage.cpu <= 100 and len(usage.per_core) == psutil.cpu_count()
    assert 0 < usage.memory_used <= usage.memory_total and 0 < usage.memory_percent < 100


def test_human_bytes():
    assert [taskman.human_bytes(n) for n in (512, 2048, 5 * 2 ** 20, 3 * 2 ** 30, None)] == [
        "512 B", "2 KB", "5.0 MB", "3.0 GB", "—"]


# --- processes and ending them ----------------------------------------------------------------------------


def spawn(code: str) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", code])
    time.sleep(0.3)
    return proc


def test_process_list_has_cpu_and_memory():
    child = spawn("import time\nwhile True: sum(range(10000))")
    try:
        taskman.processes()  # first sample
        time.sleep(0.5)
        mine = {p.pid: p for p in taskman.processes()}
        assert child.pid in mine and mine[child.pid].cpu > 10 and mine[child.pid].memory > 0
        assert mine[os.getpid()].protected  # the Control Center itself
        assert not any(p.name.startswith(("kworker", "cpuhp", "ksoftirqd")) for p in taskman.processes(True))
    finally:
        child.kill()
        child.wait()


def test_end_task_terminates():
    child = spawn("import time; time.sleep(60)")
    assert taskman.end_task(child.pid).endswith(" was ended.")
    assert child.wait(5) is not None


def test_end_task_kills_a_process_that_ignores_sigterm():
    child = spawn("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)")
    started = time.monotonic()
    taskman.end_task(child.pid, wait=0.5)
    elapsed = time.monotonic() - started
    assert not psutil.pid_exists(child.pid) or psutil.Process(child.pid).status() == psutil.STATUS_ZOMBIE
    assert 0.5 <= elapsed < 3  # SIGTERM was ignored for the grace time, then SIGKILL


def test_end_task_takes_the_children_along():
    parent = subprocess.Popen(["sh", "-c", "sleep 60 & sleep 60 & wait"])
    time.sleep(0.3)
    children = psutil.Process(parent.pid).children(recursive=True)
    assert len(children) == 2
    with pytest.raises(taskman.TaskError, match="keeps PERDIX OS running"):
        taskman.end_task(parent.pid)  # "sh" is on the protected list...
    taskman.end_task(children[0].pid, force=True)  # ...its children are not
    assert not children[0].is_running() or children[0].status() == psutil.STATUS_ZOMBIE
    parent.kill()
    parent.wait()
    for c in children[1:]:
        c.kill()


def test_end_task_refuses_protected_and_handles_gone():
    with pytest.raises(taskman.TaskError, match="keeps PERDIX OS running"):
        taskman.end_task(os.getpid())
    gone = spawn("pass")
    gone.wait()
    assert taskman.end_task(gone.pid) == "It has already ended."


def test_protected_names():
    assert taskman.is_protected(psutil.Process(), "python", []) is True  # ourselves
    other = psutil.Process(os.getppid())
    assert taskman.is_protected(other, "openbox", [])
    assert taskman.is_protected(other, "python", ["python", "-m", "kiosk_shell"])  # the desktop
    assert not taskman.is_protected(other, "python", ["python", "-m", "kiosk_shell.perdix_hub"])
    assert not taskman.is_protected(other, "steam", ["steam"])


# --- the hung-window check on a real X server --------------------------------------------------------------


@pytest.fixture
def x_server():
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb is needed")
    server = subprocess.Popen(["Xvfb", ":97", "-nolisten", "tcp", "-noreset"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    from Xlib import display
    for _ in range(50):
        try:
            display.Display(":97").close()
            break
        except Exception:
            time.sleep(0.1)
    clients = []
    yield ":97", clients
    for c in clients:
        c.kill()
        c.wait()
    server.terminate()
    server.wait()


def start_client(display, clients, title, frozen=False):
    client = subprocess.Popen([sys.executable, str(HERE / "fake_x_client.py"), display, title]
                              + (["--frozen"] if frozen else []), stdout=subprocess.PIPE, text=True)
    clients.append(client)
    return client, int(client.stdout.readline())


def test_frozen_window_is_reported_and_killed(x_server):
    from Xlib import Xatom, display as xdisplay
    name, clients = x_server
    ok, ok_id = start_client(name, clients, "Counter-Strike 2")
    frozen, frozen_id = start_client(name, clients, "Frozen Game", frozen=True)
    d = xdisplay.Display(name)  # stand in for the window manager's client list
    d.screen().root.change_property(d.intern_atom("_NET_CLIENT_LIST"), Xatom.WINDOW, 32, [ok_id, frozen_id])
    d.sync()
    monitor = taskman.TaskMonitor(name)
    try:
        first = {p.window_title: p.responding for p in monitor.snapshot(ping_timeout=1.0).processes if p.is_app}
        assert first == {"Counter-Strike 2": True, "Frozen Game": True}  # one missed ping is not enough
        second = {p.window_title: p for p in monitor.snapshot(ping_timeout=1.0).processes if p.is_app}
        assert second["Counter-Strike 2"].responding is True
        assert second["Frozen Game"].responding is False and second["Frozen Game"].pid == frozen.pid
        taskman.end_task(frozen.pid, force=True)
        assert not psutil.pid_exists(frozen.pid)
    finally:
        monitor.close()
        d.close()


def test_monitor_without_x(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    snap = taskman.TaskMonitor(display="").snapshot()
    assert snap.processes and not any(p.is_app for p in snap.processes)


# --- the page ----------------------------------------------------------------------------------------------


def fake_snapshot(*procs):
    usage = taskman.SystemUsage(42.0, [40.0, 44.0], 6 * 2 ** 30, 16 * 2 ** 30, 0, 0,
                                [taskman.GpuUsage("AMD GPU (card1)", 71.0, 3 * 2 ** 30, 8 * 2 ** 30)])
    return taskman.Snapshot(usage, list(procs))


def proc(pid, name, cpu=1.0, title="", responding=None, protected=False):
    return taskman.ProcessInfo(pid, name, "kiosk", cpu, 100 * 2 ** 20, "sleeping", title, responding, protected)


@pytest.fixture
def page():
    from kiosk_shell.perdix_hub import TaskManagerPage
    page = TaskManagerPage()
    yield page
    page.shutdown()


def test_page_shows_usage_and_orders_processes(page):
    page.show_snapshot(fake_snapshot(proc(10, "pipewire", 0.1, protected=True), proc(11, "bash", 9.0),
                                     proc(12, "steam", 3.0, "Steam", True), proc(13, "game", 80.0, "Frozen Game", False)))
    rows = [page.table.item(r, 0).text() for r in range(page.table.rowCount())]
    assert rows == ["Frozen Game", "Steam", "bash", "pipewire"]  # hung, apps, then background by CPU
    assert page.table.item(0, 1).text() == "Not responding"
    assert page.cpu_graph.value_text == "42 %" and page.gpu_graph.value_text == "71 %"
    assert page.ram_graph.detail == "6.0 GB / 16.0 GB"
    assert not page.hung_banner.isHidden() and page.hung_label.text() == "Frozen Game is not responding."


def test_page_kill_hung_in_one_click(page, monkeypatch):
    calls = []
    monkeypatch.setattr(taskman, "end_task", lambda pid, force: calls.append((pid, force)) or "ok")
    monkeypatch.setattr(page, "refresh", lambda: None)
    page.show_snapshot(fake_snapshot(proc(13, "game", 80.0, "Frozen Game", False)))
    page.hung_kill.click()
    page._worker.wait(5000)
    app.processEvents()
    assert calls == [(13, True)] and page.status.text() == "Frozen Game was killed."


def test_page_sorts_by_clicked_column(page):
    page.show_snapshot(fake_snapshot(proc(11, "bash", 9.0), proc(12, "steam", 3.0, "Steam", True),
                                     proc(14, "zsh", 20.0)))
    page.header_clicked(2)  # CPU, largest first
    assert [page.table.item(r, 0).text() for r in range(3)] == ["zsh", "bash", "Steam"]
    page.header_clicked(2)  # again: smallest first
    assert [page.table.item(r, 0).text() for r in range(3)] == ["Steam", "bash", "zsh"]
    page.header_clicked(4)  # PID
    assert [page.table.item(r, 4).text() for r in range(3)] == ["11", "12", "14"]


def test_page_protected_cannot_be_ended(page):
    page.show_snapshot(fake_snapshot(proc(10, "Xorg", 2.0, protected=True), proc(11, "steam", 1.0, "Steam", True)))
    page.table.selectRow(1)  # Xorg (background, below Steam)
    assert page.table.item(1, 0).text() == "Xorg"
    assert not page.end_button.isEnabled() and "can't be ended" in page.status.text()
    page.table.selectRow(0)
    assert page.end_button.isEnabled() and page.kill_button.isEnabled()


def test_page_end_task_uses_sigterm(page, monkeypatch):
    calls = []
    monkeypatch.setattr(taskman, "end_task", lambda pid, force: calls.append((pid, force)) or "ok")
    monkeypatch.setattr(page, "refresh", lambda: None)
    page.show_snapshot(fake_snapshot(proc(11, "steam", 1.0, "Steam", True)))
    page.table.selectRow(0)
    page.end_button.click()
    page._worker.wait(5000)
    app.processEvents()
    assert calls == [(11, False)] and page.status.text() == "Steam was ended."


def test_page_refreshes_only_while_visible(page, monkeypatch):
    monkeypatch.setattr(page, "refresh", lambda: None)
    page.show()
    assert page._timer.isActive()
    page.hide()
    assert not page._timer.isActive()


def test_task_manager_is_in_the_system_group():
    from kiosk_shell.perdix_hub import NAV
    assert ("taskman", "tasks", "Task Manager", "CPU · RAM · GPU · processes", "System") in NAV
