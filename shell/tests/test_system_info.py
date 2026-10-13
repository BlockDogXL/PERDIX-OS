from kiosk_shell.services.system_info import cpu_model, memory_total, parse_lspci, read_release


def test_parse_lspci_keeps_only_display_devices():
    output = (
        '00:02.0 "VGA compatible controller" "Intel Corporation" "Alder Lake-P GT2 [Iris Xe Graphics]" -r0c "" ""\n'
        '00:1f.3 "Audio device" "Intel Corporation" "Alder Lake PCH-P High Definition Audio" -r01 "" ""\n'
        '01:00.0 "3D controller" "NVIDIA Corporation" "GA107M [GeForce RTX 3050 Mobile]" -ra1 "" ""\n'
        'broken "line\n'
    )
    assert parse_lspci(output) == [
        "Intel Alder Lake-P GT2 [Iris Xe Graphics]",
        "NVIDIA GA107M [GeForce RTX 3050 Mobile]",
    ]


def test_cpu_and_memory(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("processor\t: 0\nmodel name\t: AMD Ryzen 7  5800X 8-Core Processor\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16303428 kB\nMemFree:  1 kB\n")
    assert cpu_model(str(cpuinfo)) == "AMD Ryzen 7 5800X 8-Core Processor"
    assert memory_total(str(meminfo)) == "15.5 GB"
    assert memory_total(str(tmp_path / "missing")) == "Unknown"


def test_release_file_and_fallback(tmp_path):
    release = tmp_path / "perdix-release"
    release.write_text('VERSION="2026.10.12"\nEDITION="Live ISO"\n')
    assert read_release(str(release)) == ("2026.10.12", "Live ISO")
    assert read_release(str(tmp_path / "missing")) == ("Development build", "Kiosk")
