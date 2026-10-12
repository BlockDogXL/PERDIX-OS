"""Tests for PERDIX Studio. Run: QT_QPA_PLATFORM=offscreen pytest apps/perdix-studio/tests

The real-decompiler test runs when PERDIX_CFR_JAR points at a CFR jar and java/javac exist.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl  # noqa: E402
from PyQt5.QtGui import QDropEvent  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

import perdix_studio as ps  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    qs = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    s = ps.StudioSettings(qs)
    s.workdir = str(tmp_path / "work")
    return s


@pytest.fixture(autouse=True)
def no_dialogs(monkeypatch):
    """Record message boxes instead of blocking on them."""
    shown = []
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, _n=name, **k: shown.append((_n, a[2])))
    # "Save changes?" on close: answer Discard instead of waiting for a click.
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: shown.append(("question", a[2])) or QMessageBox.Discard)
    return shown


# ---- languages and files ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,language",
    [("a.c", "C"), ("a.hpp", "C++"), ("A.CS", "C#"), ("x.py", "Python"), ("app.mjs", "JavaScript"),
     ("Main.java", "Java"), ("notes.txt", "Plain Text"), (None, "Plain Text")],
)
def test_language_for_path(name, language):
    assert ps.language_for_path(name).name == language


def test_text_files_round_trip_encodings(tmp_path):
    utf8, bom, latin = tmp_path / "u.py", tmp_path / "b.cs", tmp_path / "l.c"
    utf8.write_bytes("şğü = 1\n".encode())
    bom.write_bytes(b"\xef\xbb\xbfclass A {}\n")
    latin.write_bytes("caf\xe9\n".encode("cp1252"))
    assert ps.read_text_file(utf8) == ("şğü = 1\n", "UTF-8")
    assert ps.read_text_file(bom) == ("class A {}\n", "UTF-8 BOM")
    assert ps.read_text_file(latin) == ("café\n", "Windows-1252")
    ps.write_text_file(bom, "class B {}\n", "UTF-8 BOM")
    assert bom.read_bytes() == b"\xef\xbb\xbfclass B {}\n"
    binary = tmp_path / "x.bin"
    binary.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ps.BinaryFileError):
        ps.read_text_file(binary)


# ---- archive detection and commands --------------------------------------------------------------


def make_quake_pak(path: Path, files: dict[str, bytes]) -> None:
    data, directory = b"", b""
    offset = 12
    for name, content in files.items():
        directory += struct.pack("<56sii", name.encode(), offset + len(data), len(content))
        data += content
    path.write_bytes(b"PACK" + struct.pack("<ii", 12 + len(data), len(directory)) + data + directory)


def test_detect_archive_by_magic(tmp_path):
    jar = tmp_path / "mod.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("a/B.class", b"\xca\xfe\xba\xbe")
    pak = tmp_path / "pak0.pak"
    make_quake_pak(pak, {"maps/e1m1.bsp": b"x"})
    unreal = tmp_path / "Game-Windows.pak"
    unreal.write_bytes(b"\x00" * 500 + struct.pack("<I", 0x5A6F12E1) + b"\x00" * 40)
    other = tmp_path / "data.pak"
    other.write_bytes(b"\x01\x02\x03\x04" * 10)
    bsa = tmp_path / "Skyrim - Textures.bsa"
    bsa.write_bytes(b"BSA\x00" + b"\x00" * 32)
    assert ps.detect_archive(jar) is ps.ArchiveKind.JAR
    assert ps.detect_archive(pak) is ps.ArchiveKind.PAK_QUAKE
    assert ps.detect_archive(unreal) is ps.ArchiveKind.PAK_UNREAL
    assert ps.detect_archive(other) is ps.ArchiveKind.PAK_OTHER
    assert ps.detect_archive(bsa) is ps.ArchiveKind.BSA
    fake_jar = tmp_path / "fake.jar"
    fake_jar.write_bytes(b"not a zip")
    with pytest.raises(ps.ModdingError, match="not a valid .jar"):
        ps.detect_archive(fake_jar)


def test_validate_archive_path(tmp_path):
    with pytest.raises(ps.ModdingError, match="not found"):
        ps.validate_archive_path(tmp_path / "missing.jar")
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(ps.ModdingError, match="Unsupported"):
        ps.validate_archive_path(txt)
    empty = tmp_path / "e.bsa"
    empty.touch()
    with pytest.raises(ps.ModdingError, match="empty"):
        ps.validate_archive_path(empty)


def test_build_command_never_uses_a_shell(tmp_path):
    hostile = str(tmp_path / "my mod; rm -rf ~ $(reboot).pak")
    command = ps.build_command(sys.executable, "unpack {input} --output {output}",
                               {"input": hostile, "output": str(tmp_path / "out dir")})
    assert command == [sys.executable, "unpack", hostile, "--output", str(tmp_path / "out dir")]
    with pytest.raises(ps.ModdingError, match="not found on PATH"):
        ps.build_command("definitely-not-a-tool-xyz", "{input}", {"input": "a"})
    with pytest.raises(ps.ModdingError, match="must contain"):
        ps.build_command(sys.executable, "--help", {"input": "a"})


def test_plan_job_reports_missing_decompiler(tmp_path, settings):
    jar = tmp_path / "mod.jar"
    with pytest.raises(ps.ModdingError, match="Set the path of the CFR"):
        ps.plan_job(jar, ps.ArchiveKind.JAR, tmp_path, settings)
    settings.set_decompiler_jar("CFR", str(tmp_path / "nope.jar"))
    with pytest.raises(ps.ModdingError, match="jar not found"):
        ps.plan_job(jar, ps.ArchiveKind.JAR, tmp_path, settings)
    assert ps.plan_job(jar, ps.ArchiveKind.PAK_QUAKE, tmp_path, settings) is None


def test_safe_join_blocks_zip_slip(tmp_path):
    assert ps.safe_join(tmp_path, "a/b.txt") == tmp_path / "a" / "b.txt"
    assert ps.safe_join(tmp_path, "/etc/passwd") == tmp_path / "etc" / "passwd"
    for bad in ("../evil", "a/../../evil", "..\\..\\evil"):
        with pytest.raises(ps.ModdingError):
            ps.safe_join(tmp_path, bad)


def test_builtin_pak_extractor(tmp_path):
    pak = tmp_path / "pak0.pak"
    make_quake_pak(pak, {"maps/e1m1.bsp": b"BSP", "progs.dat": b"QC" * 10})
    seen = []
    assert ps.extract_quake_pak(pak, tmp_path / "out", lambda v, m: seen.append((v, m)), lambda: False) == 2
    assert (tmp_path / "out" / "maps" / "e1m1.bsp").read_bytes() == b"BSP"
    assert seen == [(1, 2), (2, 2)]
    evil = tmp_path / "evil.pak"
    make_quake_pak(evil, {"../../escape.txt": b"x"})
    with pytest.raises(ps.ModdingError, match="unsafe path"):
        ps.extract_quake_pak(evil, tmp_path / "out2", lambda v, m: None, lambda: False)


# ---- the background worker, with a fake external tool ---------------------------------------------

FAKE_TOOL = textwrap.dedent("""\
    import os, sys, time
    mode, archive, out = sys.argv[1], sys.argv[2], sys.argv[3]
    if mode == "ok":
        for i in range(3):
            os.makedirs(os.path.join(out, "data"), exist_ok=True)
            open(os.path.join(out, "data", f"file{i}.txt"), "w").write("hello")
            print(f"extracted file{i}.txt", flush=True)
    elif mode == "fail":
        print("error: unsupported archive version", flush=True)
        sys.exit(3)
    elif mode == "slow":
        print("working...", flush=True)
        time.sleep(30)
