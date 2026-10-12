#!/usr/bin/env python3
# =============================================================================================
# PERDIX Studio: a code editor with a Modding Tools tab, for PERDIX OS.
#
# Dependencies
#   Python 3.10+
#   PyQt5 >= 5.15            pip install PyQt5            (Arch: python-pyqt5)
#   QScintilla >= 2.13       pip install QScintilla       (Arch: python-qscintilla-qt5)
#                            (the pip package provides the PyQt5.Qsci module)
#   Optional, for the Modding Tools tab (paths are set in Tools > Settings):
#     Java 11+ and one decompiler jar: CFR, Fernflower/Vineflower or Procyon   (.jar)
#     bsatool from OpenMW (Linux) or BSArch (Windows; on Linux through Wine)  (.bsa)
#     repak (Unreal Engine 4/5 .pak); Quake/Half-Life "PACK" .pak files need no tool
#
# Installation
#   python3 -m venv .venv && . .venv/bin/activate
#   pip install -r requirements.txt
#   python3 perdix_studio.py [files to open...]
#
# Editor component: QScintilla, not Monaco-in-QWebEngineView. QScintilla is a native widget
# with lexers for every requested language, it adds a few MB instead of a ~150 MB Chromium
# runtime, starts instantly, and needs no JavaScript bridge for open/save/status updates.
#
# Assumptions (where the brief left room)
#   * ".pak" is not one format. PERDIX Studio reads the archive's magic bytes: Quake/Half-Life
#     "PACK" archives are unpacked by a built-in Python extractor; Unreal Engine 4/5 paks
#     (magic 0x5A6F12E1 in the footer) and anything else go to the configured .pak tool,
#     repak by default. Encrypted/Oodle-compressed Unreal paks need a tool that supports them.
#   * ".bsa" covers Morrowind, Oblivion, Fallout 3/NV and Skyrim archives (magic checked).
#     Fallout 4/Starfield ".ba2" files are out of scope. BSArch is Windows-only, so the default
#     on Linux is OpenMW's open-source bsatool; a "BSArch via Wine" preset is included.
#   * Tool versions are not pinned. Each tool is a program plus an argument template with
#     {input}, {output} (and for decompilers {jar}) placeholders, editable in Settings, so a
#     different version or CLI syntax only needs a settings change. Defaults were written for
#     CFR 0.152, Fernflower/Vineflower 1.x, Procyon 0.6, OpenMW bsatool 0.48, repak 0.2.
#   * Fernflower writes a jar of sources; PERDIX Studio extracts it so the tree shows files.
#   * "Basic auto-completion" = language keywords plus words already in the document.
#   * Decompiled .java files open with the Java lexer (Java was not in the language list, but
#     it is what the .jar workflow produces); other unknown files open as plain text.
#   * Files over 20 MB or containing NUL bytes are not opened in the editor (binary assets).
# =============================================================================================
"""PERDIX Studio: tabbed QScintilla code editor plus a Modding Tools tab that unpacks .jar,
.pak and .bsa archives with external open-source tools in a background thread."""

from __future__ import annotations

import enum
import os
import queue
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import QDir, QModelIndex, QSettings, Qt, QThread, pyqtSignal
from PyQt5.QtGui import (
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QKeySequence,
    QPalette,
)
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt5.Qsci import (
        QsciAPIs,
        QsciLexer,
        QsciLexerCPP,
        QsciLexerCSharp,
        QsciLexerJava,
        QsciLexerJavaScript,
        QsciLexerPython,
        QsciScintilla,
    )
except ImportError:  # pragma: no cover - only hit when the dependency is missing
    sys.stderr.write(
        "PERDIX Studio needs QScintilla: pip install QScintilla "
        "(Arch: pacman -S python-qscintilla-qt5)\n"
    )
    raise SystemExit(1)

APP_NAME = "PERDIX Studio"
ORG_NAME = "PERDIX"
MAX_EDITOR_BYTES = 20 * 1024 * 1024
ARCHIVE_EXTENSIONS = (".jar", ".pak", ".bsa")


# =============================================================================================
# Languages
# =============================================================================================

C_KEYWORDS = (
    "auto break case char const continue default do double else enum extern float for goto if "
    "inline int long register restrict return short signed sizeof static struct switch typedef "
    "union unsigned void volatile while _Bool _Complex _Static_assert NULL bool true false "
    "#include #define #ifdef #ifndef #endif #pragma printf malloc free memcpy strlen"
).split()
CPP_KEYWORDS = (
    C_KEYWORDS
    + (
        "alignas alignof and asm catch class concept consteval constexpr constinit const_cast "
        "co_await co_return co_yield decltype delete dynamic_cast explicit export friend mutable "
        "namespace new noexcept nullptr operator or override final private protected public "
        "reinterpret_cast requires static_assert static_cast template this throw try typeid "
        "typename using virtual std::string std::vector std::unique_ptr std::shared_ptr std::cout"
    ).split()
)
CSHARP_KEYWORDS = (
    "abstract as base bool break byte case catch char checked class const continue decimal "
    "default delegate do double else enum event explicit extern false finally fixed float for "
    "foreach goto if implicit in int interface internal is lock long namespace new null object "
    "operator out override params private protected public readonly ref return sbyte sealed "
    "short sizeof stackalloc static string struct switch this throw true try typeof uint ulong "
    "unchecked unsafe ushort using virtual void volatile while async await var dynamic get set "
    "init record yield nameof Console.WriteLine List Dictionary Task"
).split()
PYTHON_KEYWORDS = (
    "False None True and as assert async await break class continue def del elif else except "
    "finally for from global if import in is lambda nonlocal not or pass raise return try "
    "while with yield match case print len range enumerate isinstance self __init__ __name__"
).split()
JS_KEYWORDS = (
    "async await break case catch class const continue debugger default delete do else export "
    "extends false finally for function if import in instanceof let new null return static "
    "super switch this throw true try typeof undefined var void while with yield console.log "
    "document window Promise JSON.stringify JSON.parse addEventListener querySelector"
).split()
JAVA_KEYWORDS = (
    "abstract assert boolean break byte case catch char class const continue default do double "
    "else enum extends final finally float for goto if implements import instanceof int "
    "interface long native new null package private protected public return short static "
    "strictfp super switch synchronized this throw throws transient try void volatile while "
    "var record sealed permits String System.out.println"
).split()


@dataclass(frozen=True)
class Language:
    """An editor language: how to recognise it and how to highlight, indent and complete it."""

    name: str
    extensions: tuple[str, ...]
    lexer_class: type | None
    keywords: tuple[str, ...]
    indent_after: tuple[str, ...]  # a line ending in one of these opens a block
    closing_chars: str  # typing one of these at the start of a line dedents it


