#!/usr/bin/env python3
"""PERDIX Sentinel (PERDIX Kılavuz): audit of open ports and weak file permissions, with hardening.

The audit only *looks*: it reads the kernel's socket tables (and `ss`, when installed) and file
metadata on this machine and reports findings with a suggested fix. It never contacts other
hosts. Hardening is opt-in: --fix shows which fixes can be applied automatically, and only
--apply changes anything. Automatic fixes are limited to tightening permissions (removing
world-write, tightening sensitive files, giving them back to root); stopping services and
removing SUID/SGID bits stay manual, because they can break a working system.

Findings and the report are in Turkish (the PERDIX Kılavuz language); code and docstrings are
English like the rest of the PERDIX OS repository.

Usage:
  python3 perdix_kilavuz.py                 # Turkish report on the terminal
  python3 perdix_kilavuz.py --json          # machine-readable findings
  python3 perdix_kilavuz.py --paths /etc /opt --ports-only / --perms-only
  python3 perdix_kilavuz.py --fix           # hardening plan (changes nothing)
  sudo python3 perdix_kilavuz.py --apply    # apply the automatic fixes, log to /var/log/perdix
Run as root for a complete picture: as a normal user, sockets and files belonging to other
users can't all be attributed or read (the report says when that happened).

Functions:
  list_listening_sockets()   sockets in LISTEN (TCP) or bound (UDP) state, from /proc/net
  audit_ports()              findings for sockets reachable from the network
  audit_permissions()        findings for world-writable files, SUID/SGID, sensitive files
  run_audit() / format_report()
  remediate()                plan or apply the automatic fixes for a list of findings
"""

from __future__ import annotations

import argparse
import errno
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

# ---------------------------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------------------------

SEVERITIES = ("bilgi", "düşük", "orta", "yüksek")  # info, low, medium, high
SEVERITY_ORDER = {name: rank for rank, name in enumerate(SEVERITIES)}
# Turkish capitals: str.upper() would give "BILGI" instead of "BİLGİ".
SEVERITY_LABELS = {"bilgi": "BİLGİ", "düşük": "DÜŞÜK", "orta": "ORTA", "yüksek": "YÜKSEK"}


@dataclass
class Fix:
    """An automatic fix for one file. dev/ino pin the exact file that was scanned, so the fix
    is never applied to something that replaced it in the meantime."""

    action: str  # "chmod" (remove clear_bits) or "chown" (give the file to uid)
    path: str
    dev: int
    ino: int
    clear_bits: int = 0
    uid: int = 0


@dataclass
class Finding:
    """One audit result, written for the user in Turkish."""

    category: str  # "port" or "izin" (permission)
    severity: str  # one of SEVERITIES
    target: str  # "tcp 0.0.0.0:22" or a file path
    message: str  # what was found
    suggestion: str  # what to do about it
    fix: Fix | None = None  # set when the fix is safe to apply automatically


# ---------------------------------------------------------------------------------------------
# Open ports
# ---------------------------------------------------------------------------------------------

PROC_NET_FILES = {
    "tcp": "/proc/net/tcp",
    "tcp6": "/proc/net/tcp6",
    "udp": "/proc/net/udp",
    "udp6": "/proc/net/udp6",
}
TCP_LISTEN = "0A"
UDP_UNCONNECTED = "07"

# Services that should not be reachable from the network on a gaming kiosk.
RISKY_PORTS = {
    21: "FTP (şifresiz dosya aktarımı)",
    23: "Telnet (şifresiz uzak oturum)",
    69: "TFTP",
    111: "rpcbind / NFS",
    135: "Windows RPC",
    139: "NetBIOS / Samba",
    445: "SMB / Samba dosya paylaşımı",
    512: "rexec",
    513: "rlogin",
    514: "rsh",
    2049: "NFS",
    3306: "MySQL / MariaDB",
    3389: "Uzak masaüstü (RDP)",
    5432: "PostgreSQL",
    5900: "VNC uzak masaüstü",
    6379: "Redis",
    9200: "Elasticsearch",
    11211: "Memcached",
    27017: "MongoDB",
}
# Ports that are normal for a gaming PC (Steam, game clients, local discovery).
EXPECTED_PORTS = {
    22: "SSH",
    5353: "mDNS (yerel ağ keşfi)",
    27036: "Steam yerel yayın / Remote Play",
    27037: "Steam yerel yayın",
    27031: "Steam yerel yayın",
    27032: "Steam yerel yayın",
    27060: "Steam",
    68: "DHCP istemcisi",
}


