from kiosk_shell.services.antivirus import VirusScanner


def test_parses_clean_infected_and_noise_lines():
    scanner = VirusScanner("/home/kiosk")
    found = []
    scanner.threat_found.connect(lambda path, sig: found.append((path, sig)))
    for line in [
        "/home/kiosk/a.txt: OK",
        "/home/kiosk/odd: name.exe: Win.Test.EICAR_HDB-1 FOUND",
        "/home/kiosk/empty: Empty file",
        "",
    ]:
        scanner.handle_line(line)
    assert scanner._result.files_scanned == 3
    assert found == [("/home/kiosk/odd: name.exe", "Win.Test.EICAR_HDB-1")]


def test_missing_clamscan_reports_an_error(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    scanner = VirusScanner("/home/kiosk")
    results = []
    scanner.finished.connect(results.append)
    scanner.start()
    assert "not installed" in results[0].error
