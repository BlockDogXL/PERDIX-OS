import importlib.machinery
import importlib.util
import os
import sys

import pytest

from kiosk_shell.services import magnifier, tuning

HELPER = os.path.join(os.path.dirname(__file__), "..", "..", "rootfs", "usr", "local", "bin", "perdix-tune")
sys.dont_write_bytecode = True  # don't leave a __pycache__ next to the helper in rootfs/
loader = importlib.machinery.SourceFileLoader("perdix_tune", HELPER)
spec = importlib.util.spec_from_loader("perdix_tune", loader)
perdix_tune = importlib.util.module_from_spec(spec)
loader.exec_module(perdix_tune)


def covered(blocks):
    ports = set()
    for value, mask in blocks:
        size = (~mask & 0xFFFF) + 1
        assert value % size == 0
        ports.update(range(value, value + size))
    return ports


@pytest.mark.parametrize("lo,hi", [(27015, 27050), (7000, 8000), (1, 1), (37000, 40000), (1, 65535)])
def test_port_masks_cover_exactly_the_range(lo, hi):
    assert covered(perdix_tune.port_masks(lo, hi)) == set(range(lo, hi + 1))


def test_helper_rejects_bad_input():
    for args in (["governor", "turbo"], ["tcp", "bbr2"], ["qos", "on", "eth0; reboot", "50", "1-2"],
                 ["rm", "-rf", "/"], []):
        with pytest.raises(perdix_tune.UsageError):
            perdix_tune.plan(args)
    with pytest.raises(perdix_tune.UsageError):
        perdix_tune.parse_ports("70000")
    with pytest.raises(perdix_tune.UsageError):
        perdix_tune.parse_limit("0")
    assert perdix_tune.main(["governor", "turbo"]) == 2


def test_helper_commands(tmp_path):
    (tmp_path / "eth0").mkdir()
    assert perdix_tune.check_iface("eth0", str(tmp_path)) == "eth0"
    assert perdix_tune.tcp_commands("bbr") == (
        [["sysctl", "-w", "net.ipv4.tcp_congestion_control=bbr"]],
        [["sysctl", "-w", "net.core.default_qdisc=fq"]],
    )
    assert perdix_tune.governor_commands("performance") == [["cpupower", "frequency-set", "-g", "performance"]]
    commands = perdix_tune.qos_on_commands("eth0", 50, [(27015, 27050)])
    flat = [" ".join(c) for c in commands]
    assert flat[0] == "tc qdisc add dev eth0 root handle 1: prio bands 3"
    assert any("match ip dport 27015 0xffff flowid 1:1" in c for c in flat)
    assert any("match ip dport 27016 0xfff8 flowid 1:1" in c for c in flat)
    assert not any("protocol 17" in c and "ffff:" in c for c in flat)  # game UDP never policed
    assert flat[-1].endswith("match ip protocol 6 0xff police rate 50mbit burst 625k drop flowid :1")


def test_default_interface_and_zoom_maths():
    assert tuning.parse_default_interface("default via 192.168.1.1 dev enp5s0 proto dhcp metric 100\n") == "enp5s0"
    assert tuning.parse_default_interface("") == ""
    assert magnifier.zoom_transform(1920, 1080, 2.0) == "0.500000,0,480.000,0,0.500000,270.000,0,0,1"
    xrandr = ("Screen 0: minimum 8 x 8, current 2560 x 1440\n"
              "DP-1 connected 1920x1080+2560+0 (normal) 527mm x 296mm\n"
              "DP-2 connected primary 2560x1440+0+0 (normal) 597mm x 336mm\n"
              "HDMI-1 disconnected (normal)\n")
    assert magnifier.parse_xrandr(xrandr) == ("DP-2", 2560, 1440)