@dataclass
class ListeningSocket:
    """A socket accepting traffic: TCP in LISTEN state or a bound UDP socket."""

    protocol: str  # "tcp" or "udp"
    address: str  # textual IP, e.g. "0.0.0.0", "::", "127.0.0.1"
    port: int
    inode: int
    uid: int
    process: str = ""  # "steam (pid 1234)" when known

    @property
    def exposed(self) -> bool:
        """Reachable from other machines (not bound to a loopback address)."""
        return not ipaddress.ip_address(self.address).is_loopback

    def label(self) -> str:
        host = f"[{self.address}]" if ":" in self.address else self.address
        return f"{self.protocol} {host}:{self.port}"


def decode_proc_address(hex_address: str) -> tuple[str, int]:
    """'0100007F:0277' -> ('127.0.0.1', 631). /proc/net stores the IP as 32-bit words in host
    (little-endian) byte order, the port in big-endian hex."""
    ip_hex, port_hex = hex_address.split(":")
    raw = bytes.fromhex(ip_hex)
    words = b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))
    ip = ipaddress.IPv4Address(words) if len(words) == 4 else ipaddress.IPv6Address(words)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip), int(port_hex, 16)


def parse_proc_net(text: str, protocol: str) -> list[ListeningSocket]:
    """Listening/bound sockets from the contents of a /proc/net/{tcp,tcp6,udp,udp6} file."""
    wanted_state = TCP_LISTEN if protocol.startswith("tcp") else UDP_UNCONNECTED
    sockets = []
    for line in text.splitlines()[1:]:  # first line is the header
        fields = line.split()
        if len(fields) < 10 or fields[3] != wanted_state:
            continue
        address, port = decode_proc_address(fields[1])
        if port == 0:
            continue
        sockets.append(
            ListeningSocket(
                protocol.rstrip("6"), address, port, inode=int(fields[9]), uid=int(fields[7])
            )
        )
    return sockets


def socket_owners(proc: str = "/proc") -> dict[int, str]:
    """Socket inode -> 'name (pid N)' for every process whose fds we may read."""
    owners: dict[int, str] = {}
    for pid in (p for p in os.listdir(proc) if p.isdigit()):
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
            with open(os.path.join(proc, pid, "comm"), encoding="utf-8") as f:
                name = f.read().strip()
        except OSError:
            continue  # gone, or another user's process
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            match = re.fullmatch(r"socket:\[(\d+)\]", target)
            if match:
                owners.setdefault(int(match.group(1)), f"{name} (pid {pid})")
    return owners


def list_listening_sockets(
    files: dict[str, str] | None = None, proc: str = "/proc"
) -> list[ListeningSocket]:
    """All listening TCP and bound UDP sockets on this machine, IPv4 and IPv6."""
    sockets: list[ListeningSocket] = []
    for protocol, path in (files or PROC_NET_FILES).items():
        try:
            with open(path, encoding="ascii") as f:
                sockets += parse_proc_net(f.read(), protocol)
        except OSError:
            continue  # e.g. IPv6 disabled
    owners = socket_owners(proc)
    for sock in sockets:
        sock.process = owners.get(sock.inode, "")
    # One entry per protocol/address/port (SO_REUSEPORT can list the same one several times);
    # keep the copy whose owner is known.
    unique: dict[tuple[str, str, int], ListeningSocket] = {}
    for sock in sockets:
        key = (sock.protocol, sock.address, sock.port)
        if key not in unique or (sock.process and not unique[key].process):
            unique[key] = sock
    return sorted(unique.values(), key=lambda s: (s.protocol, s.port, s.address))


