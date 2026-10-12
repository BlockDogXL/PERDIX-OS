"""PERDIX Kılavuz in the Control Center: the service, `perdix-tune harden` and the 06 page.

Hardening only ever runs on a temporary tree; the real system is never scanned for fixes."""

import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys

import pytest
from PyQt5.QtWidgets import QApplication

from kiosk_shell.services import kilavuz

sys.dont_write_bytecode = True
HELPER = os.path.join(os.path.dirname(__file__), "..", "..", "rootfs", "usr", "local", "bin", "perdix-tune")
_loader = importlib.machinery.SourceFileLoader("perdix_tune_kilavuz", HELPER)
tune = importlib.util.module_from_spec(importlib.util.spec_from_loader("perdix_tune_kilavuz", _loader))
_loader.exec_module(tune)

app = QApplication.instance() or QApplication([])
pk = kilavuz.load()  # the development copy in apps/perdix-kilavuz


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A world-writable file, an open folder and a SUID program; sensitive files are not checked."""
    root = tmp_path / "scan"
    (root / "bin").mkdir(parents=True)
    for name, mode in (("ok.conf", 0o644), ("world.conf", 0o666), ("bin/tool", 0o4755)):
        (root / name).write_text("x")
        os.chmod(root / name, mode)
    (root / "open").mkdir()
    os.chmod(root / "open", 0o777)
    monkeypatch.setattr(pk, "audit_sensitive_files", lambda *a, **k: [])
    return root


def report_for(root):
    return pk.AuditReport(findings=pk.audit_permissions([str(root)]), as_root=False)


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


# --- service --------------------------------------------------------------------------------------


def test_module_found_and_scan_uses_it(monkeypatch, tree):
    assert kilavuz.module_path() in (kilavuz.INSTALLED, kilavuz.DEVELOPMENT)
    monkeypatch.setattr(pk, "run_audit", lambda: report_for(tree))
    assert {os.path.basename(f.target) for f in kilavuz.scan().findings} == {"world.conf", "open", "tool"}


@pytest.mark.parametrize("returncode, stdout, stderr, expected", [
    (0, '[{"target": "/x", "status": "uygulandı"}]', "", (True, [{"target": "/x", "status": "uygulandı"}])),
    (126, "", "", (False, "İzin verilmedi: polkit perdix-tune'u reddetti.")),
    (1, "", "perdix-tune: PERDIX Kılavuz is not installed", (False, "PERDIX Kılavuz is not installed")),
    (0, "not json", "", (False, "perdix-tune'dan anlaşılamayan bir yanıt geldi.")),
    (0, '{"a": 1}', "", (False, "perdix-tune'dan anlaşılamayan bir yanıt geldi.")),
])
def test_close_vulnerabilities(monkeypatch, tmp_path, returncode, stdout, stderr, expected):
    helper = tmp_path / "perdix-tune"
    helper.write_text("")
    monkeypatch.setattr(kilavuz.tuning, "TUNE", str(helper))
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
    monkeypatch.setattr(kilavuz.subprocess, "run", run)
    assert kilavuz.close_vulnerabilities() == expected
    assert calls == [["pkexec", str(helper), "harden"]]  # no paths ever go to root


def test_close_vulnerabilities_without_helper(monkeypatch, tmp_path):
    monkeypatch.setattr(kilavuz.tuning, "TUNE", str(tmp_path / "missing"))
    assert kilavuz.close_vulnerabilities() == (False, f"{tmp_path / 'missing'} bulunamadı.")


def test_summarize():
    results = [{"status": s} for s in ("uygulandı", "uygulandı", "zaten düzgün", "atlandı", "hata", "elle")]
    assert kilavuz.summarize(results) == (
        "2 düzeltme uygulandı · 1 zaten düzgündü · 1 dosya taramadan sonra değiştiği için atlandı · "
        "1 hata · elle yapılması gereken 1 bulgu tabloda.")
    assert kilavuz.summarize([]) == "0 düzeltme uygulandı · 0 zaten düzgündü."


# --- perdix-tune harden ---------------------------------------------------------------------------


def test_harden_applies_fixes_and_logs(tree, tmp_path, monkeypatch):
    monkeypatch.setattr(tune, "LOG_DIR", str(tmp_path / "log"))
    results = {os.path.basename(r["target"]): r for r in tune.harden(pk, roots=[str(tree)])}
    assert results["world.conf"]["status"] == "uygulandı" and results["world.conf"]["detail"] == "izinler 0666 → 0664"
    assert results["open"]["status"] == "uygulandı"
    assert results["tool"]["status"] == "elle" and results["tool"]["severity"] == "orta"
    assert mode(tree / "world.conf") == 0o664 and mode(tree / "open") == 0o775
    assert mode(tree / "bin" / "tool") == 0o4755  # SUID is never changed automatically
    log = [json.loads(line) for line in (tmp_path / "log" / "kilavuz.log").read_text().splitlines()]
    assert len(log) == 2


def test_harden_command_line(tree, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tune, "LOG_DIR", str(tmp_path / "log"))
    monkeypatch.setattr(tune, "load_kilavuz", lambda: pk)
    monkeypatch.setattr(pk, "DEFAULT_SCAN_ROOTS", (str(tree),))
    assert tune.main(["harden"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert {r["status"] for r in output} == {"uygulandı", "elle"}
    # The caller can't choose what root hardens.
    assert tune.main(["harden", "/home"]) == 2


def test_harden_reports_missing_module(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tune, "KILAVUZ", str(tmp_path / "missing.py"))
    assert tune.main(["harden"]) == 1
    assert "PERDIX Kılavuz is not installed" in capsys.readouterr().err


# --- the 06 Kılavuz page --------------------------------------------------------------------------


def settle(page):
    """Let the page's worker threads finish and deliver their signals."""
    for _ in range(5):
        if page._worker is not None:
            page._worker.wait(10000)
        app.processEvents()


