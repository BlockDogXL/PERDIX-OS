# PERDIX Sentinel (PERDIX Kılavuz): security audit

`perdix_kilavuz.py` checks this machine for **open ports** and **weak file permissions**, then
prints a Turkish report with a suggested fix for each finding. It is **read-only**: it reads the
kernel's socket tables (`/proc/net/{tcp,tcp6,udp,udp6}`) and file metadata, never changes
anything, and never contacts other hosts. No interface is attached yet; it is a plain script and
a set of functions you can import.

## Run

```sh
sudo python3 apps/perdix-kilavuz/perdix_kilavuz.py              # full audit, Turkish report
python3 apps/perdix-kilavuz/perdix_kilavuz.py --ports-only      # only listening sockets
python3 apps/perdix-kilavuz/perdix_kilavuz.py --perms-only --paths /etc /opt
python3 apps/perdix-kilavuz/perdix_kilavuz.py --json            # machine-readable
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

## Use from Python

```python
import perdix_kilavuz as pk

for sock in pk.list_listening_sockets():
    print(sock.label(), sock.process, "exposed" if sock.exposed else "loopback")
findings = pk.audit_ports() + pk.audit_permissions(["/etc"])
print(pk.format_report(pk.AuditReport(findings)))
```

## Tests

```sh
pytest apps/perdix-kilavuz/tests
```

The tests use synthetic `/proc/net` tables, a fake `/proc` and a temporary file tree, so they
don't depend on the machine they run on.