""")


def run_worker(app, worker: ps.ExtractWorker, cancel_after: float | None = None) -> dict:
    result: dict = {"log": []}
    worker.log.connect(result["log"].append)
    worker.succeeded.connect(lambda out, n: result.update(ok=(out, n)))
    worker.failed.connect(lambda msg: result.update(failed=msg))
    worker.start()
    if cancel_after is not None:
        QTest.qWait(int(cancel_after * 1000))
        worker.cancel()
    assert worker.wait(20000)
    app.processEvents()
    return result


@pytest.fixture
def fake_tool(tmp_path):
    script = tmp_path / "fake_tool.py"
    script.write_text(FAKE_TOOL)
    archive = tmp_path / "Data.bsa"
    archive.write_bytes(b"BSA\x00" + b"\x00" * 32)
    return script, archive


@pytest.mark.parametrize("mode", ["ok", "fail"])
def test_worker_runs_tool_and_reports(app, tmp_path, fake_tool, mode):
    script, archive = fake_tool
    out = tmp_path / "out"
    command = ps.build_command(sys.executable, f"{script} {mode} {{input}} {{output}}",
                               {"input": str(archive), "output": str(out)})
    result = run_worker(app, ps.ExtractWorker(archive, ps.ArchiveKind.BSA, out, command, 60))
    if mode == "ok":
        assert result["ok"] == (str(out), 3)
        assert "extracted file2.txt" in result["log"]
    else:
        assert "failed (exit code 3)" in result["failed"]
        assert "unsupported archive version" in result["failed"]


def test_worker_cancel_and_timeout(app, tmp_path, fake_tool):
    script, archive = fake_tool
    command = ps.build_command(sys.executable, f"{script} slow {{input}} {{output}}",
                               {"input": str(archive), "output": str(tmp_path / "o1")})
    result = run_worker(app, ps.ExtractWorker(archive, ps.ArchiveKind.BSA, tmp_path / "o1", command, 60),
                        cancel_after=1.0)
    assert result["failed"] == "Cancelled."
    command = ps.build_command(sys.executable, f"{script} slow {{input}} {{output}}",
                               {"input": str(archive), "output": str(tmp_path / "o2")})
    worker = ps.ExtractWorker(archive, ps.ArchiveKind.BSA, tmp_path / "o2", command, 60)
    worker.timeout = 1
    assert "did not finish within 1 s" in run_worker(app, worker)["failed"]


def test_fernflower_source_jar_is_expanded(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with zipfile.ZipFile(out / "mod.jar", "w") as z:
        z.writestr("com/example/Mod.java", "class Mod {}")
    ps.expand_source_jars(out, lambda line: None, lambda: False)
    assert (out / "com" / "example" / "Mod.java").read_text() == "class Mod {}"
    assert not (out / "mod.jar").exists()


@pytest.mark.skipif(not os.environ.get("PERDIX_CFR_JAR") or not shutil.which("javac"),
                    reason="set PERDIX_CFR_JAR to a CFR jar (and install a JDK) to run")
def test_real_cfr_decompiles_a_jar(app, tmp_path, settings):
    src = tmp_path / "src" / "com" / "perdix"
    src.mkdir(parents=True)
    (src / "Hello.java").write_text("package com.perdix;\npublic class Hello {\n"
                                    "  public static int answer() { return 42; }\n}\n")
    subprocess.run(["javac", "-d", str(tmp_path / "classes"), str(src / "Hello.java")], check=True)
    jar = tmp_path / "hello mod.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.write(tmp_path / "classes" / "com" / "perdix" / "Hello.class", "com/perdix/Hello.class")
    settings.set_decompiler_jar("CFR", os.environ["PERDIX_CFR_JAR"])
    out = tmp_path / "out"
    command = ps.plan_job(jar, ps.detect_archive(jar), out, settings)
    result = run_worker(app, ps.ExtractWorker(jar, ps.ArchiveKind.JAR, out, command, 120))
    assert "ok" in result, result
    decompiled = (out / "com" / "perdix" / "Hello.java").read_text()
    assert "public static int answer()" in decompiled and "return 42;" in decompiled


# ---- UI -----------------------------------------------------------------------------------------


def test_editor_open_status_and_smart_indent(app, tmp_path, settings):
    window = ps.MainWindow(settings)
    window.show()  # QScintilla only runs its UI update (cursor reports) for a visible widget
    source = tmp_path / "game.py"
    source.write_text("x = 1\n")
    assert window.editor.open_file(source)
    editor = window.editor.current_editor()
    assert editor.language.name == "Python" and editor.lexer() is not None
    assert window.language_label.text() == "Python" and window.encoding_label.text() == "UTF-8"

    editor.setText("")
    editor.setFocus()
    QTest.keyClicks(editor, "def f():")
    QTest.keyClick(editor, Qt.Key_Return)
    assert editor.getCursorPosition() == (1, 4)                       # indented after ':'
    QTest.keyClicks(editor, "return 1")
    QTest.keyClick(editor, Qt.Key_Return)
    assert editor.getCursorPosition() == (2, 0)                       # dedented after return

    c_file = tmp_path / "mod.cpp"
    c_file.write_text("")
    window.editor.open_file(c_file)
    cpp = window.editor.current_editor()
    cpp.setFocus()
    QTest.keyClicks(cpp, "int main() {")
    QTest.keyClick(cpp, Qt.Key_Return)
    assert cpp.indentation(1) == 4
    QTest.keyClicks(cpp, "}")
    assert cpp.indentation(1) == 0 and cpp.text(1).strip() == "}"     # '}' snaps back
    QTest.qWait(50)
    assert window.position_label.text() == "Ln 2, Col 2"

    cpp.setModified(False)
    window.editor.find_bar.field.setText("main")
    assert window.editor.find_bar.find_next(from_start=True)
    window.close()


def test_save_keeps_encoding_and_marks_tab(app, tmp_path, settings):
    window = ps.MainWindow(settings)
    path = tmp_path / "legacy.c"
    path.write_bytes("/* caf\xe9 */\n".encode("cp1252"))
    window.editor.open_file(path)
    editor = window.editor.current_editor()
    editor.append("int x;\n")
    assert window.editor.tabs.tabText(window.editor.tabs.currentIndex()).startswith("●")
    assert window.editor.save()
    assert path.read_bytes() == "/* caf\xe9 */\nint x;\n".encode("cp1252")
    assert not window.editor.tabs.tabText(window.editor.tabs.currentIndex()).startswith("●")
    window.close()


def test_drop_starts_extraction_and_tree_opens_files(app, tmp_path, settings, no_dialogs):
    window = ps.MainWindow(settings)
    panel = window.modding
    pak = tmp_path / "pak0.pak"
    make_quake_pak(pak, {"scripts/weapons.cfg": b"damage 10\n"})
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(pak))])
    event = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    panel.drop_zone.dropEvent(event)
    assert panel.worker.wait(10000)
    app.processEvents()
    assert panel.tree.isEnabled()
    extracted = next(Path(settings.workdir).rglob("weapons.cfg"))
    panel.open_in_editor.emit(str(extracted))
    assert window.main_tabs.currentWidget() is window.editor
    assert window.editor.current_editor().text() == "damage 10\n"

    missing_tool = tmp_path / "Data.bsa"
    missing_tool.write_bytes(b"BSA\x00" + b"\x00" * 32)
    settings.bsa_program = "no-such-bsatool"
    panel.start(str(missing_tool))
    assert no_dialogs and "not found on PATH" in no_dialogs[-1][1]
    assert "ERROR" in panel.log.toPlainText()
    window.close()


def test_settings_persist(tmp_path):
    path = str(tmp_path / "s.ini")
    first = ps.StudioSettings(QSettings(path, QSettings.IniFormat))
    first.theme, first.decompiler, first.timeout = "light", "Procyon", 120
    first.set_decompiler_jar("Procyon", "/opt/procyon.jar")
    first.sync()
    second = ps.StudioSettings(QSettings(path, QSettings.IniFormat))
    assert (second.theme, second.decompiler, second.timeout) == ("light", "Procyon", 120)
    assert second.decompiler_jar("Procyon") == "/opt/procyon.jar"
    second.qsettings.setValue("ui/theme", "neon-pink")                # invalid -> default
    assert second.theme == "dark"