C_LIKE_INDENT = ("{", "(", "[")
LANGUAGES: tuple[Language, ...] = (
    Language("C", (".c", ".h"), QsciLexerCPP, tuple(C_KEYWORDS), C_LIKE_INDENT, "})]"),
    Language(
        "C++",
        (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".ipp"),
        QsciLexerCPP,
        tuple(CPP_KEYWORDS),
        C_LIKE_INDENT,
        "})]",
    ),
    Language("C#", (".cs", ".csx"), QsciLexerCSharp, tuple(CSHARP_KEYWORDS), C_LIKE_INDENT, "})]"),
    Language(
        "Python",
        (".py", ".pyw", ".pyi"),
        QsciLexerPython,
        tuple(PYTHON_KEYWORDS),
        (":", "(", "[", "{"),
        ")]}",
    ),
    Language(
        "JavaScript",
        (".js", ".mjs", ".cjs", ".jsx"),
        QsciLexerJavaScript,
        tuple(JS_KEYWORDS),
        C_LIKE_INDENT,
        "})]",
    ),
    Language("Java", (".java",), QsciLexerJava, tuple(JAVA_KEYWORDS), C_LIKE_INDENT, "})]"),
    Language("Plain Text", (), None, (), (), ""),
)
PLAIN_TEXT = LANGUAGES[-1]
LANGUAGE_BY_NAME = {lang.name: lang for lang in LANGUAGES}
PYTHON_DEDENT_AFTER = ("return", "pass", "break", "continue", "raise")


def language_for_path(path: str | os.PathLike | None) -> Language:
    """The language for a file name, by extension; plain text when unknown."""
    if not path:
        return PLAIN_TEXT
    suffix = Path(path).suffix.lower()
    for lang in LANGUAGES:
        if suffix in lang.extensions:
            return lang
    return PLAIN_TEXT


# =============================================================================================
# Themes
# =============================================================================================

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "paper": "#14161a",
        "text": "#d4d9e0",
        "margin_bg": "#101215",
        "margin_fg": "#5d6672",
        "caret_line": "#1b1e23",
        "selection": "#2b4a6b",
        "caret": "#ff6a1a",
        "keyword": "#ff8a4a",
        "keyword2": "#8fe3ff",
        "comment": "#6b7480",
        "string": "#a5d6a7",
        "number": "#f2c66d",
        "preprocessor": "#c792ea",
        "class": "#8fe3ff",
        "function": "#82aaff",
        "operator": "#d4d9e0",
        "decorator": "#c792ea",
        "brace_bg": "#3a3f47",
        "fold": "#191c20",
        "window": "#1b1e23",
        "base": "#14161a",
        "button": "#23272d",
        "highlight": "#ff6a1a",
    },
    "light": {
        "paper": "#ffffff",
        "text": "#1f2328",
        "margin_bg": "#f3f4f6",
        "margin_fg": "#8c959f",
        "caret_line": "#f6f8fa",
        "selection": "#b6d7ff",
        "caret": "#d04f00",
        "keyword": "#cf222e",
        "keyword2": "#0550ae",
        "comment": "#6e7781",
        "string": "#0a3069",
        "number": "#953800",
        "preprocessor": "#8250df",
        "class": "#0550ae",
        "function": "#8250df",
        "operator": "#1f2328",
        "decorator": "#8250df",
        "brace_bg": "#d0d7de",
        "fold": "#f3f4f6",
        "window": "#f3f4f6",
        "base": "#ffffff",
        "button": "#e8eaed",
        "highlight": "#d04f00",
    },
}

# Lexer style constants (by attribute name, so one table serves every lexer) -> theme role.
STYLE_ROLES: tuple[tuple[str, str], ...] = (
    ("Comment", "comment"),
    ("CommentLine", "comment"),
    ("CommentDoc", "comment"),
    ("CommentLineDoc", "comment"),
    ("CommentBlock", "comment"),
    ("CommentDocKeyword", "comment"),
    ("Number", "number"),
    ("Keyword", "keyword"),
    ("KeywordSet2", "keyword2"),
    ("GlobalClass", "class"),
    ("DoubleQuotedString", "string"),
    ("SingleQuotedString", "string"),
    ("TripleSingleQuotedString", "string"),
    ("TripleDoubleQuotedString", "string"),
    ("VerbatimString", "string"),
    ("RawString", "string"),
    ("UnclosedString", "string"),
    ("DoubleQuotedFString", "string"),
    ("SingleQuotedFString", "string"),
    ("TripleSingleQuotedFString", "string"),
    ("TripleDoubleQuotedFString", "string"),
    ("Regex", "string"),
    ("PreProcessor", "preprocessor"),
    ("Operator", "operator"),
    ("ClassName", "class"),
    ("FunctionMethodName", "function"),
    ("Decorator", "decorator"),
    ("HighlightedIdentifier", "keyword2"),
)


MONOSPACE_FAMILIES = (
    "JetBrains Mono",
    "Fira Code",
    "Source Code Pro",
    "Cascadia Code",
    "DejaVu Sans Mono",
    "Noto Sans Mono",
    "Liberation Mono",
    "Ubuntu Mono",
    "Consolas",
    "Menlo",
    "Courier New",
)


def monospace_font(size: int = 11) -> QFont:
    """A real fixed-width font: the first installed coding font, else any fixed-pitch one.

    QFontDatabase.systemFont(FixedFont) is not trusted on its own: on minimal systems it can
    return a proportional font."""
    database = QFontDatabase()
    installed = set(database.families())
    family = next((name for name in MONOSPACE_FAMILIES if name in installed), None)
    if family is None:
        family = next(
            (name for name in database.families() if database.isFixedPitch(name)), "Monospace"
        )
    font = QFont(family, size)
    font.setStyleHint(QFont.Monospace)
    font.setFixedPitch(True)
    return font


def style_lexer(lexer: QsciLexer, theme: str) -> None:
    """Colour a lexer for a theme: one paper/text colour everywhere, then per-role colours."""
    colors = THEMES[theme]
    font = monospace_font()
    lexer.setDefaultPaper(QColor(colors["paper"]))
    lexer.setDefaultColor(QColor(colors["text"]))
    lexer.setDefaultFont(font)
    lexer.setPaper(QColor(colors["paper"]), -1)
    lexer.setColor(QColor(colors["text"]), -1)
    lexer.setFont(font, -1)
    for attribute, role in STYLE_ROLES:
        style = getattr(type(lexer), attribute, None)
        if isinstance(style, int):
            lexer.setColor(QColor(colors[role]), style)
    if isinstance(lexer, QsciLexerPython):
        lexer.setIndentationWarning(QsciLexerPython.Inconsistent)