def ss_process_names() -> dict[tuple[str, int], str]:
    """(protocol, port) -> program name from `ss -tulnpH`, if ss is installed (optional)."""
    if shutil.which("ss") is None:
        return {}
    try:
        output = subprocess.run(
            ["ss", "-tulnpH"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    names = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        port = fields[4].rsplit(":", 1)[-1]
        program = re.search(r'users:\(\("([^"]+)"', line)
        if port.isdigit() and program:
            names[(fields[0], int(port))] = program.group(1)
    return names


def audit_ports(sockets: Iterable[ListeningSocket] | None = None) -> list[Finding]:
    """Findings for sockets other machines can reach. Loopback-only sockets are fine."""
    sockets = list_listening_sockets() if sockets is None else list(sockets)
    findings = []
    for sock in sockets:
        if not sock.exposed:
            continue
        who = f" — {sock.process}" if sock.process else ""
        if sock.port in RISKY_PORTS:
            findings.append(
                Finding(
                    "port",
                    "yüksek",
                    sock.label(),
                    f"Ağa açık riskli servis: {RISKY_PORTS[sock.port]}{who}.",
                    "Kullanmıyorsanız servisi durdurun (systemctl disable --now <servis>) "
                    "ya da yalnızca 127.0.0.1 adresini dinleyecek şekilde ayarlayın.",
                )
            )
        elif sock.port in EXPECTED_PORTS:
            findings.append(
                Finding(
                    "port",
                    "bilgi",
                    sock.label(),
                    f"Beklenen servis ağa açık: {EXPECTED_PORTS[sock.port]}{who}.",
                    "Gerekli değilse kapatılabilir; aksi halde işlem gerekmez.",
                )
            )
        else:
            severity = "orta" if sock.protocol == "tcp" and sock.port < 1024 else "düşük"
            findings.append(
                Finding(
                    "port",
                    severity,
                    sock.label(),
                    f"Ağa açık port{who or ' (sahibi bilinmiyor)'}.",
                    "Bu programı tanımıyorsanız inceleyin; gerekmiyorsa kapatın veya "
                    "güvenlik duvarında engelleyin.",
                )
            )
    return findings


# ---------------------------------------------------------------------------------------------
# File permissions
# ---------------------------------------------------------------------------------------------

DEFAULT_SCAN_ROOTS = ("/etc", "/usr/local", "/opt", "/var/log", "/home")
SKIP_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/tmp", "/var/tmp")
MAX_FILES = 200_000  # stop a runaway walk on huge trees

# SUID/SGID programs that a normal Arch Linux system ships.
EXPECTED_SETID = {
    "/usr/bin/chage",
    "/usr/bin/chfn",
    "/usr/bin/chsh",
    "/usr/bin/expiry",
    "/usr/bin/gpasswd",
    "/usr/bin/groupmems",
    "/usr/bin/mount",
    "/usr/bin/newgrp",
    "/usr/bin/passwd",
    "/usr/bin/pkexec",
    "/usr/bin/su",
    "/usr/bin/sudo",
    "/usr/bin/umount",
    "/usr/bin/unix_chkpwd",
    "/usr/bin/fusermount",
    "/usr/bin/fusermount3",
    "/usr/bin/ksu",
    "/usr/bin/sg",
    "/usr/bin/write",
    "/usr/bin/wall",
    "/usr/bin/crontab",
    "/usr/bin/locate",
    "/usr/lib/polkit-1/polkit-agent-helper-1",
    "/usr/lib/dbus-daemon-launch-helper",
    "/usr/lib/ssh/ssh-keysign",
    "/usr/lib/Xorg.wrap",
    "/usr/bin/Xorg.wrap",
    "/usr/lib/utempter/utempter",
    "/usr/bin/newuidmap",
    "/usr/bin/newgidmap",
    "/opt/google/chrome/chrome-sandbox",
    "/usr/lib/chromium/chrome-sandbox",
}

# Sensitive files: path -> (permission bits that must NOT be set, readable description).
SENSITIVE_FILES = {
    "/etc/shadow": (0o077 & ~0o040, "parola özetleri"),  # root:root 600 or root:shadow 640
    "/etc/gshadow": (0o077 & ~0o040, "grup parola özetleri"),
    "/etc/sudoers": (0o137, "sudo yetkileri"),  # at most 440
    "/etc/ssh/sshd_config": (0o022, "SSH sunucu ayarları"),
    "/etc/passwd": (0o022, "kullanıcı listesi"),
    "/etc/group": (0o022, "grup listesi"),
    "/etc/perdix/perdix.conf": (0o022, "PERDIX sistem ayarları"),
    "/etc/perdix/drivers.conf": (0o022, "PERDIX GPU ayarları"),
    "/usr/local/bin/perdix-tune": (0o022, "PERDIX yetkili yardımcı programı"),
}
SSH_HOST_KEY_GLOB = "/etc/ssh/ssh_host_*_key"


def is_skipped(path: str) -> bool:
    """True for a pseudo-filesystem or scratch path (/proc, /proc/1, but not /process)."""
    return any(path == p or path.startswith(p + "/") for p in SKIP_PREFIXES)


def walk_files(
    roots: Iterable[str], limit: int = MAX_FILES
) -> Iterator[tuple[str, os.stat_result]]:
    """(path, lstat) for every entry under the roots. Symlinks are not followed; unreadable
    directories are skipped; pseudo-filesystems are not entered from outside."""
    seen = 0
    # Pseudo-filesystems are skipped when the walk wanders into them (/proc when scanning /), but
    # a root the user asked for is scanned in full, even one inside them (e.g. /tmp/something).
    stack = [(os.path.abspath(r), is_skipped(os.path.abspath(r))) for r in roots]
    while stack:
        current, inside_skipped = stack.pop()
        if not inside_skipped and is_skipped(current):
            continue
        try:
            info = os.lstat(current)
        except OSError:
            continue
        yield current, info
        seen += 1
        if seen >= limit:
            return
        if stat.S_ISDIR(info.st_mode):
            try:
                with os.scandir(current) as entries:
                    stack.extend((entry.path, inside_skipped) for entry in entries)
            except OSError:
                continue


def check_entry(path: str, info: os.stat_result) -> Finding | None:
    """The permission problem of one file or directory, if any."""
    mode = info.st_mode
    if stat.S_ISLNK(mode):
        return None
    octal = f"{stat.S_IMODE(mode):04o}"
    if stat.S_ISDIR(mode):
        if mode & stat.S_IWOTH and not mode & stat.S_ISVTX:
            return Finding(
                "izin",
                "yüksek",
                path,
                f"Herkesin yazabildiği klasör, yapışkan bit (sticky) yok ({octal}).",
                f"chmod o-w '{path}'  (paylaşılan bir klasörse: chmod +t '{path}')",
                Fix("chmod", path, info.st_dev, info.st_ino, clear_bits=stat.S_IWOTH),
            )
        return None
    if not stat.S_ISREG(mode):
        return None
    if mode & stat.S_IWOTH:
        return Finding(
            "izin",
            "yüksek",
            path,
            f"Herkesin değiştirebildiği dosya ({octal}).",
            f"chmod o-w '{path}'",
            Fix("chmod", path, info.st_dev, info.st_ino, clear_bits=stat.S_IWOTH),
        )
    if mode & (stat.S_ISUID | stat.S_ISGID) and path not in EXPECTED_SETID:
        kind = "SUID" if mode & stat.S_ISUID else "SGID"
        return Finding(
            "izin",
            "orta",
            path,
            f"Beklenmeyen {kind} program ({octal}): onu çalıştıran herkese sahibinin "
            "yetkilerini verir.",
            f"Bir paketten gelmiyorsa ve gerekli değilse: chmod u-s,g-s '{path}'",
        )
    return None


def audit_sensitive_files(
    files: dict[str, tuple[int, str]] | None = None, host_key_glob: str = SSH_HOST_KEY_GLOB
) -> list[Finding]:
    """Sensitive files that are readable or writable by more users than they should be."""
    findings = []
    rules = dict(SENSITIVE_FILES if files is None else files)
    for key in sorted(Path("/").glob(host_key_glob.lstrip("/"))) if host_key_glob else []:
        rules[str(key)] = (0o077, "SSH sunucu gizli anahtarı")
    for path, (forbidden, description) in rules.items():
        try:
            info = os.stat(path)
        except OSError:
            continue  # not present on this system
        mode = stat.S_IMODE(info.st_mode)
        if mode & forbidden:
            allowed = mode & ~forbidden
            findings.append(
                Finding(
                    "izin",
                    "yüksek",
                    path,
                    f"Hassas dosya ({description}) fazla açık: {mode:04o}.",
                    f"chmod {allowed:04o} '{path}'",
                    Fix("chmod", path, info.st_dev, info.st_ino, clear_bits=forbidden),
                )
            )
        if info.st_uid != 0 and path.startswith(("/etc/", "/usr/")):
            findings.append(
                Finding(
                    "izin",
                    "yüksek",
                    path,
                    f"Hassas dosya ({description}) root'a ait değil (uid {info.st_uid}).",
                    f"chown root '{path}'",
                    Fix("chown", path, info.st_dev, info.st_ino, uid=0),
                )
            )
    return findings


def audit_permissions(
    roots: Iterable[str] = DEFAULT_SCAN_ROOTS,
    sensitive: dict[str, tuple[int, str]] | None = None,
    host_key_glob: str = SSH_HOST_KEY_GLOB,
) -> list[Finding]:
    """World-writable files/directories and unexpected SUID/SGID under the roots, plus the
    sensitive-file checks."""
    findings = audit_sensitive_files(sensitive, host_key_glob)
    for path, info in walk_files(roots):
        finding = check_entry(path, info)
        if finding is not None:
            findings.append(finding)
    return findings


# ---------------------------------------------------------------------------------------------
# Hardening (remediation)
# ---------------------------------------------------------------------------------------------

REMEDIATION_LOG = "/var/log/perdix/kilavuz.log"
# Remediation states, in Turkish like the report.
PLANNED = "uygulanabilir"  # --fix: would be applied by --apply
MANUAL = "elle"  # no safe automatic fix; follow the suggestion by hand
APPLIED = "uygulandı"
ALREADY_OK = "zaten düzgün"  # fixed in the meantime
SKIPPED = "atlandı"  # the file changed since the scan; left alone
FAILED = "hata"
RESOLVED = (APPLIED, ALREADY_OK)


@dataclass
class Remediation:
    finding: Finding
    status: str  # one of the states above
    detail: str


def apply_fix(fix: Fix) -> tuple[str, str]:
    """Apply one fix; returns (status, detail).

    The file is opened without following symlinks and checked against the device/inode seen
    during the scan, and the change is made on that open file (fchmod/fchown). So a file that
    was swapped for a symlink or another file after the scan is never touched."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(fix.path, flags)
    except OSError as error:
        if error.errno == errno.ELOOP:
            return SKIPPED, "dosya bir sembolik bağlantı; dokunulmadı"
        if error.errno == errno.ENOENT:
            return SKIPPED, "dosya artık yok"
        return FAILED, error.strerror or str(error)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (fix.dev, fix.ino):
            return SKIPPED, "dosya taramadan sonra değişmiş; dokunulmadı"
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            return SKIPPED, "normal dosya ya da klasör değil; dokunulmadı"
        if fix.action == "chmod":
            old = stat.S_IMODE(info.st_mode)
            new = old & ~fix.clear_bits
            if new == old:
                return ALREADY_OK, f"izinler zaten {old:04o}"
            os.fchmod(fd, new)
            return APPLIED, f"izinler {old:04o} → {new:04o}"
        if fix.action == "chown":
            if info.st_uid == fix.uid:
                return ALREADY_OK, f"sahibi zaten uid {fix.uid}"
            os.fchown(fd, fix.uid, -1)
            return APPLIED, f"sahibi uid {info.st_uid} → {fix.uid}"
        return FAILED, f"bilinmeyen işlem: {fix.action}"
    except OSError as error:
        return FAILED, error.strerror or str(error)
    finally:
        os.close(fd)


def remediate(
    findings: Iterable[Finding], apply: bool = False, log_path: str | None = REMEDIATION_LOG
) -> list[Remediation]:
    """The hardening plan for the findings, or (apply=True) the result of carrying it out.

    Only findings with a Fix are applied; ports and SUID/SGID programs are always left to the
    user. Applied changes are appended to log_path as JSON lines (old and new mode or owner),
    so every change can be traced and undone."""
    results = []
    for finding in findings:
        if finding.fix is None:
            results.append(Remediation(finding, MANUAL, finding.suggestion))
        elif not apply:
            results.append(Remediation(finding, PLANNED, finding.suggestion))
        else:
            results.append(Remediation(finding, *apply_fix(finding.fix)))
    if apply and log_path:
        write_remediation_log(results, log_path)
    return results


def write_remediation_log(results: Iterable[Remediation], log_path: str) -> None:
    """Append applied and failed fixes to the log. Logging problems never stop hardening."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries = [
        json.dumps(
            {
                "time": now,
                "path": r.finding.fix.path,
                "action": r.finding.fix.action,
                "status": r.status,
                "detail": r.detail,
            },
            ensure_ascii=False,
        )
        for r in results
        if r.finding.fix is not None and r.status in (APPLIED, FAILED)
    ]
    if not entries:
        return
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(entries) + "\n")
    except OSError:
        pass


