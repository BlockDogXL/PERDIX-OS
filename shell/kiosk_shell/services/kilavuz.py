"""PERDIX Kılavuz (PERDIX Sentinel) for the Control Center.

Scanning runs as the logged-in user: it only reads socket tables and file metadata. Closing
vulnerabilities goes through `pkexec perdix-tune harden`, which re-scans as root and applies only
PERDIX Kılavuz's automatic permission fixes; the GUI never tells root which files to change.
Messages are Turkish, like the Kılavuz page.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from kiosk_shell.services import tuning

INSTALLED = "/usr/local/lib/perdix/perdix_kilavuz.py"  # on the ISO (see build_iso.sh)
# In a checkout: apps/perdix-kilavuz/perdix_kilavuz.py next to shell/.
DEVELOPMENT = str(Path(__file__).resolve().parents[3] / "apps" / "perdix-kilavuz" / "perdix_kilavuz.py")
HARDEN_TIMEOUT = 600  # a full re-scan of /home can take a while on a big Steam library

_module: ModuleType | None = None


def module_path() -> str | None:
    for path in (INSTALLED, DEVELOPMENT):
        if os.path.isfile(path):
            return path
    return None


def load() -> ModuleType:
    """The perdix_kilavuz module (loaded once). Raises FileNotFoundError when it isn't installed."""
    global _module
    if _module is None:
        path = module_path()
        if path is None:
            raise FileNotFoundError(f"PERDIX Kılavuz bulunamadı ({INSTALLED}).")
        spec = importlib.util.spec_from_file_location("perdix_kilavuz", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["perdix_kilavuz"] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        _module = module
    return _module


def scan():
    """Open ports and file permissions, as the current user. Returns perdix_kilavuz.AuditReport."""
    return load().run_audit()


def close_vulnerabilities(timeout: int = HARDEN_TIMEOUT) -> tuple[bool, list[dict] | str]:
    """Run `pkexec perdix-tune harden`. Returns (True, results) or (False, Turkish error).

    Each result: {"target", "severity", "message", "status", "detail"}."""
    if not os.path.exists(tuning.TUNE):
        return False, f"{tuning.TUNE} bulunamadı."
    try:
        result = subprocess.run(
            ["pkexec", tuning.TUNE, "harden"], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return False, "pkexec kurulu değil ('polkit' paketi)."
    except subprocess.TimeoutExpired:
        return False, "perdix-tune zamanında yanıt vermedi."
    if result.returncode in (126, 127):
        return False, "İzin verilmedi: polkit perdix-tune'u reddetti."
    if result.returncode != 0:
        error = result.stderr.strip().removeprefix("perdix-tune: ")
        return False, error or f"perdix-tune başarısız oldu (kod {result.returncode})."
    try:
        results = json.loads(result.stdout)
    except ValueError:
        return False, "perdix-tune'dan anlaşılamayan bir yanıt geldi."
    if not isinstance(results, list):
        return False, "perdix-tune'dan anlaşılamayan bir yanıt geldi."
    return True, results


def summarize(results: list[dict]) -> str:
    """One Turkish line for the outcome of close_vulnerabilities()."""
    count = {}
    for r in results:
        count[r.get("status")] = count.get(r.get("status"), 0) + 1
    parts = [
        f"{count.get('uygulandı', 0)} düzeltme uygulandı",
        f"{count.get('zaten düzgün', 0)} zaten düzgündü",
    ]
    if count.get("atlandı"):
        parts.append(f"{count['atlandı']} dosya taramadan sonra değiştiği için atlandı")
    if count.get("hata"):
        parts.append(f"{count['hata']} hata")
    manual = count.get("elle", 0)
    if manual:
        parts.append(f"elle yapılması gereken {manual} bulgu tabloda")
    return " · ".join(parts) + "."