def apply_app_theme(app: QApplication, theme: str) -> None:
    """Fusion style with a palette and a small stylesheet for the chosen theme."""
    colors = THEMES[theme]
    app.setStyle("Fusion")
    palette = QPalette()
    roles = {
        QPalette.Window: colors["window"],
        QPalette.WindowText: colors["text"],
        QPalette.Base: colors["base"],
        QPalette.AlternateBase: colors["window"],
        QPalette.Text: colors["text"],
        QPalette.Button: colors["button"],
        QPalette.ButtonText: colors["text"],
        QPalette.Highlight: colors["highlight"],
        QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: colors["button"],
        QPalette.ToolTipText: colors["text"],
        QPalette.PlaceholderText: colors["margin_fg"],
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(colors["margin_fg"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(colors["margin_fg"]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
        QTabBar::tab {{ padding: 6px 14px; border: none; background: {colors['window']}; }}
        QTabBar::tab:selected {{
            background: {colors['base']}; border-bottom: 2px solid {colors['highlight']};
        }}
        QStatusBar QLabel {{ padding: 0 8px; color: {colors['margin_fg']}; }}
        QFrame#DropZone {{ border: 2px dashed {colors['margin_fg']}; border-radius: 6px; }}
        QFrame#DropZone[hover="true"] {{ border-color: {colors['highlight']}; }}
        QProgressBar {{
            border: 1px solid {colors['button']}; border-radius: 3px; text-align: center;
        }}
        QProgressBar::chunk {{ background: {colors['highlight']}; }}
    """)


# =============================================================================================
# Text files
# =============================================================================================


class BinaryFileError(Exception):
    """The file looks binary (or is too large) and should not be opened as text."""


def read_text_file(path: Path) -> tuple[str, str]:
    """Read a text file; returns (text, encoding). Tries UTF-8 (with/without BOM), then cp1252."""
    size = path.stat().st_size
    if size > MAX_EDITOR_BYTES:
        raise BinaryFileError(
            f"{path.name} is {size / 1e6:.0f} MB; the editor opens files up to 20 MB."
        )
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise BinaryFileError(f"{path.name} looks like a binary file.")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "UTF-8 BOM"
    try:
        return data.decode("utf-8"), "UTF-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "Windows-1252"


ENCODING_CODECS = {"UTF-8": "utf-8", "UTF-8 BOM": "utf-8-sig", "Windows-1252": "cp1252"}


def write_text_file(path: Path, text: str, encoding: str) -> None:
    """Write atomically (temporary file + rename) so a failed save never truncates the file."""
    codec = ENCODING_CODECS.get(encoding, "utf-8")
    data = text.encode(codec, errors="replace")
    tmp = path.with_name(f".{path.name}.perdix-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# =============================================================================================
# Editor
# =============================================================================================


class CodeEditor(QsciScintilla):
    """A QScintilla editor bound to one file: language, encoding, theme, smart indentation."""

    def __init__(
        self, theme: str, path: Path | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.path = path
        self.encoding = "UTF-8"
        self.theme = theme
        self.language = language_for_path(path)
        self._api: QsciAPIs | None = None
        self._setup()
        self.set_language(self.language)
        self.SCN_CHARADDED.connect(self._char_added)

    def _setup(self) -> None:
        self.setUtf8(True)
        self.setEolMode(QsciScintilla.EolUnix)
        self.setMarginType(0, QsciScintilla.NumberMargin)
        self.setMarginWidth(0, "00000")
        self.setMarginWidth(1, 0)
        self.setFolding(QsciScintilla.BoxedTreeFoldStyle, 2)
        self.setBraceMatching(QsciScintilla.SloppyBraceMatch)
        self.setCaretLineVisible(True)
        self.setCaretWidth(2)
        self.setIndentationsUseTabs(False)
        self.setTabWidth(4)
        self.setIndentationGuides(True)
        self.setAutoIndent(True)
        self.setBackspaceUnindents(True)
        self.setTabIndents(True)
        self.setAutoCompletionThreshold(2)
        self.setAutoCompletionCaseSensitivity(False)
        self.setAutoCompletionReplaceWord(False)
        self.setScrollWidthTracking(True)
        self.setScrollWidth(1)

    # ---- language and theme -----------------------------------------------------------------

    def set_language(self, language: Language) -> None:
        """Switch highlighting, keyword completion and indentation rules."""
        self.language = language
        if language.lexer_class is None:
            self.setLexer(None)
            self._api = None
            self.setAutoCompletionSource(QsciScintilla.AcsDocument)
            self.setFont(monospace_font())
            self.setMarginsFont(monospace_font())
        else:
            lexer = language.lexer_class(self)
            style_lexer(lexer, self.theme)
            self.setLexer(lexer)
            self._api = QsciAPIs(lexer)
            for word in language.keywords:
                self._api.add(word)
            self._api.prepare()
            self.setAutoCompletionSource(QsciScintilla.AcsAll)
        self._apply_chrome()

    def set_theme(self, theme: str) -> None:
        self.theme = theme
        lexer = self.lexer()
        if lexer is not None:
            style_lexer(lexer, theme)
            self.setLexer(lexer)  # re-apply styles
        self._apply_chrome()

    def _apply_chrome(self) -> None:
        colors = THEMES[self.theme]
        self.setPaper(QColor(colors["paper"]))
        self.setColor(QColor(colors["text"]))
        self.setMarginsBackgroundColor(QColor(colors["margin_bg"]))
        self.setMarginsForegroundColor(QColor(colors["margin_fg"]))
        self.setFoldMarginColors(QColor(colors["fold"]), QColor(colors["fold"]))
        self.setCaretLineBackgroundColor(QColor(colors["caret_line"]))
        self.setCaretForegroundColor(QColor(colors["caret"]))
        self.setSelectionBackgroundColor(QColor(colors["selection"]))
        self.setMatchedBraceBackgroundColor(QColor(colors["brace_bg"]))
        self.setMatchedBraceForegroundColor(QColor(colors["keyword"]))
        self.setIndentationGuidesBackgroundColor(QColor(colors["margin_fg"]))
        self.setIndentationGuidesForegroundColor(QColor(colors["margin_fg"]))

    # ---- smart indentation --------------------------------------------------------------------

    def _char_added(self, char: int) -> None:
        """Indent after block openers; dedent closing brackets and Python block enders."""
        if char in (ord("\n"), ord("\r")):
            self._indent_new_line()
        elif chr(char) in self.language.closing_chars:
            self._dedent_closing_char()

    def _indent_new_line(self) -> None:
        line, _index = self.getCursorPosition()
        if line == 0:
            return
        previous = self.text(line - 1).rstrip("\r\n")
        stripped = previous.rstrip()
        base = self.indentation(line - 1)
        target = base
        if self.language.indent_after and stripped.endswith(self.language.indent_after):
            target = base + self.tabWidth()
        elif (
            self.language.name == "Python"
            and stripped.strip().split(" ")[0] in PYTHON_DEDENT_AFTER
        ):
            target = max(0, base - self.tabWidth())
        if target != self.indentation(line):
            self.setIndentation(line, target)
            self.setCursorPosition(line, target)

    def _dedent_closing_char(self) -> None:
        line, index = self.getCursorPosition()
        before_cursor = self.text(line)[: index - 1]
        if before_cursor.strip():
            return  # only when the bracket is the first thing on the line
        bracket_pos = self.positionFromLineIndex(line, index - 1)
        match = self.SendScintilla(QsciScintilla.SCI_BRACEMATCH, bracket_pos, 0)
        if match < 0:
            return
        open_line, _ = self.lineIndexFromPosition(match)
        target = self.indentation(open_line)
        if target != self.indentation(line):
            self.setIndentation(line, target)
            self.setCursorPosition(line, target + 1)


class FindBar(QWidget):
    """Incremental find for the current editor (Ctrl+F, Enter/F3 next, Shift+Enter previous)."""

    def __init__(
        self, current_editor: Callable[[], CodeEditor | None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._current_editor = current_editor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(QLabel("Find:"))
        self.field = QLineEdit(placeholderText="Text to find")
        self.field.returnPressed.connect(self.find_next)
        self.field.textEdited.connect(lambda _text: self.find_next(from_start=True))
        layout.addWidget(self.field, 1)
        self.case = QCheckBox("Match case")
        layout.addWidget(self.case)
        for text, handler in (("Previous", self.find_previous), ("Next", self.find_next)):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked, h=handler: h())
            layout.addWidget(button)
        close = QToolButton(text="✕", autoRaise=True)
        close.clicked.connect(self.hide)
        layout.addWidget(close)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.hide()

    def open(self) -> None:
        editor = self._current_editor()
        if editor is not None and editor.hasSelectedText() and "\n" not in editor.selectedText():
            self.field.setText(editor.selectedText())
        self.show()
        self.field.setFocus()
        self.field.selectAll()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key_Escape:
            self.hide()
            editor = self._current_editor()
            if editor is not None:
                editor.setFocus()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ShiftModifier:
            self.find_previous()
        else:
            super().keyPressEvent(event)

    def find_next(self, from_start: bool = False) -> bool:
        return self._find(forward=True, from_start=from_start)

    def find_previous(self) -> bool:
        return self._find(forward=False, from_start=False)

    def _find(self, forward: bool, from_start: bool) -> bool:
        editor, text = self._current_editor(), self.field.text()
        if editor is None or not text:
            self.status.setText("")
            return False
        line, index = -1, -1
        if from_start or (not forward and editor.hasSelectedText()):
            start_line, start_index, _end_line, _end_index = editor.getSelection()
            if from_start:
                line, index = (
                    (start_line, start_index)
                    if editor.hasSelectedText()
                    else editor.getCursorPosition()
                )
            else:
                line, index = start_line, start_index
        found = editor.findFirst(
            text, False, self.case.isChecked(), False, True, forward, line, index
        )
        self.status.setText("" if found else "Not found")
        return found


class EditorPanel(QWidget):
    """Tabbed editors with file open / save / save as / close, plus the find bar."""

    status_changed = pyqtSignal(str, str, str)  # position, encoding, language

    def __init__(self, settings: StudioSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.tabs = QTabWidget(documentMode=True, tabsClosable=True, movable=True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(lambda _i: self._emit_status())
        self.find_bar = FindBar(self.current_editor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.find_bar)
        self._untitled = 0

    # ---- tab helpers --------------------------------------------------------------------------

    def current_editor(self) -> CodeEditor | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, CodeEditor) else None

    def editors(self) -> list[CodeEditor]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _title(self, editor: CodeEditor) -> str:
        name = editor.path.name if editor.path else editor.property("untitled")
        return f"● {name}" if editor.isModified() else name

    def _refresh_title(self, editor: CodeEditor) -> None:
        index = self.tabs.indexOf(editor)
        if index >= 0:
            self.tabs.setTabText(index, self._title(editor))
            self.tabs.setTabToolTip(index, str(editor.path) if editor.path else "Not saved yet")

    def _add_editor(self, editor: CodeEditor) -> None:
        editor.modificationChanged.connect(lambda _m, e=editor: self._refresh_title(e))
        editor.cursorPositionChanged.connect(lambda _l, _i: self._emit_status())
        self.tabs.addTab(editor, "")
        self._refresh_title(editor)
        self.tabs.setCurrentWidget(editor)
        editor.setFocus()

    def _emit_status(self) -> None:
        """Send the current editor's position, encoding and language to the status bar."""
        editor = self.current_editor()
        if editor is None:
            self.status_changed.emit("", "", "")
            return
        line, index = editor.getCursorPosition()
        self.status_changed.emit(
            f"Ln {line + 1}, Col {index + 1}", editor.encoding, editor.language.name
        )

    # ---- file actions -------------------------------------------------------------------------

    def new_file(self) -> CodeEditor:
        self._untitled += 1
        editor = CodeEditor(self.settings.theme)
        editor.setProperty("untitled", f"Untitled-{self._untitled}")
        self._add_editor(editor)
        return editor

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Open files",
            self.settings.last_dir,
            "Source files (*.c *.h *.cpp *.cc *.cxx *.hpp *.cs *.py "
            "*.js *.mjs *.java);;All files (*)",
        )
        for path in paths:
            self.open_file(Path(path))

    def open_file(self, path: Path) -> bool:
        """Open a file in a new tab (or focus it if it is already open). Never raises."""
        path = path.resolve()
        for editor in self.editors():
            if editor.path == path:
                self.tabs.setCurrentWidget(editor)
                return True
        try:
            text, encoding = read_text_file(path)
        except BinaryFileError as error:
            QMessageBox.information(self, APP_NAME, str(error))
            return False
        except OSError as error:
            QMessageBox.warning(
                self, APP_NAME, f"Could not open {path}:\n{error.strerror or error}"
            )
            return False
        editor = CodeEditor(self.settings.theme, path)
        editor.encoding = encoding
        editor.setText(text)
        editor.setModified(False)
        editor.SendScintilla(QsciScintilla.SCI_EMPTYUNDOBUFFER)
        self._add_editor(editor)
        self.settings.last_dir = str(path.parent)
        self._emit_status()
        return True

    def save(self, editor: CodeEditor | None = None) -> bool:
        editor = editor or self.current_editor()
        if editor is None:
            return False
        if editor.path is None:
            return self.save_as(editor)
        try:
            write_text_file(editor.path, editor.text(), editor.encoding)
        except OSError as error:
            QMessageBox.warning(
                self, APP_NAME, f"Could not save {editor.path}:\n{error.strerror or error}"
            )
            return False
        editor.setModified(False)
        return True

    def save_as(self, editor: CodeEditor | None = None) -> bool:
        editor = editor or self.current_editor()
        if editor is None:
            return False
        start = str(editor.path) if editor.path else self.settings.last_dir
        path, _ = QFileDialog.getSaveFileName(self, "Save as", start)
        if not path:
            return False
        editor.path = Path(path).resolve()
        self.settings.last_dir = str(editor.path.parent)
        new_language = language_for_path(editor.path)
        if new_language is not editor.language:
            editor.set_language(new_language)
        saved = self.save(editor)
        self._refresh_title(editor)
        self._emit_status()
        return saved

    def maybe_save(self, editor: CodeEditor) -> bool:
        """Ask about unsaved changes. Returns False if the user cancelled."""
        if not editor.isModified():
            return True
        self.tabs.setCurrentWidget(editor)
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Save changes to {self._title(editor).lstrip('● ')}?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Save:
            return self.save(editor)
        return answer == QMessageBox.Discard

    def close_tab(self, index: int | None = None) -> bool:
        index = self.tabs.currentIndex() if index is None else index
        editor = self.tabs.widget(index)
        if not isinstance(editor, CodeEditor) or not self.maybe_save(editor):
            return False
        self.tabs.removeTab(index)
        editor.deleteLater()
        self._emit_status()
        return True

    def close_all(self) -> bool:
        return all(self.maybe_save(editor) for editor in self.editors())

    def set_theme(self, theme: str) -> None:
        for editor in self.editors():
            editor.set_theme(theme)

    def set_language(self, name: str) -> None:
        editor = self.current_editor()
        if editor is not None:
            editor.set_language(LANGUAGE_BY_NAME[name])
            self._emit_status()


# =============================================================================================
# Settings (QSettings)
# =============================================================================================


@dataclass(frozen=True)
class ToolPreset:
    """A ready-made program + argument template for an archive tool."""

    label: str
    program: str
    args: str


DECOMPILERS = {
    # name -> argument template run with the Java program; {jar} is the decompiler jar.
    "CFR": "-jar {jar} {input} --outputdir {output}",
    "Fernflower / Vineflower": "-jar {jar} -dgs=1 {input} {output}",
    "Procyon": "-jar {jar} -jar {input} -o {output}",
}
BSA_PRESETS = (
    ToolPreset("bsatool (OpenMW, Linux)", "bsatool", "extractall {input} {output}"),
    ToolPreset("BSArch via Wine (Linux)", "wine", "C:/Tools/BSArch.exe unpack {input} {output}"),
    ToolPreset("BSArch (Windows)", "BSArch.exe", "unpack {input} {output}"),
)
PAK_PRESETS = (
    ToolPreset("repak (Unreal Engine 4/5)", "repak", "unpack {input} --output {output}"),
)


class _Setting:
    """A string setting stored in QSettings, with a default and optional allowed values."""

    def __init__(
        self, key: str, default: str | Callable[[], str], choices: tuple[str, ...] | None = None
    ) -> None:
        self.key, self.default, self.choices = key, default, choices

    def __get__(self, owner: StudioSettings | None, _type: type | None = None) -> str:
        default = self.default() if callable(self.default) else self.default
        if owner is None:
            return default
        value = owner.qsettings.value(self.key, default)
        if not isinstance(value, str) or (self.choices and value not in self.choices):
            return default
        return value

    def __set__(self, owner: StudioSettings, value: str) -> None:
        owner.qsettings.setValue(self.key, value)


def _default_workdir() -> str:
    return str(Path(tempfile.gettempdir()) / "perdix-studio")


class StudioSettings:
    """Typed access to the persistent settings (QSettings, organisation "PERDIX")."""

    theme = _Setting("ui/theme", "dark", tuple(THEMES))
    last_dir = _Setting("ui/last_dir", lambda: str(Path.home()))
    workdir = _Setting("modding/workdir", _default_workdir)
    java = _Setting("tools/java", "java")
    decompiler = _Setting("tools/decompiler", "CFR", tuple(DECOMPILERS))
    bsa_program = _Setting("tools/bsa_program", BSA_PRESETS[0].program)
    bsa_args = _Setting("tools/bsa_args", BSA_PRESETS[0].args)
    pak_program = _Setting("tools/pak_program", PAK_PRESETS[0].program)
    pak_args = _Setting("tools/pak_args", PAK_PRESETS[0].args)

    def __init__(self, qsettings: QSettings | None = None) -> None:
        self.qsettings = qsettings or QSettings(ORG_NAME, APP_NAME)

    def decompiler_jar(self, name: str) -> str:
        """Path of the jar for a decompiler ('' if not set)."""
        value = self.qsettings.value(f"tools/jar/{name}", "")
        return value if isinstance(value, str) else ""

    def set_decompiler_jar(self, name: str, path: str) -> None:
        self.qsettings.setValue(f"tools/jar/{name}", path)

    @property
    def timeout(self) -> int:
        """Seconds a tool may run before it is stopped."""
        try:
            return max(10, int(self.qsettings.value("modding/timeout", 600)))
        except (TypeError, ValueError):
            return 600

    @timeout.setter
    def timeout(self, seconds: int) -> None:
        self.qsettings.setValue("modding/timeout", int(seconds))

    def sync(self) -> None:
        self.qsettings.sync()


# =============================================================================================
# Archives: detection, validation, commands
# =============================================================================================


class ArchiveKind(enum.Enum):
    JAR = "Java archive (.jar)"
    PAK_QUAKE = "Quake / Half-Life PACK (.pak)"
    PAK_UNREAL = "Unreal Engine 4/5 (.pak)"
    PAK_OTHER = "Unknown .pak format"
    BSA = "Bethesda archive (.bsa)"


class ModdingError(Exception):
    """A problem to show the user (bad input, missing tool, failed run). Never fatal."""


UNREAL_PAK_MAGIC = struct.pack("<I", 0x5A6F12E1)
BSA_MAGICS = (b"BSA\x00", b"\x00\x01\x00\x00")  # Oblivion/FO3/NV/Skyrim, Morrowind


def validate_archive_path(path: str | os.PathLike) -> Path:
    """The resolved archive path, or ModdingError if it is not a readable .jar/.pak/.bsa file."""
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ModdingError(f"File not found: {path}") from error
    if not resolved.is_file():
        raise ModdingError(f"Not a file: {resolved}")
    if resolved.suffix.lower() not in ARCHIVE_EXTENSIONS:
        raise ModdingError(
            f"Unsupported file type '{resolved.suffix}'. Drop a .jar, .pak or .bsa file."
        )
    if not os.access(resolved, os.R_OK):
        raise ModdingError(f"No permission to read {resolved}")
    if resolved.stat().st_size == 0:
        raise ModdingError(f"{resolved.name} is empty.")
    return resolved


def detect_archive(path: Path) -> ArchiveKind:
    """Identify the archive by extension and magic bytes. Raises ModdingError on a mismatch."""
    suffix = path.suffix.lower()
    with path.open("rb") as f:
        head = f.read(4)
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 1024))
        tail = f.read()
    if suffix == ".jar":
        if head[:2] != b"PK":
            raise ModdingError(f"{path.name} is not a valid .jar (no ZIP signature).")
        return ArchiveKind.JAR
    if suffix == ".bsa":
        if head not in BSA_MAGICS:
            raise ModdingError(
                f"{path.name} is not a Morrowind–Skyrim .bsa (unknown signature {head!r})."
            )
        return ArchiveKind.BSA
    if head == b"PACK":
        return ArchiveKind.PAK_QUAKE
    if UNREAL_PAK_MAGIC in tail:
        return ArchiveKind.PAK_UNREAL
    return ArchiveKind.PAK_OTHER


def resolve_program(program: str) -> str:
    """Absolute path of an executable given as a path or a name on PATH, or ModdingError."""
    program = program.strip()
    if not program:
        raise ModdingError("No program configured. Set it in Tools > Settings.")
    candidate = Path(program).expanduser()
    if candidate.parent != Path(".") or candidate.is_absolute():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            # Absolute, but not symlink-resolved: tools such as virtualenv interpreters or
            # multi-call binaries depend on the name they are started under.
            return str(candidate.absolute())
        raise ModdingError(f"Tool not found or not executable: {candidate}")
    found = shutil.which(program)
    if found is None:
        raise ModdingError(
            f"'{program}' was not found on PATH. "
            "Install it or set its full path in Tools > Settings."
        )
    return found


def build_command(program: str, args_template: str, values: dict[str, str]) -> list[str]:
    """argv for a tool: the template is split like a shell would (no shell runs), then each
    {placeholder} is replaced inside its own argument, so paths with spaces stay one argument."""
    if "{input}" not in args_template:
        raise ModdingError(f"The argument template must contain {{input}}: '{args_template}'")
    try:
        tokens = shlex.split(args_template)
    except ValueError as error:
        raise ModdingError(f"Bad argument template '{args_template}': {error}") from error
    command = [resolve_program(program)]
    for token in tokens:
        for key, value in values.items():
            token = token.replace("{" + key + "}", value)
        command.append(token)
    return command


def plan_job(
    archive: Path, kind: ArchiveKind, output: Path, settings: StudioSettings
) -> list[str] | None:
    """The command to run for an archive, or None for the built-in PACK extractor."""
    values = {"input": str(archive), "output": str(output)}
    if kind is ArchiveKind.JAR:
        name = settings.decompiler
        jar = settings.decompiler_jar(name)
        if not jar:
            raise ModdingError(f"Set the path of the {name} decompiler jar in Tools > Settings.")
        jar_path = Path(jar).expanduser()
        if not jar_path.is_file() or jar_path.suffix.lower() != ".jar":
            raise ModdingError(f"{name} decompiler jar not found: {jar_path}")
        return build_command(
            settings.java, DECOMPILERS[name], {**values, "jar": str(jar_path.resolve())}
        )
    if kind is ArchiveKind.PAK_QUAKE:
        return None
    if kind is ArchiveKind.BSA:
        return build_command(settings.bsa_program, settings.bsa_args, values)
    return build_command(settings.pak_program, settings.pak_args, values)


def safe_join(root: Path, member: str) -> Path:
    """root/member, refusing absolute paths and '..' that would escape root (zip-slip)."""
    member = member.replace("\\", "/").lstrip("/")
    target = (root / member).resolve()
    if target != root.resolve() and root.resolve() not in target.parents:
        raise ModdingError(f"Refusing unsafe path in archive: {member!r}")
    return target


def extract_quake_pak(
    archive: Path,
    output: Path,
    progress: Callable[[int, int], None],
    cancelled: Callable[[], bool],
) -> int:
    """Built-in extractor for Quake/Half-Life PACK files. Returns the number of files."""
    with archive.open("rb") as f:
        header = f.read(12)
        if len(header) != 12 or header[:4] != b"PACK":
            raise ModdingError("Not a PACK archive.")
        dir_offset, dir_length = struct.unpack("<ii", header[4:])
        size = archive.stat().st_size
        if dir_offset < 12 or dir_length < 0 or dir_length % 64 or dir_offset + dir_length > size:
            raise ModdingError("Corrupt PACK directory.")
        f.seek(dir_offset)
        directory = f.read(dir_length)
        entries = [struct.unpack_from("<56sii", directory, i) for i in range(0, dir_length, 64)]
        for number, (raw_name, offset, length) in enumerate(entries, 1):
            if cancelled():
                raise InterruptedError
            name = raw_name.split(b"\x00", 1)[0].decode("latin-1")
            if not name or offset < 0 or length < 0 or offset + length > size:
                raise ModdingError(f"Corrupt PACK entry {number}: {name!r}")
            target = safe_join(output, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            f.seek(offset)
            target.write_bytes(f.read(length))
            progress(number, len(entries))
    return len(entries)


def expand_source_jars(
    output: Path, log: Callable[[str], None], cancelled: Callable[[], bool]
) -> None:
    """Fernflower writes sources as a .jar in the output folder: unpack it (zip-slip safe)."""
    for jar in list(output.glob("*.jar")):
        log(f"Unpacking decompiled sources from {jar.name}")
        with zipfile.ZipFile(jar) as archive:
            for info in archive.infolist():
                if cancelled():
                    raise InterruptedError
                target = safe_join(output, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        jar.unlink()


# =============================================================================================
# Background worker
# =============================================================================================


class ExtractWorker(QThread):
    """Runs one extraction off the UI thread: an external tool (subprocess, shell=False) or the
    built-in PACK extractor. Output is streamed to the log; cancel() stops it."""

    log = pyqtSignal(str)
    progress = pyqtSignal(int, int)  # value, maximum (maximum 0 = busy indicator)
    succeeded = pyqtSignal(str, int)  # output folder, number of files
    failed = pyqtSignal(str)

    def __init__(
        self,
        archive: Path,
        kind: ArchiveKind,
        output: Path,
        command: list[str] | None,
        timeout: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.archive, self.kind, self.output, self.command, self.timeout = (
            archive,
            kind,
            output,
            command,
            timeout,
        )
        self._cancel = threading.Event()
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        """Ask the job to stop; a running tool gets SIGTERM, then SIGKILL after 3 s."""
        self._cancel.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            if self.command is None:
                self.log.emit(f"Extracting {self.archive.name} with the built-in PACK extractor")
                count = extract_quake_pak(
                    self.archive, self.output, self.progress.emit, self._cancel.is_set
                )
                self.log.emit(f"{count} files extracted")
            else:
                self._run_tool()
                if self.kind is ArchiveKind.JAR:
                    expand_source_jars(self.output, self.log.emit, self._cancel.is_set)
            files = sum(1 for p in self.output.rglob("*") if p.is_file())
            if files == 0:
                raise ModdingError(
                    "The tool finished but produced no files. Check the log and the tool settings."
                )
            self.progress.emit(1, 1)
            self.succeeded.emit(str(self.output), files)
        except InterruptedError:
            self.failed.emit("Cancelled.")
        except ModdingError as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - report anything instead of crashing the app
            self.failed.emit(f"Unexpected error: {type(error).__name__}: {error}")

    def _run_tool(self) -> None:
        assert self.command is not None
        self.log.emit("$ " + " ".join(shlex.quote(part) for part in self.command))
        self.progress.emit(0, 0)
        try:
            self._process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                shell=False,
                cwd=self.output,
                text=True,
                errors="replace",
                bufsize=1,
                start_new_session=(os.name == "posix"),
            )
        except OSError as error:
            raise ModdingError(
                f"Could not start {self.command[0]}: {error.strerror or error}"
            ) from error

        lines: queue.Queue[str | None] = queue.Queue()

        def pump() -> None:  # reading blocks, so it gets its own small thread
            assert self._process is not None and self._process.stdout is not None
            for line in self._process.stdout:
                lines.put(line.rstrip("\n"))
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        deadline = time.monotonic() + self.timeout
        tail: list[str] = []
        while True:
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line:
                self.log.emit(line)
                tail = (tail + [line])[-8:]
            if self._cancel.is_set():
                self._stop_process()
                raise InterruptedError
            if time.monotonic() > deadline:
                self._stop_process()
                raise ModdingError(
                    f"The tool did not finish within {self.timeout} s and was stopped."
                )
        code = self._process.wait()
        if self._cancel.is_set():
            raise InterruptedError
        if code != 0:
            details = "\n".join(tail[-5:])
            raise ModdingError(
                f"{Path(self.command[0]).name} failed (exit code {code})."
                + (f"\n\nLast output:\n{details}" if details else "")
            )

    def _stop_process(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


# =============================================================================================
# Modding Tools tab
# =============================================================================================


class DropZone(QFrame):
    """Accepts one dropped .jar/.pak/.bsa file (dragEnterEvent/dropEvent)."""

    file_dropped = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="DropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(90)
        layout = QVBoxLayout(self)
        title = QLabel("Drop a .jar, .pak or .bsa file here", alignment=Qt.AlignCenter)
        font = title.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        layout.addWidget(
            QLabel(
                ".jar → Java decompiler   ·   .pak → repak / built-in PACK   ·   "
                ".bsa → bsatool / BSArch",
                alignment=Qt.AlignCenter,
            )
        )

    @staticmethod
    def _archive_url(event: QDragEnterEvent | QDropEvent) -> str | None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if len(urls) != 1 or not urls[0].isLocalFile():
            return None
        path = urls[0].toLocalFile()
        return path if path.lower().endswith(ARCHIVE_EXTENSIONS) else None

    def _set_hover(self, hover: bool) -> None:
        self.setProperty("hover", hover)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._archive_url(event):
            event.acceptProposedAction()
            self._set_hover(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(False)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._set_hover(False)
        path = self._archive_url(event)
        if path:
            event.acceptProposedAction()
            self.file_dropped.emit(path)


class ModdingPanel(QWidget):
    """Drop an archive, unpack it in the background, browse the result and open files."""

    open_in_editor = pyqtSignal(str)

    def __init__(self, settings: StudioSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.worker: ExtractWorker | None = None

        self.drop_zone = DropZone()
        self.drop_zone.file_dropped.connect(self.start)
        open_button = QPushButton("Open archive…")
        open_button.clicked.connect(self.open_dialog)
        self.info = QLabel("No archive loaded.")
        self.info.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.cancel_button)

        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(""))
        self.tree.setEnabled(False)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.doubleClicked.connect(self._double_clicked)
        self.tree.setColumnWidth(0, 340)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setFont(monospace_font(10))
        self.log.setMaximumBlockCount(5000)
        self.log.setPlaceholderText("Tool output and errors appear here.")

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.log)
        splitter.setSizes([420, 180])

        top = QHBoxLayout()
        top.addWidget(self.drop_zone, 1)
        side = QVBoxLayout()
        side.addWidget(open_button)
        side.addStretch(1)
        top.addLayout(side)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.info)
        layout.addLayout(progress_row)
        layout.addWidget(splitter, 1)

    # ---- actions ------------------------------------------------------------------------------

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open archive", self.settings.last_dir, "Mod archives (*.jar *.pak *.bsa)"
        )
        if path:
            self.start(path)

    def start(self, path: str) -> None:
        """Validate, detect, plan and launch an extraction. Problems are shown, never raised."""
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(
                self, APP_NAME, "An archive is still being unpacked. Cancel it first."
            )
            return
        output: Path | None = None
        try:
            archive = validate_archive_path(path)
            kind = detect_archive(archive)
            output = self._new_output_dir(archive)
            command = plan_job(archive, kind, output, self.settings)
        except (ModdingError, OSError) as error:
            if output is not None:
                shutil.rmtree(output, ignore_errors=True)  # nothing was extracted into it
            self._error(str(error))
            return
        self.settings.last_dir = str(archive.parent)
        tool = "built-in PACK extractor" if command is None else Path(command[0]).name
        if kind is ArchiveKind.JAR:
            tool = f"{self.settings.decompiler} (Java)"
        self.info.setText(f"<b>{archive.name}</b> — {kind.value}. Tool: {tool}. Output: {output}")
        if kind is ArchiveKind.PAK_OTHER:
            self._log(
                f"Warning: {archive.name} is not a known .pak layout; "
                "trying the configured .pak tool."
            )
        self.log.appendPlainText(f"\n=== {time.strftime('%H:%M:%S')}  {archive}")
        self.tree.setEnabled(False)
        self.progress.setRange(0, 0)
        self.cancel_button.setEnabled(True)
        self.worker = ExtractWorker(archive, kind, output, command, self.settings.timeout, self)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._progress)
        self.worker.succeeded.connect(self._succeeded)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.cancel_button.setEnabled(False)
            self._log("Cancelling…")
            self.worker.cancel()

    def shutdown(self) -> None:
        """Stop a running job before the window closes."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)

    def _new_output_dir(self, archive: Path) -> Path:
        root = Path(self.settings.workdir).expanduser()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ModdingError(
                f"Cannot create the working directory {root}: {error.strerror or error}"
            ) from error
        stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in archive.stem) or "archive"
        return Path(tempfile.mkdtemp(prefix=f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}-", dir=root))

    # ---- worker signals -----------------------------------------------------------------------

    def _log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _progress(self, value: int, maximum: int) -> None:
        self.progress.setRange(0, maximum)
        self.progress.setValue(value)

    def _succeeded(self, output: str, files: int) -> None:
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._log(f"Done: {files} files in {output}")
        self.model.setRootPath(output)
        self.tree.setRootIndex(self.model.index(output))
        self.tree.setEnabled(True)

    def _failed(self, message: str) -> None:
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        if message == "Cancelled.":
            self._log("Cancelled.")
        else:
            self._error(message)

    def _error(self, message: str) -> None:
        self._log(f"ERROR: {message}")
        QMessageBox.warning(self, f"{APP_NAME}: Modding Tools", message)

    def _double_clicked(self, index: QModelIndex) -> None:
        if not self.model.isDir(index):
            self.open_in_editor.emit(self.model.filePath(index))


class SettingsDialog(QDialog):
    """Tool paths, working directory, timeout and theme."""

    def __init__(self, settings: StudioSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(f"{APP_NAME} settings")
        self.setMinimumWidth(640)
        form = QFormLayout(self)

        self.theme = QComboBox()
        self.theme.addItems(["dark", "light"])
        self.theme.setCurrentText(settings.theme)
        form.addRow("Theme", self.theme)
        self.workdir = self._path_row(form, "Working directory", settings.workdir, directory=True)
        self.timeout = QSpinBox(minimum=10, maximum=86400, suffix=" s", value=settings.timeout)
        form.addRow("Tool timeout", self.timeout)

        form.addRow(QLabel("<b>.jar: Java decompiler</b>"))
        self.java = self._path_row(form, "Java program", settings.java)
        self.decompiler = QComboBox()
        self.decompiler.addItems(list(DECOMPILERS))
        self.decompiler.setCurrentText(settings.decompiler)
        form.addRow("Decompiler", self.decompiler)
        self.jars = {
            name: self._path_row(
                form,
                f"{name} jar",
                settings.decompiler_jar(name),
                file_filter="Java archives (*.jar)",
            )
            for name in DECOMPILERS
        }

        form.addRow(QLabel("<b>.bsa: unpacker</b>"))
        self.bsa_program, self.bsa_args = self._tool_rows(
            form, BSA_PRESETS, settings.bsa_program, settings.bsa_args
        )
        form.addRow(
            QLabel("<b>.pak: unpacker</b> (Quake/Half-Life PACK files use the built-in extractor)")
        )
        self.pak_program, self.pak_args = self._tool_rows(
            form, PAK_PRESETS, settings.pak_program, settings.pak_args
        )
        form.addRow(
            QLabel(
                "Arguments are split like a shell command but never run through a shell. "
                "Placeholders: {input}, {output}."
            )
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _path_row(
        self,
        form: QFormLayout,
        label: str,
        value: str,
        directory: bool = False,
        file_filter: str = "All files (*)",
    ) -> QLineEdit:
        field = QLineEdit(value)
        browse = QPushButton("Browse…")

        def pick() -> None:
            if directory:
                chosen = QFileDialog.getExistingDirectory(self, label, field.text())
            else:
                chosen, _ = QFileDialog.getOpenFileName(self, label, field.text(), file_filter)
            if chosen:
                field.setText(chosen)

        browse.clicked.connect(pick)
        row = QHBoxLayout()
        row.addWidget(field, 1)
        row.addWidget(browse)
        form.addRow(label, row)
        return field

    def _tool_rows(
        self, form: QFormLayout, presets: tuple[ToolPreset, ...], program: str, args: str
    ) -> tuple[QLineEdit, QLineEdit]:
        preset = QComboBox()
        preset.addItem("Custom")
        for item in presets:
            preset.addItem(item.label)
        form.addRow("Preset", preset)
        program_field = self._path_row(form, "Program", program)
        args_field = QLineEdit(args)
        form.addRow("Arguments", args_field)

        def apply(index: int) -> None:
            if index > 0:
                program_field.setText(presets[index - 1].program)
                args_field.setText(presets[index - 1].args)

        preset.currentIndexChanged.connect(apply)
        return program_field, args_field

    def accept(self) -> None:
        s = self.settings
        s.theme = self.theme.currentText()
        s.workdir = self.workdir.text().strip() or s.workdir
        s.timeout = self.timeout.value()
        s.java = self.java.text().strip() or "java"
        s.decompiler = self.decompiler.currentText()
        for name, field in self.jars.items():
            s.set_decompiler_jar(name, field.text().strip())
        s.bsa_program, s.bsa_args = self.bsa_program.text().strip(), self.bsa_args.text().strip()
        s.pak_program, s.pak_args = self.pak_program.text().strip(), self.pak_args.text().strip()
        s.sync()
        super().accept()


# =============================================================================================
# Main window
# =============================================================================================


class MainWindow(QMainWindow):
    """Editor and Modding Tools tabs, menus, shortcuts and the status bar."""

    def __init__(self, settings: StudioSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or StudioSettings()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 820)
        self.setAcceptDrops(True)

        self.editor = EditorPanel(self.settings)
        self.modding = ModdingPanel(self.settings)
        self.modding.open_in_editor.connect(self.open_from_modding)
        self.main_tabs = QTabWidget(documentMode=True)
        self.main_tabs.addTab(self.editor, "Editor")
        self.main_tabs.addTab(self.modding, "Modding Tools")
        self.setCentralWidget(self.main_tabs)

        self.position_label, self.encoding_label, self.language_label = (
            QLabel(),
            QLabel(),
            QLabel(),
        )
        for label in (self.position_label, self.encoding_label, self.language_label):
            self.statusBar().addPermanentWidget(label)
        self.editor.status_changed.connect(self._update_status)
        self.main_tabs.currentChanged.connect(self._tab_changed)
        self._build_menus()
        self.editor.new_file()

    # ---- menus --------------------------------------------------------------------------------

    def _action(
        self,
        menu,
        text: str,
        handler: Callable[[], object],
        shortcut=None,
        checkable: bool = False,
    ) -> QAction:
        action = QAction(text, self, checkable=checkable)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: handler())
        menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        self._action(file_menu, "&New", self._new, QKeySequence.New)
        self._action(file_menu, "&Open…", self._open, QKeySequence.Open)
        self._action(file_menu, "&Save", self.editor.save, QKeySequence.Save)
        self._action(file_menu, "Save &As…", self.editor.save_as, "Ctrl+Shift+S")
        self._action(file_menu, "&Close Tab", self.editor.close_tab, "Ctrl+W")
        file_menu.addSeparator()
        self._action(file_menu, "E&xit", self.close, "Ctrl+Q")

        edit_menu = bar.addMenu("&Edit")
        # The editor handles these keys itself; binding them window-wide would steal Ctrl+C/V
        # from the find field and the log panel, so the menu only shows them.
        for text, method, keys in (
            ("&Undo", "undo", "Ctrl+Z"),
            ("&Redo", "redo", "Ctrl+Y"),
            ("Cu&t", "cut", "Ctrl+X"),
            ("&Copy", "copy", "Ctrl+C"),
            ("&Paste", "paste", "Ctrl+V"),
            ("Select &All", "selectAll", "Ctrl+A"),
        ):
            self._action(edit_menu, f"{text}\t{keys}", lambda m=method: self._editor_call(m))
        edit_menu.addSeparator()
        self._action(edit_menu, "&Find…", self._find, QKeySequence.Find)
        self._action(edit_menu, "Find &Next", self.editor.find_bar.find_next, "F3")
        self._action(edit_menu, "Find Pre&vious", self.editor.find_bar.find_previous, "Shift+F3")

        view_menu = bar.addMenu("&View")
        self.dark_action = self._action(
            view_menu, "&Dark Theme", self._toggle_theme, checkable=True
        )
        self.dark_action.setChecked(self.settings.theme == "dark")
        language_menu = view_menu.addMenu("&Language")
        for lang in LANGUAGES:
            self._action(language_menu, lang.name, lambda n=lang.name: self.editor.set_language(n))

        tools_menu = bar.addMenu("&Tools")
        self._action(tools_menu, "Open &Archive…", self._open_archive, "Ctrl+Shift+O")
        self._action(tools_menu, "&Settings…", self._settings, "Ctrl+,")
        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "&About", self._about)

    def _editor_call(self, method: str) -> None:
        editor = self.editor.current_editor()
        if editor is not None and self.main_tabs.currentWidget() is self.editor:
            getattr(editor, method)()

    def _new(self) -> None:
        self.main_tabs.setCurrentWidget(self.editor)
        self.editor.new_file()

    def _open(self) -> None:
        self.main_tabs.setCurrentWidget(self.editor)
        self.editor.open_dialog()

    def _find(self) -> None:
        self.main_tabs.setCurrentWidget(self.editor)
        self.editor.find_bar.open()

    def _open_archive(self) -> None:
        self.main_tabs.setCurrentWidget(self.modding)
        self.modding.open_dialog()

    def _toggle_theme(self) -> None:
        self.set_theme("dark" if self.dark_action.isChecked() else "light")

    def set_theme(self, theme: str) -> None:
        self.settings.theme = theme
        apply_app_theme(QApplication.instance(), theme)
        self.editor.set_theme(theme)
        self.dark_action.setChecked(theme == "dark")

    def _settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_() == QDialog.Accepted:
            self.set_theme(self.settings.theme)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            APP_NAME,
            f"<b>{APP_NAME}</b><br>Code editor and modding tools for PERDIX OS."
            "<br>Editor: QScintilla. Settings: Tools > Settings.",
        )

    # ---- status, drops, closing ---------------------------------------------------------------

    def _update_status(self, position: str, encoding: str, language: str) -> None:
        self.position_label.setText(position)
        self.encoding_label.setText(encoding)
        self.language_label.setText(language)

    def _tab_changed(self, _index: int) -> None:
        """Editor details in the status bar only while the editor is showing."""
        if self.main_tabs.currentWidget() is self.editor:
            self.editor._emit_status()
        else:
            self._update_status("", "", "")

    def open_from_modding(self, path: str) -> None:
        if self.editor.open_file(Path(path)):
            self.main_tabs.setCurrentWidget(self.editor)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        """Archives dropped anywhere go to Modding Tools; other files open in the editor."""
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(ARCHIVE_EXTENSIONS):
                self.main_tabs.setCurrentWidget(self.modding)
                self.modding.start(path)
            elif os.path.isfile(path):
                self.open_from_modding(path)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self.editor.close_all():
            event.ignore()
            return
        self.modding.shutdown()
        self.settings.sync()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    """Start PERDIX Studio; file arguments are opened in the editor."""
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    settings = StudioSettings()
    apply_app_theme(app, settings.theme)
    window = MainWindow(settings)
    window.show()
    for arg in argv[1:]:
        if arg.lower().endswith(ARCHIVE_EXTENSIONS):
            window.main_tabs.setCurrentWidget(window.modding)
            window.modding.start(arg)
        elif os.path.isfile(arg):
            window.open_from_modding(arg)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
