# PERDIX Sentinel (PERDIX Kılavuz): security audit

`perdix_kilavuz.py` checks this machine for **open ports** and **weak file permissions**, then
prints a Turkish report with a suggested fix for each finding. The audit reads the kernel's
socket tables (`/proc/net/{tcp,tcp6,udp,udp6}`, plus `ss` when installed) and file metadata, and
never contacts other hosts. It changes nothing unless you ask for hardening with `--apply`. It
runs as a plain script, as a set of functions you can import, and as module 06 of PERDIX Control
Center (below).

## Run

```sh
sudo python3 apps/perdix-kilavuz/perdix_kilavuz.py              # full audit, Turkish report
python3 apps/perdix-kilavuz/perdix_kilavuz.py --ports-only      # only listening sockets
python3 apps/perdix-kilavuz/perdix_kilavuz.py --perms-only --paths /etc /opt
python3 apps/perdix-kilavuz/perdix_kilavuz.py --json            # machine-readable
python3 apps/perdix-kilavuz/perdix_kilavuz.py --fix             # hardening plan, changes nothing
sudo python3 apps/perdix-kilavuz/perdix_kilavuz.py --apply      # apply the automatic fixes
```

Run it as root for a complete picture. As a normal user, some sockets can't be matched to their
program and some folders can't be read; the report says so. The exit code is 1 when a
high-severity (`yüksek`) finding exists, so scripts can react. It needs Python 3.9+ and no other
packages; `ss` (iproute2) is used when present to name more socket owners.

## What it checks

| Area | Finding | Severity |
|---|---|---|
| Ports | Network-reachable risky service (Telnet, FTP, SMB, RDP, VNC, databases, ...) | yüksek |
| Ports | Unknown network-reachable TCP port below 1024 | orta |
| Ports | Other network-reachable port | düşük |
| Ports | Expected service reachable (SSH, mDNS, Steam Remote Play) | bilgi |
| Files | World-writable file, or world-writable folder without the sticky bit | yüksek |
| Files | `/etc/shadow`, `/etc/sudoers`, SSH host keys, `/etc/perdix/*.conf`, `perdix-tune` too open or not owned by root | yüksek |
| Files | SUID/SGID program that a normal Arch system doesn't ship | orta |

Sockets bound only to loopback (`127.0.0.1`, `::1`) are not reported. Permissions are scanned
under `/etc /usr/local /opt /var/log /home` by default (`--paths` changes this). Symlinks are
never followed, and `/proc`, `/sys`, `/dev`, `/run` and `/tmp` are not entered from outside.

## Hardening

`--fix` lists, after the report, which findings can be fixed automatically and which need a
person. `--apply` carries out the automatic ones:

| Finding | Automatic fix |
|---|---|
| World-writable file or folder (no sticky bit) | remove write permission for others (`o-w`) |
| Sensitive file too open | remove only the forbidden bits (e.g. `/etc/shadow` 0666 → 0640) |
| Sensitive file not owned by root | give it to root, keeping its group |
| Unexpected SUID/SGID program | none: listed for you to check (removing it can break software) |
| Network-reachable service | none: listed for you to check (stopping it can cut off SSH or Steam) |

Every fix is made on the exact file that was scanned: the file is opened without following
symlinks and its device/inode is compared with the scan, so a file swapped for a symlink or
another file in the meantime is skipped (`atlandı`). Each change is appended to
`/var/log/perdix/kilavuz.log` as a JSON line with the old and new mode or owner (`--log` changes
the path), so it can be traced and undone. After `--apply`, the exit code is 1 only if a
high-severity finding is still open.

## In PERDIX Control Center

Module **06 Kılavuz** (`shell/kiosk_shell/perdix_hub.py`, all text in Turkish) scans on first
visit and on **Taramayı Başlat**. It shows the findings by severity, with the full finding and its
fix for the selected row. Scanning runs as the logged-in user, because it only reads.

**Açıkları Kapat** asks for confirmation, then runs `pkexec perdix-tune harden`. The root helper
re-scans the default folders itself and applies only the automatic fixes above. The GUI never
passes file paths to root. The page then shows how many fixes were applied, skipped or failed,
and scans again so the table shows what is left. On the ISO the module is installed root-owned
at `/usr/local/lib/perdix/perdix_kilavuz.py`; in a checkout the Control Center uses this copy.

## Use from Python

```python
import perdix_kilavuz as pk

for sock in pk.list_listening_sockets():
    print(sock.label(), sock.process, "exposed" if sock.exposed else "loopback")
findings = pk.audit_ports() + pk.audit_permissions(["/etc"])
print(pk.format_report(pk.AuditReport(findings)))
plan = pk.remediate(findings)                 # what --fix shows; apply=True applies it
print(pk.format_remediation(plan, applied=False))
```

## Tests

```sh
pytest apps/perdix-kilavuz/tests
```

The tests use synthetic `/proc/net` tables, a fake `/proc` and a temporary file tree, so they
don't depend on the machine they run on; hardening is only ever applied inside that tree.
