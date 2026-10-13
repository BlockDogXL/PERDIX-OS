"""PERDIX System Diagnostics: one report of what the PC is and what games can use.

Sources (none needs root): /proc and psutil for CPU and memory, DMI for the BIOS and motherboard,
`lspci -k` for graphics cards and drivers, `vulkaninfo --summary` for Vulkan, `glxinfo -B` for
OpenGL, `xrandr` for the displays, `lsblk` for disks.

DirectX: Linux has no DirectX of its own. Proton translates DirectX 9, 10 and 11 to Vulkan with DXVK,
and DirectX 12 with VKD3D-Proton; current versions of both need Vulkan 1.3 on the GPU. The report
derives DirectX support from the GPU's Vulkan version, and says so.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

import psutil

from kiosk_shell.services import devices, system_info

DXVK_VULKAN = (1, 3)  # DXVK 2.x and VKD3D-Proton 2.8+ require Vulkan 1.3


def run(argv: list[str], timeout: int = 15) -> str:
    if shutil.which(argv[0]) is None:
        return ""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


# --- parsers --------------------------------------------------------------------------------------------


@dataclass
class VulkanDevice:
    name: str
    api_version: str
    device_type: str  # DISCRETE_GPU, INTEGRATED_GPU, CPU, ...
    driver_name: str
    driver_info: str

    @property
    def software(self) -> bool:
        return self.device_type == "CPU"


def parse_vulkaninfo(text: str) -> tuple[str, list[VulkanDevice]]:
    """`vulkaninfo --summary` -> (instance version, devices)."""
    instance = ""
    match = re.search(r"Vulkan Instance Version:\s*(\S+)", text)
    if match:
        instance = match.group(1)
    found = []
    for block in re.split(r"\n(?=GPU\d+:)", text.split("Devices:", 1)[-1]):
        values = dict(re.findall(r"^\s*(\w+)\s*=\s*(.+?)\s*$", block, re.M))
        if "deviceName" in values:
            found.append(VulkanDevice(values["deviceName"], values.get("apiVersion", ""),
                                      values.get("deviceType", "").replace("PHYSICAL_DEVICE_TYPE_", ""),
                                      values.get("driverName", ""), values.get("driverInfo", "")))
    return instance, found


@dataclass
class OpenGLInfo:
    renderer: str = ""
    version: str = ""
    accelerated: bool | None = None
    video_memory: str = ""


def parse_glxinfo(text: str) -> OpenGLInfo:
    info = OpenGLInfo()
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if key == "OpenGL renderer string":
            info.renderer = value
        elif key in ("OpenGL core profile version string", "OpenGL version string") and not info.version:
            info.version = value
        elif key == "Accelerated":
            info.accelerated = value.lower() == "yes"
        elif key == "Video memory":
            info.video_memory = value
    return info


@dataclass
class Display:
    output: str
    width: int
    height: int
    refresh: float
    max_refresh: float


def parse_xrandr(text: str) -> list[Display]:
    displays, current = [], None
    for line in text.splitlines():
        match = re.match(r"^(\S+) connected", line)
        if match:
            current = match.group(1)
            continue
        if current and line.startswith("   "):
            parts = line.split()
            size = re.match(r"(\d+)x(\d+)", parts[0]) if parts else None
            rates = [float(re.sub(r"[*+]", "", r)) for r in parts[1:] if re.match(r"^[\d.]+[*+]*$", r)]
            if size and any("*" in r for r in parts[1:]):
                active = next(float(re.sub(r"[*+]", "", r)) for r in parts[1:] if "*" in r)
                displays.append(Display(current, int(size.group(1)), int(size.group(2)), active, max(rates or [0])))
                current = None
        elif not line.startswith(" "):
            current = None
    return displays


@dataclass
class Disk:
    name: str
    size: str
    model: str
    kind: str  # SSD / HDD / NVMe


def parse_lsblk(text: str) -> list[Disk]:
    """`lsblk -dno NAME,SIZE,ROTA,TYPE,MODEL` (model last: it may contain spaces)."""
    disks = []
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4 or parts[3] != "disk" or parts[0].startswith(("zram", "loop")) or parts[1] in ("0B", "0"):
            continue
        name, size, rota = parts[0], parts[1], parts[2]
        kind = "NVMe" if name.startswith("nvme") else "HDD" if rota == "1" else "SSD"
        if name.startswith("vd"):
            kind = "Virtual disk"
        disks.append(Disk(name, size, parts[4].strip() if len(parts) > 4 else "", kind))
    return disks


# --- DirectX through Proton ---------------------------------------------------------------------------------


def directx_support(vulkan: str) -> list[tuple[str, bool, str]]:
    """(label, supported, explanation) for DirectX 9-11 (DXVK) and 12 (VKD3D-Proton)."""
    ok = bool(vulkan) and version_tuple(vulkan)[:2] >= DXVK_VULKAN
    have = f"the GPU has Vulkan {vulkan}" if vulkan else "no hardware Vulkan found"
    need = "needs Vulkan 1.3" + ("" if ok else f"; {have}")
    return [
        ("DirectX 9 · 10 · 11", ok, f"through DXVK in Proton ({need})"),
        ("DirectX 12", ok, f"through VKD3D-Proton ({need})"),
    ]


# --- the report ------------------------------------------------------------------------------------------------


@dataclass
class Row:
    key: str
    value: str
    ok: bool | None = None  # None: neutral fact; False: a problem worth showing


@dataclass
class Report:
    sections: list[tuple[str, list[Row]]] = field(default_factory=list)
    summary: dict[str, str] = field(default_factory=dict)  # the five headline figures
    created: str = ""

    def problems(self) -> list[Row]:
        return [row for _, rows in self.sections for row in rows if row.ok is False]


def human(size: int) -> str:
    return f"{size / 2 ** 30:.1f} GB"


def build_report(vulkan_text: str | None = None, glx_text: str | None = None, xrandr_text: str | None = None,
                 lsblk_text: str | None = None, pci: list[devices.Device] | None = None,
                 board: devices.Motherboard | None = None) -> Report:
    """Collect everything (the arguments let tests feed recorded tool output)."""
    report = Report(created=time.strftime("%Y-%m-%d %H:%M"))
    version, edition = system_info.read_release()
    uptime = int(time.time() - psutil.boot_time())
    report.sections.append(("System", [
        Row("PERDIX OS", f"{version} ({edition})"), Row("Kernel", platform.release()),
        Row("Up for", f"{uptime // 3600} h {uptime % 3600 // 60} min"),
    ]))

    freq = psutil.cpu_freq()
    report.sections.append(("Processor", [
        Row("Model", system_info.cpu_model()),
        Row("Cores / threads", f"{psutil.cpu_count(logical=False) or '?'} / {psutil.cpu_count()}"),
        Row("Max clock", f"{freq.max / 1000:.2f} GHz" if freq and freq.max else "unknown"),
    ]))

    memory, swap = psutil.virtual_memory(), psutil.swap_memory()
    report.sections.append(("Memory", [
        Row("Installed", human(memory.total) + ("" if memory.total >= 7.5 * 2 ** 30 else
                                                 " (below the 8 GB most games need)"),
            None if memory.total >= 7.5 * 2 ** 30 else False),
        Row("In use", f"{human(memory.total - memory.available)} ({memory.percent:.0f} %)"),
        Row("Swap", human(swap.total) if swap.total else "none"),
    ]))

    pci = devices.pci_devices() if pci is None else pci
    gpus = [d for d in pci if d.category == "Graphics"]
    instance, vk_devices = parse_vulkaninfo(run(["vulkaninfo", "--summary"]) if vulkan_text is None else vulkan_text)
    hardware = [d for d in vk_devices if not d.software]
    best = max(hardware, key=lambda d: version_tuple(d.api_version), default=None)
    graphics = []
    for gpu in gpus:
        graphics.append(Row("Card", f"{gpu.name} ({gpu.vendor})"))
        graphics.append(Row("Driver", gpu.driver or "none in use", True if gpu.driver else False))
    for d in vk_devices:
        graphics.append(Row(f"Vulkan {d.api_version}", f"{d.name} · {d.driver_info or d.driver_name}",
                            False if d.software and not hardware else None))
    if not vk_devices:
        graphics.append(Row("Vulkan", "not available (vulkaninfo found no device)", False))
    gl = parse_glxinfo(run(["glxinfo", "-B"]) if glx_text is None else glx_text)
    if gl.renderer:
        graphics.append(Row("OpenGL", f"{gl.version} · {gl.renderer}", False if gl.accelerated is False else None))
    if hardware == [] and vk_devices:
        graphics.append(Row("Warning", "only software rendering (llvmpipe/lavapipe): games will be very slow",
                            False))
    report.sections.append(("Graphics", graphics))

    vulkan_version = best.api_version if best else ""
    report.sections.append(("DirectX (through Proton)", [
        Row(label, ("Yes, " if ok else "No, ") + why, ok) for label, ok, why in directx_support(vulkan_version)
    ] + [Row("Vulkan loader", instance or "unknown")]))

    displays = parse_xrandr(run(["xrandr", "--current"]) if xrandr_text is None else xrandr_text)
    report.sections.append(("Displays", [
        Row(d.output, f"{d.width}×{d.height}" + (f" at {d.refresh:.0f} Hz" if d.refresh else "")
            + (f" (up to {d.max_refresh:.0f} Hz available)" if d.max_refresh > d.refresh + 1 else ""),
            False if d.max_refresh > d.refresh + 1 else None)
        for d in displays] or [Row("Display", "unknown (no X session)")]))

    board = devices.motherboard() if board is None else board
    report.sections.append(("Motherboard & BIOS", [
        Row("Board", " ".join(x for x in (board.vendor, board.model, board.version) if x) or "unknown"),
        Row("BIOS", " ".join(x for x in (board.bios_vendor, board.bios_version) if x) or "unknown"),
        Row("BIOS date", board.bios_date or "unknown"),
    ] + ([Row("System", board.system)] if board.system else [])))

    disks = parse_lsblk(run(["lsblk", "-dno", "NAME,SIZE,ROTA,TYPE,MODEL"]) if lsblk_text is None else lsblk_text)
    report.sections.append(("Storage", [Row(d.name, f"{d.size} {d.kind}" + (f" · {d.model}" if d.model else ""))
                                        for d in disks] or [Row("Disks", "unknown")]))

    dx = directx_support(vulkan_version)
    report.summary = {
        "GPU": (best.name if best else gpus[0].name if gpus
                else f"{vk_devices[0].name.split(' (')[0]} (software)" if vk_devices else "unknown"),
        "Vulkan": vulkan_version or "none",
        "DirectX": "9 · 10 · 11 · 12" if dx[1][1] else "limited" if vulkan_version else "none",
        "RAM": human(memory.total),
        "BIOS": board.bios_version or "unknown",
    }
    return report


def report_text(report: Report) -> str:
    lines = [f"PERDIX System Diagnostics — {report.created}", "=" * 48]
    for title, rows in report.sections:
        lines += ["", title.upper()]
        for row in rows:
            mark = "  ! " if row.ok is False else "    "
            lines.append(f"{mark}{row.key + ':':<22} {row.value}")
    problems = report.problems()
    lines += ["", f"{len(problems)} thing(s) to look at." if problems else "Nothing to look at."]
    return "\n".join(lines) + "\n"


def save_report(report: Report, folder: str | None = None) -> str:
    folder = folder or os.path.expanduser("~")
    path = os.path.join(folder, f"PERDIX-Diagnostics-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report_text(report))
    return path