def format_remediation(results: list[Remediation], applied: bool) -> str:
    """The hardening plan or its results as Turkish text."""
    title = "Sertleştirme sonuçları" if applied else "Sertleştirme planı (hiçbir şey değişmedi)"
    lines = ["", title, "-" * len(title)]
    automatic = [r for r in results if r.status != MANUAL]
    manual = [r for r in results if r.status == MANUAL and r.finding.severity != "bilgi"]
    if not automatic and not manual:
        lines.append("Yapılacak bir şey yok.")
    for r in automatic:
        lines.append(f"[{r.status}] {r.finding.target}: {r.detail}")
    if automatic and not applied:
        lines.append(
            f"Bu {len(automatic)} düzeltmeyi uygulamak için: "
            "sudo python3 perdix_kilavuz.py --apply"
        )
    if manual:
        lines += ["", "Elle yapılması gerekenler (otomatik uygulanmaz):"]
        lines += [f"  {r.finding.target}: {r.finding.suggestion}" for r in manual]
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    as_root: bool = False

    def sorted_findings(self) -> list[Finding]:
        return sorted(
            self.findings, key=lambda f: (-SEVERITY_ORDER[f.severity], f.category, f.target)
        )


def run_audit(
    ports: bool = True, permissions: bool = True, roots: Iterable[str] = DEFAULT_SCAN_ROOTS
) -> AuditReport:
    """Run the selected checks on this machine. Read-only."""
    report = AuditReport(as_root=os.geteuid() == 0)
    if ports:
        sockets = list_listening_sockets()
        names = ss_process_names()
        for sock in sockets:
            if not sock.process and (sock.protocol, sock.port) in names:
                sock.process = names[(sock.protocol, sock.port)]
        report.findings += audit_ports(sockets)
    if permissions:
        report.findings += audit_permissions(roots)
    return report


