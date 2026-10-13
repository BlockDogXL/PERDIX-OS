"""PERDIX System Diagnostics: parsers against output recorded on this machine (Mesa llvmpipe/lavapipe,
Xvfb) and a gaming PC in the same formats; the report; the page."""

from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import devices, diagnostics

app = QApplication.instance() or QApplication([])
DATA = Path(__file__).parent / "data"

GAMING_VULKAN = """==========
VULKANINFO
==========

Vulkan Instance Version: 1.3.290

Devices:
========
GPU0:
\tapiVersion         = 1.3.289
\tdriverVersion      = 24.2.4
\tvendorID           = 0x1002
\tdeviceID           = 0x744c
\tdeviceType         = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU
\tdeviceName         = AMD Radeon RX 7900 XTX (RADV NAVI31)
\tdriverID           = DRIVER_ID_MESA_RADV
\tdriverName         = radv
\tdriverInfo         = Mesa 24.2.4-arch1.1
\tconformanceVersion = 1.3.0.0
GPU1:
\tapiVersion         = 1.3.289
\tdriverVersion      = 0.0.1
\tvendorID           = 0x10005
\tdeviceID           = 0x0000
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe (LLVM 18.1.8, 256 bits)
\tdriverID           = DRIVER_ID_MESA_LLVMPIPE
\tdriverName         = llvmpipe
\tdriverInfo         = Mesa 24.2.4-arch1.1 (LLVM 18.1.8)
\tconformanceVersion = 1.3.1.1
"""
GAMING_XRANDR = """Screen 0: minimum 320 x 200, current 4480 x 1440, maximum 16384 x 16384
DisplayPort-0 connected primary 2560x1440+0+0 (normal left inverted right x axis y axis) 597mm x 336mm
   2560x1440    165.00*+ 143.97   120.00    59.95
   1920x1080    144.00    60.00
HDMI-A-0 connected 1920x1080+2560+0 (normal left inverted right x axis y axis) 527mm x 296mm
   1920x1080     60.00*+ 144.00   120.00
DisplayPort-1 disconnected (normal left inverted right x axis y axis)
"""
GAMING_LSBLK = "nvme0n1   1.8T 0 disk Samsung SSD 990 PRO 2TB\nsda       3.6T 1 disk ST4000DM004-2U9104\nzram0     8G 0 disk\n"


def test_vulkaninfo_recorded_here():
    instance, found = diagnostics.parse_vulkaninfo((DATA / "vulkaninfo-summary.txt").read_text())
    assert instance == "1.3.275"
    (lavapipe,) = found
    assert lavapipe.software and lavapipe.api_version == "1.4.318" and lavapipe.driver_name == "llvmpipe"


def test_vulkaninfo_gaming_pc():
    instance, (radv, cpu) = diagnostics.parse_vulkaninfo(GAMING_VULKAN)
    assert instance == "1.3.290"
    assert (radv.name, radv.device_type, radv.api_version) == ("AMD Radeon RX 7900 XTX (RADV NAVI31)",
                                                              "DISCRETE_GPU", "1.3.289")
    assert not radv.software and cpu.software


def test_glxinfo_recorded_here():
    gl = diagnostics.parse_glxinfo((DATA / "glxinfo-B.txt").read_text())
    assert gl.renderer.startswith("llvmpipe") and gl.version.startswith("4.5 (Core Profile) Mesa")
    assert gl.accelerated is False


def test_xrandr():
    (xvfb,) = diagnostics.parse_xrandr((DATA / "xrandr-current.txt").read_text())  # recorded on Xvfb
    assert (xvfb.width, xvfb.height, xvfb.refresh) == (1280, 1024, 0.0)
    main, second = diagnostics.parse_xrandr(GAMING_XRANDR)
    assert (main.output, main.width, main.refresh, main.max_refresh) == ("DisplayPort-0", 2560, 165.0, 165.0)
    assert (second.refresh, second.max_refresh) == (60.0, 144.0)  # a 144 Hz screen left at 60 Hz


def test_lsblk():
    disks = diagnostics.parse_lsblk(GAMING_LSBLK)
    assert [(d.name, d.size, d.kind, d.model) for d in disks] == [
        ("nvme0n1", "1.8T", "NVMe", "Samsung SSD 990 PRO 2TB"), ("sda", "3.6T", "HDD", "ST4000DM004-2U9104")]


