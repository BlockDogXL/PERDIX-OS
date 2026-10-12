"""Tests for PERDIX Kılavuz (PERDIX Sentinel). Run: pytest apps/perdix-kilavuz/tests

Port tests use synthetic /proc/net tables and a fake /proc; permission tests build a small
tree in a temporary directory. Nothing outside the temporary directory is touched.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import perdix_kilavuz as pk  # noqa: E402

HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout"
    " inode\n"
)


def row(n: int, local: str, state: str, inode: int, uid: int = 0, remote: str = "") -> str:
    remote = remote or ("0" * (len(local) - 5) + ":0000")
    return (
        f"   {n}: {local} {remote} {state} 00000000:00000000 00:00000000 00000000"
        f"  {uid}        0 {inode} 1 0000000000000000 100 0 0 10 0\n"
    )


TCP = HEADER + "".join(
    [
        row(0, "0100007F:0277", "0A", 1001),  # 127.0.0.1:631, loopback
        row(1, "00000000:0016", "0A", 1002),  # 0.0.0.0:22
        row(2, "00000000:01BD", "0A", 1003),  # 0.0.0.0:445
        row(3, "0501A8C0:A2B4", "01", 1004, remote="0101A8C0:01BB"),  # established, ignored
        row(4, "00000000:0050", "0A", 1005, uid=33),  # 0.0.0.0:80
    ]
)
TCP6 = HEADER + "".join(
    [
        row(0, "00000000000000000000000000000000:1F90", "0A", 2001),  # [::]:8080
        row(1, "00000000000000000000000001000000:1538", "0A", 2002),  # [::1]:5432, loopback
        row(2, "0000000000000000FFFF00000501A8C0:0D3D", "0A", 2003),  # ::ffff:192.168.1.5:3389
    ]
)
UDP = HEADER + "".join(
    [
        row(0, "00000000:14E9", "07", 3001),  # 0.0.0.0:5353
        row(1, "00000000:0000", "07", 3002),  # port 0, ignored
        row(2, "0100007F:0035", "07", 3003),  # 127.0.0.1:53, loopback
    ]
)


# --- open ports ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hex_address, expected",
    [
        ("0100007F:0277", ("127.0.0.1", 631)),
        ("00000000:0016", ("0.0.0.0", 22)),
        ("0501A8C0:1F90", ("192.168.1.5", 8080)),
        ("00000000000000000000000000000000:0016", ("::", 22)),
        ("00000000000000000000000001000000:0050", ("::1", 80)),
        ("0000000000000000FFFF00000501A8C0:0D3D", ("192.168.1.5", 3389)),
        ("B80D0120000000000000000001000000:01BB", ("2001:db8::1", 443)),
    ],
)
def test_decode_proc_address(hex_address, expected):
    assert pk.decode_proc_address(hex_address) == expected


def test_parse_proc_net_keeps_only_listening_tcp():
    sockets = pk.parse_proc_net(TCP, "tcp")
    assert [(s.address, s.port) for s in sockets] == [
        ("127.0.0.1", 631),
        ("0.0.0.0", 22),
        ("0.0.0.0", 445),
        ("0.0.0.0", 80),
    ]
    assert sockets[3].uid == 33 and sockets[3].inode == 1005
    assert all(s.protocol == "tcp" for s in sockets)


def test_parse_proc_net_udp_and_ipv6():
    udp = pk.parse_proc_net(UDP, "udp")
    assert [(s.protocol, s.address, s.port) for s in udp] == [
        ("udp", "0.0.0.0", 5353),
        ("udp", "127.0.0.1", 53),
    ]
    tcp6 = pk.parse_proc_net(TCP6, "tcp6")
    assert [s.label() for s in tcp6] == [
        "tcp [::]:8080",
        "tcp [::1]:5432",
        "tcp 192.168.1.5:3389",
    ]


def test_parse_proc_net_ignores_garbage():
    assert pk.parse_proc_net("header only\n", "tcp") == []
    assert pk.parse_proc_net(HEADER + "   short line\n", "tcp") == []


def test_exposed_means_not_loopback():
    make = lambda address: pk.ListeningSocket("tcp", address, 1, 0, 0)  # noqa: E731
    assert make("0.0.0.0").exposed and make("::").exposed and make("192.168.1.5").exposed
    assert not make("127.0.0.1").exposed and not make("::1").exposed


def fake_proc(root: Path, processes: dict[int, tuple[str, list[str]]]) -> Path:
    """A minimal /proc: <pid>/comm and <pid>/fd/<n> symlinks."""
    for pid, (name, targets) in processes.items():
        fd_dir = root / str(pid) / "fd"
        fd_dir.mkdir(parents=True)
        (root / str(pid) / "comm").write_text(name + "\n")
        for n, target in enumerate(targets):
            (fd_dir / str(n)).symlink_to(target)
    (root / "self").mkdir()  # non-numeric entries are ignored
    return root


def test_socket_owners(tmp_path):
    proc = fake_proc(
        tmp_path / "proc",
        {
            100: ("sshd", ["/dev/null", "socket:[1002]"]),
            200: ("smbd", ["socket:[1003]", "pipe:[77]", "socket:[9999]"]),
        },
    )
    owners = pk.socket_owners(str(proc))
    assert owners[1002] == "sshd (pid 100)"
    assert owners[1003] == "smbd (pid 200)"
    assert 77 not in owners


def test_list_listening_sockets(tmp_path):
    files = {}
    for name, text in {"tcp": TCP, "tcp6": TCP6, "udp": UDP}.items():
        files[name] = str(tmp_path / name)
        Path(files[name]).write_text(text)
    files["udp6"] = str(tmp_path / "missing")  # e.g. IPv6 disabled: skipped
    proc = fake_proc(tmp_path / "proc", {100: ("sshd", ["socket:[1002]"])})

    sockets = pk.list_listening_sockets(files, proc=str(proc))
    labels = [s.label() for s in sockets]
    assert labels == sorted(set(labels), key=labels.index)  # no duplicates
    assert "tcp 0.0.0.0:22" in labels and "udp 0.0.0.0:5353" in labels
    assert "tcp 192.168.1.5:3389" in labels
    ssh = next(s for s in sockets if s.port == 22)
    assert ssh.process == "sshd (pid 100)"


def test_list_listening_sockets_deduplicates_keeping_owner(tmp_path):
    text = HEADER + row(0, "00000000:0016", "0A", 1) + row(1, "00000000:0016", "0A", 2)
    (tmp_path / "tcp").write_text(text)
    proc = fake_proc(tmp_path / "proc", {100: ("sshd", ["socket:[2]"])})
    sockets = pk.list_listening_sockets({"tcp": str(tmp_path / "tcp")}, proc=str(proc))
    assert len(sockets) == 1 and sockets[0].process == "sshd (pid 100)"


def test_audit_ports_severities():
    sockets = (
        pk.parse_proc_net(TCP, "tcp")
        + pk.parse_proc_net(TCP6, "tcp6")
        + pk.parse_proc_net(UDP, "udp")
    )
    sockets[2].process = "smbd (pid 200)"  # 0.0.0.0:445
    findings = {f.target: f for f in pk.audit_ports(sockets)}

    assert "tcp 127.0.0.1:631" not in findings  # loopback is fine
    assert "tcp [::1]:5432" not in findings
    assert "udp 127.0.0.1:53" not in findings
    assert findings["tcp 0.0.0.0:445"].severity == "yüksek"
    assert "smbd (pid 200)" in findings["tcp 0.0.0.0:445"].message
    assert findings["tcp 192.168.1.5:3389"].severity == "yüksek"  # RDP via ipv4-mapped
    assert findings["tcp 0.0.0.0:22"].severity == "bilgi"
    assert findings["udp 0.0.0.0:5353"].severity == "bilgi"
    assert findings["tcp 0.0.0.0:80"].severity == "orta"  # unknown privileged TCP port
    assert findings["tcp [::]:8080"].severity == "düşük"
    assert "sahibi bilinmiyor" in findings["tcp [::]:8080"].message
    assert all(f.category == "port" and f.suggestion for f in findings.values())


# --- file permissions ---------------------------------------------------------------------------


def make_file(path: Path, mode: int, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.chmod(path, mode)
    return path


@pytest.fixture
def tree(tmp_path):
    """A small tree with one example of each problem and some healthy entries."""
    root = tmp_path / "scan"
    make_file(root / "ok.conf", 0o644)
    make_file(root / "world_writable.conf", 0o666)
    make_file(root / "bin" / "suid_tool", 0o4755)
    make_file(root / "bin" / "sgid_tool", 0o2755)
    make_file(root / "bin" / "normal_tool", 0o755)
    (root / "open_dir").mkdir()
    os.chmod(root / "open_dir", 0o777)
    (root / "shared").mkdir()
    os.chmod(root / "shared", 0o1777)  # world-writable with sticky bit, like /tmp: fine
    (root / "link_to_ww").symlink_to(root / "world_writable.conf")  # symlinks are not judged
    return root


def test_check_entry(tree):
    def check(name):
        path = str(tree / name)
        return pk.check_entry(path, os.lstat(path))

    assert check("ok.conf") is None
    assert check("bin/normal_tool") is None
    assert check("shared") is None
    assert check("link_to_ww") is None

    ww = check("world_writable.conf")
    assert ww.severity == "yüksek" and "0666" in ww.message and "chmod o-w" in ww.suggestion
    open_dir = check("open_dir")
    assert open_dir.severity == "yüksek" and "sticky" in open_dir.message
    suid = check("bin/suid_tool")
    assert suid.severity == "orta" and "SUID" in suid.message and "4755" in suid.message
    assert "SGID" in check("bin/sgid_tool").message


def test_check_entry_expected_setid(tree, monkeypatch):
    path = str(tree / "bin" / "suid_tool")
    monkeypatch.setattr(pk, "EXPECTED_SETID", pk.EXPECTED_SETID | {path})
    assert pk.check_entry(path, os.lstat(path)) is None


def test_walk_files(tree):
    paths = {path for path, _ in pk.walk_files([str(tree)])}
    assert str(tree) in paths and str(tree / "bin" / "suid_tool") in paths
    assert str(tree / "link_to_ww") in paths  # listed, but never followed
    assert len(list(pk.walk_files([str(tree)], limit=3))) == 3
    assert list(pk.walk_files([str(tree / "does-not-exist")])) == []


def test_walk_does_not_follow_directory_symlinks(tmp_path):
    outside = tmp_path / "outside"
    make_file(outside / "secret.conf", 0o666)
    root = tmp_path / "root"
    root.mkdir()
    (root / "jump").symlink_to(outside, target_is_directory=True)
    paths = {path for path, _ in pk.walk_files([str(root)])}
    assert str(root / "jump") in paths
    assert not any("secret.conf" in p for p in paths)


def test_is_skipped():
    assert pk.is_skipped("/proc") and pk.is_skipped("/proc/1/fd") and pk.is_skipped("/run/user")
    assert not pk.is_skipped("/process") and not pk.is_skipped("/runner/x")
    assert not pk.is_skipped("/etc/passwd")


def test_skip_prefixes_apply_below_roots_only(tmp_path, monkeypatch):
    make_file(tmp_path / "a" / "skipme" / "f", 0o644)
    monkeypatch.setattr(pk, "SKIP_PREFIXES", (str(tmp_path / "a" / "skipme"),))
    walked = {p for p, _ in pk.walk_files([str(tmp_path / "a")])}
    assert str(tmp_path / "a" / "skipme") not in walked  # met during the walk: skipped
    walked = {p for p, _ in pk.walk_files([str(tmp_path / "a" / "skipme")])}
    assert str(tmp_path / "a" / "skipme" / "f") in walked  # asked for explicitly: scanned


def test_audit_sensitive_files(tmp_path):
    shadow = make_file(tmp_path / "shadow", 0o644)  # world-readable password hashes
    good_shadow = make_file(tmp_path / "shadow_ok", 0o640)
    sudoers = make_file(tmp_path / "sudoers", 0o440)
    conf = make_file(tmp_path / "perdix.conf", 0o666)
    rules = {
        str(shadow): pk.SENSITIVE_FILES["/etc/shadow"],
        str(good_shadow): pk.SENSITIVE_FILES["/etc/shadow"],
        str(sudoers): pk.SENSITIVE_FILES["/etc/sudoers"],
        str(conf): pk.SENSITIVE_FILES["/etc/perdix/perdix.conf"],
        str(tmp_path / "absent"): (0o022, "yok"),
    }
    make_file(tmp_path / "ssh" / "ssh_host_ed25519_key", 0o644)
    make_file(tmp_path / "ssh" / "ssh_host_rsa_key", 0o600)

    findings = {
        f.target: f
        for f in pk.audit_sensitive_files(rules, str(tmp_path / "ssh" / "ssh_host_*_key"))
    }

    assert set(findings) == {
        str(shadow),
        str(conf),
        str(tmp_path / "ssh" / "ssh_host_ed25519_key"),
    }
    assert findings[str(shadow)].suggestion == f"chmod 0640 '{shadow}'"
    assert findings[str(conf)].suggestion == f"chmod 0644 '{conf}'"
    assert "0600" in findings[str(tmp_path / "ssh" / "ssh_host_ed25519_key")].suggestion
    assert all(f.severity == "yüksek" and f.category == "izin" for f in findings.values())


def test_audit_permissions(tree):
    findings = pk.audit_permissions([str(tree)], sensitive={}, host_key_glob="")
    targets = {Path(f.target).relative_to(tree).as_posix() for f in findings}
    assert targets == {"world_writable.conf", "open_dir", "bin/suid_tool", "bin/sgid_tool"}


def test_audit_is_read_only(tree):
    def snapshot():
        return {p: (i.st_mode, i.st_uid, i.st_size) for p, i in pk.walk_files([str(tree)])}

    before = snapshot()
    pk.audit_permissions([str(tree)], sensitive={}, host_key_glob="")
    assert snapshot() == before


# --- report -------------------------------------------------------------------------------------


def test_format_report_orders_by_severity():
    report = pk.AuditReport(
        findings=[
            pk.Finding("port", "bilgi", "tcp 0.0.0.0:22", "SSH", "-"),
            pk.Finding("izin", "yüksek", "/etc/x", "açık", "chmod o-w '/etc/x'"),
            pk.Finding("izin", "orta", "/opt/y", "SUID", "chmod u-s '/opt/y'"),
        ],
        as_root=True,
    )
    text = pk.format_report(report)
    assert "Özet: 1 yüksek, 1 orta, 0 düşük, 1 bilgi" in text
    assert text.index("[YÜKSEK]") < text.index("[ORTA]") < text.index("[BİLGİ]")
    assert "root olmadan" not in text
    assert "Öneri: chmod o-w '/etc/x'" in text


def test_format_report_empty_and_not_root():
    text = pk.format_report(pk.AuditReport(as_root=False))
    assert "Sorun bulunmadı." in text and "root olmadan" in text


def test_main_json_and_exit_code(tree, monkeypatch, capsys):
    monkeypatch.setattr(pk, "audit_sensitive_files", lambda *a, **k: [])
    assert pk.main(["--perms-only", "--json", "--paths", str(tree)]) == 1
    data = json.loads(capsys.readouterr().out)
    assert {"as_root", "findings"} <= data.keys()
    assert data["findings"][0]["severity"] == "yüksek"
    assert {f["category"] for f in data["findings"]} == {"izin"}


def test_main_clean_tree_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pk, "audit_sensitive_files", lambda *a, **k: [])
    make_file(tmp_path / "clean" / "a.conf", 0o644)
    assert pk.main(["--perms-only", "--paths", str(tmp_path / "clean")]) == 0
    assert "Sorun bulunmadı." in capsys.readouterr().out


def test_main_ports_only_uses_port_audit(monkeypatch, capsys):
    sockets = pk.parse_proc_net(TCP, "tcp")
    monkeypatch.setattr(pk, "list_listening_sockets", lambda: sockets)
    monkeypatch.setattr(pk, "ss_process_names", lambda: {("tcp", 445): "smbd"})
    monkeypatch.setattr(pk, "audit_permissions", lambda *a, **k: pytest.fail("perms scanned"))
    assert pk.main(["--ports-only"]) == 1
    out = capsys.readouterr().out
    assert "tcp 0.0.0.0:445" in out and "smbd" in out


def test_mode_constants_make_sense():
    # /etc/shadow may be 0640 (root:shadow) but never group-writable or world-anything.
    forbidden = pk.SENSITIVE_FILES["/etc/shadow"][0]
    assert not 0o640 & forbidden and 0o644 & forbidden and 0o660 & forbidden
    assert not 0o440 & pk.SENSITIVE_FILES["/etc/sudoers"][0]
    assert stat.S_IMODE(0o104755) == 0o4755


# --- hardening (remediation) --------------------------------------------------------------------

needs_root = pytest.mark.skipif(os.geteuid() != 0, reason="chown needs root")


def mode_of(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_findings_carry_safe_fixes_only(tree):
    findings = {
        Path(f.target).name: f
        for f in pk.audit_permissions([str(tree)], sensitive={}, host_key_glob="")
    }
    assert findings["world_writable.conf"].fix.action == "chmod"
    assert findings["world_writable.conf"].fix.clear_bits == 0o002
    assert findings["open_dir"].fix.clear_bits == 0o002
    assert findings["suid_tool"].fix is None  # SUID removal stays manual
    assert all(f.fix is None for f in pk.audit_ports(pk.parse_proc_net(TCP, "tcp")))


def test_remediate_plan_changes_nothing(tree, tmp_path):
    findings = pk.audit_permissions([str(tree)], sensitive={}, host_key_glob="")
    log = tmp_path / "log" / "kilavuz.log"
    results = pk.remediate(findings, apply=False, log_path=str(log))
    status = {Path(r.finding.target).name: r.status for r in results}
    assert status["world_writable.conf"] == pk.PLANNED
    assert status["suid_tool"] == pk.MANUAL
    assert mode_of(tree / "world_writable.conf") == 0o666
    assert not log.exists()


def test_remediate_apply(tree, tmp_path):
    findings = pk.audit_permissions([str(tree)], sensitive={}, host_key_glob="")
    log = tmp_path / "log" / "kilavuz.log"
    results = {Path(r.finding.target).name: r for r in pk.remediate(findings, True, str(log))}

    assert results["world_writable.conf"].status == pk.APPLIED
    assert results["world_writable.conf"].detail == "izinler 0666 → 0664"
    assert mode_of(tree / "world_writable.conf") == 0o664
    assert mode_of(tree / "open_dir") == 0o775
    assert results["suid_tool"].status == pk.MANUAL
    assert mode_of(tree / "bin" / "suid_tool") == 0o4755  # untouched
    assert mode_of(tree / "shared") == 0o1777  # never flagged, never touched

    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert {Path(e["path"]).name for e in entries} == {"world_writable.conf", "open_dir"}
    assert all(e["status"] == pk.APPLIED and e["action"] == "chmod" for e in entries)

    # A second run finds nothing left to fix.
    again = pk.audit_permissions([str(tree)], sensitive={}, host_key_glob="")
    assert {Path(f.target).name for f in again} == {"suid_tool", "sgid_tool"}


def test_sensitive_file_fix(tmp_path):
    shadow = make_file(tmp_path / "shadow", 0o666)
    rules = {str(shadow): pk.SENSITIVE_FILES["/etc/shadow"]}
    (finding,) = pk.audit_sensitive_files(rules, host_key_glob="")
    assert pk.apply_fix(finding.fix) == (pk.APPLIED, "izinler 0666 → 0640")
    assert mode_of(shadow) == 0o640
    assert pk.apply_fix(finding.fix)[0] == pk.ALREADY_OK


@needs_root
def test_chown_fix(tmp_path):
    conf = make_file(tmp_path / "perdix.conf", 0o644)
    os.chown(conf, 1000, 1000)
    info = os.stat(conf)
    fix = pk.Fix("chown", str(conf), info.st_dev, info.st_ino, uid=0)
    assert pk.apply_fix(fix) == (pk.APPLIED, "sahibi uid 1000 → 0")
    assert os.stat(conf).st_uid == 0 and os.stat(conf).st_gid == 1000  # group kept
    assert pk.apply_fix(fix)[0] == pk.ALREADY_OK


def test_fix_refuses_replaced_file(tmp_path):
    target = make_file(tmp_path / "target.conf", 0o666)
    (finding,) = [f for p, i in pk.walk_files([str(target)]) if (f := pk.check_entry(p, i))]
    # The file is swapped for another one after the scan (e.g. by its owner). The old one is
    # kept around so the new file can't reuse its inode number.
    target.rename(tmp_path / "old.conf")
    make_file(target, 0o666)
    assert pk.apply_fix(finding.fix)[0] == pk.SKIPPED
    assert mode_of(target) == 0o666


def test_fix_refuses_symlink_swap(tmp_path):
    victim = make_file(tmp_path / "victim", 0o666)
    target = make_file(tmp_path / "target.conf", 0o666)
    (finding,) = [f for p, i in pk.walk_files([str(target)]) if (f := pk.check_entry(p, i))]
    target.unlink()
    target.symlink_to(victim)
    assert pk.apply_fix(finding.fix) == (pk.SKIPPED, "dosya bir sembolik bağlantı; dokunulmadı")
    assert mode_of(victim) == 0o666


def test_fix_missing_file(tmp_path):
    fix = pk.Fix("chmod", str(tmp_path / "gone"), 0, 0, clear_bits=0o002)
    assert pk.apply_fix(fix)[0] == pk.SKIPPED


def test_format_remediation():
    finding = pk.Finding("izin", "yüksek", "/etc/x", "açık", "chmod o-w '/etc/x'")
    port = pk.Finding("port", "yüksek", "tcp 0.0.0.0:23", "Telnet", "servisi durdurun")
    info = pk.Finding("port", "bilgi", "tcp 0.0.0.0:22", "SSH", "-")
    plan = pk.format_remediation(
        [
            pk.Remediation(finding, pk.PLANNED, "chmod o-w '/etc/x'"),
            pk.Remediation(port, pk.MANUAL, port.suggestion),
            pk.Remediation(info, pk.MANUAL, info.suggestion),
        ],
        applied=False,
    )
    assert "hiçbir şey değişmedi" in plan and "--apply" in plan
    assert "tcp 0.0.0.0:23: servisi durdurun" in plan
    assert "tcp 0.0.0.0:22" not in plan  # info-only findings need no action
    done = pk.format_remediation(
        [pk.Remediation(finding, pk.APPLIED, "izinler 0666 → 0664")], applied=True
    )
    assert "[uygulandı] /etc/x: izinler 0666 → 0664" in done


def test_main_fix_is_dry_run(tree, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pk, "audit_sensitive_files", lambda *a, **k: [])
    log = tmp_path / "kilavuz.log"
    assert pk.main(["--perms-only", "--fix", "--paths", str(tree), "--log", str(log)]) == 1
    assert "Sertleştirme planı" in capsys.readouterr().out
    assert mode_of(tree / "world_writable.conf") == 0o666 and not log.exists()


def test_main_apply(tree, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pk, "audit_sensitive_files", lambda *a, **k: [])
    log = tmp_path / "kilavuz.log"
    args = ["--perms-only", "--apply", "--json", "--paths", str(tree), "--log", str(log)]
    assert pk.main(args) == 0  # every high finding fixed; SUID (orta) left for the user
    data = json.loads(capsys.readouterr().out)
    statuses = {Path(r["target"]).name: r["status"] for r in data["remediation"]}
    assert statuses == {
        "world_writable.conf": pk.APPLIED,
        "open_dir": pk.APPLIED,
        "suid_tool": pk.MANUAL,
        "sgid_tool": pk.MANUAL,
    }
    assert mode_of(tree / "world_writable.conf") == 0o664 and log.exists()