def format_report(report: AuditReport) -> str:
    """The report as Turkish text."""
    lines = ["PERDIX Kılavuz — Güvenlik Denetimi", "=" * 36]
    if not report.as_root:
        lines.append(
            "Not: root olmadan çalıştırıldı; bazı portların sahibi ve bazı klasörler "
            "görülemedi."
        )
    counts = {s: sum(f.severity == s for f in report.findings) for s in SEVERITIES}
    lines.append("Özet: " + ", ".join(f"{counts[s]} {s}" for s in reversed(SEVERITIES)))
    if not report.findings:
        lines.append("Sorun bulunmadı.")
    for finding in report.sorted_findings():
        lines += [
            "",
            f"[{SEVERITY_LABELS[finding.severity]}] {finding.target}",
            f"  {finding.message}",
            f"  Öneri: {finding.suggestion}",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PERDIX Kılavuz: açık port ve dosya izni denetimi "
        "(--apply verilmedikçe sistemi değiştirmez)."
    )
    parser.add_argument("--json", action="store_true", help="bulguları JSON olarak yaz")
    parser.add_argument(
        "--paths", nargs="+", default=list(DEFAULT_SCAN_ROOTS), help="izinleri taranacak klasörler"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ports-only", action="store_true", help="yalnızca portları denetle")
    group.add_argument(
        "--perms-only", action="store_true", help="yalnızca dosya izinlerini denetle"
    )
    parser.add_argument(
        "--fix", action="store_true", help="sertleştirme planını göster (hiçbir şey değiştirmez)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="otomatik düzeltmeleri uygula (yalnızca izinler; root gerekir)",
    )
    parser.add_argument("--log", default=REMEDIATION_LOG, help="uygulanan değişikliklerin günlüğü")
    args = parser.parse_args(argv)
    report = run_audit(
        ports=not args.perms_only, permissions=not args.ports_only, roots=args.paths
    )
    findings = report.sorted_findings()
    results = None
    if args.fix or args.apply:
        results = remediate(findings, apply=args.apply, log_path=args.log)
    if args.json:
        data: dict = {"as_root": report.as_root, "findings": [asdict(f) for f in findings]}
        if results is not None:
            data["remediation"] = [
                {"target": r.finding.target, "status": r.status, "detail": r.detail}
                for r in results
            ]
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
        if results is not None:
            print(format_remediation(results, applied=args.apply))
    # Exit code 1 while something high-severity is still open, so scripts can react.
    if results is not None and args.apply:
        resolved = {id(r.finding) for r in results if r.status in RESOLVED}
        findings = [f for f in findings if id(f) not in resolved]
    return 1 if any(f.severity == "yüksek" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