@pytest.mark.parametrize("vulkan, ok", [("1.3.289", True), ("1.4.318", True), ("1.2.199", False), ("", False)])
def test_directx_through_proton(vulkan, ok):
    rows = diagnostics.directx_support(vulkan)
    assert [r[0] for r in rows] == ["DirectX 9 · 10 · 11", "DirectX 12"]
    assert all(r[1] is ok for r in rows)
    assert "DXVK" in rows[0][2] and "VKD3D-Proton" in rows[1][2]


def gaming_report():
    pci = devices.parse_lspci((DATA / "lspci-gaming-pc.txt").read_text())[:3]  # bridge, AMD GPU, its audio
    board = devices.Motherboard("ASUSTeK", "ROG STRIX B650E-F", bios_vendor="American Megatrends Inc.",
                                bios_version="3024", bios_date="08/02/2024")
    return diagnostics.build_report(GAMING_VULKAN, "OpenGL renderer string: AMD Radeon RX 7900 XTX (radeonsi)\n"
                                    "OpenGL core profile version string: 4.6 (Core Profile) Mesa 24.2.4\n"
                                    "    Accelerated: yes\n", GAMING_XRANDR, GAMING_LSBLK, pci, board)


def test_report_gaming_pc():
    report = gaming_report()
    assert report.summary == {"GPU": "AMD Radeon RX 7900 XTX (RADV NAVI31)", "Vulkan": "1.3.289",
                              "DirectX": "9 · 10 · 11 · 12", "RAM": report.summary["RAM"], "BIOS": "3024"}
    problems = [r.key for r in report.problems()]
    assert problems == ["HDMI-A-0"]  # only the screen running below its refresh rate
    text = diagnostics.report_text(report)
    assert "  ! HDMI-A-0:" in text and "1920×1080 at 60 Hz (up to 144 Hz available)" in text
    assert "DirectX 12:" in text and "Yes, through VKD3D-Proton" in text


def test_report_software_rendering():
    report = diagnostics.build_report((DATA / "vulkaninfo-summary.txt").read_text(),
                                      (DATA / "glxinfo-B.txt").read_text(), "", "", [], devices.Motherboard())
    assert report.summary["GPU"] == "llvmpipe (software)" and report.summary["DirectX"] == "none"
    keys = [r.key for r in report.problems()]
    assert "Warning" in keys and "DirectX 12" in keys


def test_save_report(tmp_path):
    path = diagnostics.save_report(gaming_report(), str(tmp_path))
    assert Path(path).read_text().startswith("PERDIX System Diagnostics")


@pytest.fixture
def page():
    from kiosk_shell.perdix_hub import DiagnosticsPage
    page = DiagnosticsPage()
    page.loaded = True
    return page


def test_page_shows_report(page, tmp_path, monkeypatch):
    page.show_report(gaming_report())
    assert page.summary["DirectX"].value.text() == "9 · 10 · 11 · 12"
    assert page.summary["GPU"].value.text() == "AMD Radeon RX 7900 XTX (RADV NAVI31)"
    assert "HDMI-A-0" in page.problems.text() and not page.problems.isHidden()
    texts = [page.tree.topLevelItem(i).text(0) for i in range(page.tree.topLevelItemCount())]
    assert "DIRECTX (THROUGH PROTON)" in texts
    page.copy()
    assert QApplication.clipboard().text().startswith("PERDIX System Diagnostics")
    monkeypatch.setenv("HOME", str(tmp_path))
    page.save()
    assert list(tmp_path.glob("PERDIX-Diagnostics-*.txt")) and "Saved to ~/PERDIX-Diagnostics-" in page.status.text()


def test_page_runs_on_this_machine(page):
    page.run()
    page._worker.wait(30000)
    app.processEvents()
    assert page.report is not None and page.save_button.isEnabled()


def test_diagnostics_is_in_the_system_group():
    from kiosk_shell.perdix_hub import NAV
    assert ("diagnostics", "pulse", "Diagnostics", "GPU · Vulkan · DirectX · BIOS", "System") in NAV