@pytest.fixture
def page():
    from kiosk_shell.perdix_hub import SecurityPage
    return SecurityPage()


def test_turkish_upper():
    from kiosk_shell.perdix_hub import turkish_upper
    assert turkish_upper("Bilgi") == "BİLGİ"
    assert turkish_upper("Kılavuz") == "KILAVUZ"
    assert turkish_upper("Açıkları Kapat ne yapar?") == "AÇIKLARI KAPAT NE YAPAR?"


def test_page_shows_findings_in_turkish(page, tree):
    page.show_report(report_for(tree))
    assert page.counts["yüksek"].value.text() == "2" and page.counts["orta"].value.text() == "1"
    assert page.table.rowCount() == 3
    rows = [[page.table.item(r, c).text() for c in range(5)] for r in range(3)]
    assert rows[0][0] == "YÜKSEK" and rows[-1][0] == "ORTA"
    assert {r[1] for r in rows} == {"Dosya izni"}
    assert {r[4] for r in rows} == {"Otomatik", "Elle"}
    assert page.close_button.isEnabled()
    assert "2 tanesi \"Açıkları Kapat\" ile düzeltilebilir" in page.status.text()
    assert "kullanıcı yetkisiyle" in page.status.text()
    headers = [page.table.horizontalHeaderItem(c).text() for c in range(5)]
    assert headers == ["ÖNEM", "TÜR", "HEDEF", "BULGU", "DÜZELTME"]
    # The first finding is selected and shown in full below the table.
    assert page.details.text().splitlines()[0] == page.findings[0].message
    assert "Ne yapılmalı (otomatik düzeltilebilir): chmod o-w" in page.details.text()
    page.table.selectRow(2)
    assert "Ne yapılmalı (elle yapılmalı)" in page.details.text()


def test_page_without_findings(page, tmp_path):
    page.show_report(pk.AuditReport(as_root=True))
    assert page.status.text() == "Sorun bulunmadı."
    assert not page.close_button.isEnabled() and page.table.rowCount() == 0


def test_close_button_asks_first(page, tree, monkeypatch):
    page.show_report(report_for(tree))
    monkeypatch.setattr(page, "confirm_close", lambda count: False)
    monkeypatch.setattr(kilavuz, "close_vulnerabilities", lambda: pytest.fail("ran without consent"))
    page.close_clicked()
    assert page._worker is None


def test_close_button_runs_harden_then_rescans(page, tree, tmp_path, monkeypatch):
    monkeypatch.setattr(tune, "LOG_DIR", str(tmp_path / "log"))
    page.show_report(report_for(tree))
    asked = []
    monkeypatch.setattr(page, "confirm_close", lambda count: asked.append(count) or True)
    # Stand-in for pkexec perdix-tune harden: the helper's harden() on the temporary tree.
    monkeypatch.setattr(kilavuz, "close_vulnerabilities", lambda: (True, tune.harden(pk, roots=[str(tree)])))
    monkeypatch.setattr(kilavuz, "scan", lambda: report_for(tree))
    page.close_clicked()
    settle(page)
    assert asked == [2]
    assert mode(tree / "world.conf") == 0o664
    assert page.table.rowCount() == 1 and page.table.item(0, 4).text() == "Elle"  # the SUID program
    assert page.status.text().startswith("2 düzeltme uygulandı · 0 zaten düzgündü · elle yapılması gereken 1")
    assert page.status.objectName() == "StatusOk"
    assert not page.close_button.isEnabled() and page.scan_button.isEnabled()


def test_close_failure_is_shown(page, tree, monkeypatch):
    page.show_report(report_for(tree))
    monkeypatch.setattr(page, "confirm_close", lambda count: True)
    monkeypatch.setattr(kilavuz, "close_vulnerabilities",
                        lambda: (False, "İzin verilmedi: polkit perdix-tune'u reddetti."))
    page.close_clicked()
    settle(page)
    assert page.status.text() == "Açıklar kapatılamadı: İzin verilmedi: polkit perdix-tune'u reddetti."
    assert page.status.objectName() == "StatusError"
    assert page.close_button.isEnabled()  # nothing changed, so it can be tried again


def test_scan_runs_on_first_visit(page, tree, monkeypatch):
    calls = []
    monkeypatch.setattr(kilavuz, "scan", lambda: calls.append(1) or report_for(tree))
    page.show()
    settle(page)
    page.hide()
    page.show()
    settle(page)
    assert calls == [1] and page.table.rowCount() == 3


def test_scan_error_is_shown(page, monkeypatch):
    def fail():
        raise FileNotFoundError("PERDIX Kılavuz bulunamadı (/usr/local/lib/perdix/perdix_kilavuz.py).")
    monkeypatch.setattr(kilavuz, "scan", fail)
    page.start_scan()
    settle(page)
    assert page.status.text().startswith("Tarama başarısız: PERDIX Kılavuz bulunamadı")


def test_hub_has_the_kilavuz_module():
    from kiosk_shell.perdix_hub import HubWindow
    assert HubWindow.MODULES[-1] == ("06", "shield", "Kılavuz", "Güvenlik · port · izin")
