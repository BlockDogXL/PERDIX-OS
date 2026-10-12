# PERDIX Studio

Code editor (C, C++, C#, Python, JavaScript; Java for decompiled sources) with a **Modding
Tools** tab that unpacks `.jar`, `.pak` and `.bsa` archives in the background and opens the
extracted files in the editor. Single file: `perdix_studio.py`.

## Install and run

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # Arch: pacman -S python-pyqt5 python-qscintilla-qt5
python3 perdix_studio.py [files...]
```

Modding tools (set their paths in **Tools → Settings**):

| Archive | Default tool | Notes |
|---|---|---|
| `.jar` | CFR (`java -jar cfr.jar {input} --outputdir {output}`) | Fernflower/Vineflower and Procyon also built in; needs Java 11+ |
| `.pak` (Quake / Half-Life `PACK`) | built-in extractor | no external tool |
| `.pak` (Unreal Engine 4/5) | `repak unpack {input} --output {output}` | check `repak unpack --help` for your version |
| `.bsa` (Morrowind → Skyrim) | `bsatool extractall {input} {output}` (OpenMW) | BSArch presets for Windows and Wine |

## Use

- **Editor:** File → New / Open / Save / Save As / Close (Ctrl+N, Ctrl+O, Ctrl+S, Ctrl+Shift+S,
  Ctrl+W); Ctrl+F find (Enter / F3 next, Shift+Enter / Shift+F3 previous, Esc closes);
  View → Dark Theme, View → Language. Auto-completion pops up after two characters.
- **Modding Tools:** drop an archive on the dashed area (or anywhere in the window), or use
  *Open archive…*. The progress bar, *Cancel* and the log show the job; when it finishes the
  tree lists the files: double-click one to edit it.

Settings (theme, working directory, tool paths, timeout) are stored with QSettings
(`~/.config/PERDIX/PERDIX Studio.conf` on Linux).

## Architecture

```
MainWindow (QMainWindow) ── menus, shortcuts, status bar (Ln/Col · encoding · language)
├── QTabWidget
│   ├── EditorPanel ──────── QTabWidget of CodeEditor, FindBar
│   │     CodeEditor (QsciScintilla): Language → lexer + QsciAPIs keywords, smart indent,
│   │                                  theme colours, path + encoding
│   └── ModdingPanel ─────── DropZone (drag & drop) · QProgressBar + Cancel
│         │                   QTreeView (QFileSystemModel) · log (QPlainTextEdit)
│         ├─ validate_archive_path → detect_archive (magic bytes) → plan_job → build_command
│         └─ ExtractWorker (QThread)
│              ├─ subprocess.Popen(argv, shell=False) + reader thread → log / cancel / timeout
│              ├─ extract_quake_pak (built-in) · expand_source_jars (Fernflower)
│              └─ safe_join on every archive member (no path traversal)
├── SettingsDialog ── StudioSettings (QSettings wrapper)
└── themes: apply_app_theme (Fusion palette) + style_lexer (per-language colours)
```

## Tests

```sh
pip install pytest
QT_QPA_PLATFORM=offscreen pytest tests
PERDIX_CFR_JAR=/path/to/cfr-0.152.jar QT_QPA_PLATFORM=offscreen pytest tests   # + real CFR run
```
