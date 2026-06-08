# Block Search — AutoCAD Library

A desktop search tool for AutoCAD block libraries. Index thousands of DWG/DXF files and search through every block record instantly with full-text search.

![Block Search UI](resources/ui/preview.png)

---

## Features

- **Full-text search** across all indexed block names using SQLite FTS5
- **Fuzzy matching** via RapidFuzz for typo-tolerant results
- **DWG + DXF support** — DXF files parsed directly with ezdxf; DWG files converted via ODA File Converter
- **Fast incremental indexing** — background QThread scanner with progress feedback
- **Persistent preview export during indexing** — previews are generated once and stored under `data/previews/`
- **Zero runtime AutoCAD dependency for preview** — result selection reads indexed preview files (no on-demand COM rendering)
- **Vector fallback preview** — if PNG preview is unavailable, geometry is rendered directly in the viewport
- **Viewport zoom/pan** — mouse wheel zoom + drag pan for both vector and raster previews
- **Dark UI** — PySide6 QWebEngineView SPA (VS Code-inspired theme)
- **Resizable table columns** — drag column headers to resize
- **Open in Explorer** — click 📁 to open the file's folder with the file selected
- **Copy path** — click 📋 to copy the full file path to clipboard
- **Context menu** — right-click any result for quick actions
- **Configurable** — set scan paths, fuzzy threshold, max results, and more via the Settings panel
- **ODA auto-detect** — automatically finds ODA File Converter in Program Files on startup

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.10+ | Tested on 3.14 |
| PySide6 | ≥ 6.6.0 | Qt 6 UI + WebEngine |
| ezdxf | ≥ 1.3.0 | DXF parsing |
| RapidFuzz | ≥ 3.6.0 | Fuzzy search |
| pywin32 | ≥ 306 | Windows shell integration |
| ODA File Converter | 27.x | Optional — required for DWG files only |

> **ODA File Converter** is a free tool from the Open Design Alliance.  
> Download: https://www.opendesign.com/guestfiles/oda_file_converter

---

## Installation

### 1. Clone the repo

```bash
git clone https://github.com/amnxlab/Block-Search-AutoCAD-Library.git
cd Block-Search-AutoCAD-Library
```

### 2. Create a virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure

Copy the template config and edit it:

```bash
copy config.template.json config.json
```

Edit `config.json`:

```json
{
  "scan_paths": ["C:/your/autocad/library"],
  "oda_converter_path": "C:/Program Files/ODA/ODAFileConverter 27.1.0/ODAFileConverter.exe"
}
```

All other fields have sensible defaults.

### 5. Run

```bash
python main.py
```

On first launch, the app will:
1. Auto-detect ODA File Converter if installed
2. Create the SQLite database under `data/`
3. Prompt you to add scan paths if none are configured

> If you upgraded from an older version, run indexing once to backfill missing previews for already indexed blocks.

---

## Preview Architecture (Important)

- Previews are generated at indexing time and persisted on disk (`data/previews/`).
- Runtime preview requests do not call AutoCAD.
- If a preview image is missing, the app falls back to vector rendering from stored geometry.
- Blocks that fail preview generation are marked and skipped on later indexing runs to keep reindexing fast.

---

## Project Structure

```
Block-Search-AutoCAD-Library/
├── core/
│   ├── aliases.py          # Block name aliases / synonyms
│   ├── database.py         # SQLite FTS5 schema + queries
│   ├── dwg_parser.py       # DWG/DXF → block records (ezdxf + ODA)
│   ├── indexer.py          # Background file scanner (QThread)
│   ├── preview_exporter.py # Index-time geometry → PNG preview renderer
│   └── search_engine.py    # FTS5 + fuzzy search
├── gui/
│   ├── bridge.py           # QWebChannel backend (JS ↔ Python)
│   ├── main_window.py      # QWebEngineView host window
│   └── ...
├── resources/
│   ├── ui/
│   │   └── index.html      # Single-page app (HTML + CSS + JS)
│   └── icon.svg            # App icon (AutoCAD Streamline)
├── config.template.json    # Config template (copy to config.json)
├── main.py                 # Entry point
├── setup_oda.py            # ODA download helper
├── make_ico.py             # SVG → ICO converter (build tool)
├── build.cmd               # Build script
├── build.spec              # PyInstaller spec
└── requirements.txt
```

---

## Building a Standalone EXE

Requires PyInstaller (included in `requirements.txt`).

Use the project build command script:

```powershell
build.cmd
```

The `make_ico.py` script runs automatically during the build to generate `resources/icon.ico` from `resources/icon.svg`.

---

## ODA File Converter

DWG support requires ODA File Converter installed separately (free):

1. Download from https://www.opendesign.com/guestfiles/oda_file_converter
2. Install it — the app will auto-detect it in `C:\Program Files\ODA\`
3. Or set the path manually in **Settings → ODA Converter Path** within the app

Without ODA, DXF files are still indexed and searchable.

---

## Configuration Reference

| Key | Default | Description |
|---|---|---|
| `scan_paths` | `[]` | Folders to scan for DWG/DXF files |
| `scan_extensions` | `[".dwg", ".dwt"]` | File extensions to index |
| `oda_converter_path` | `""` | Full path to `ODAFileConverter.exe` |
| `db_path` | `""` | SQLite database path (auto-set if empty) |
| `skip_anonymous_blocks` | `true` | Skip `*Model_Space`, `*D0`, etc. |
| `fuzzy_threshold` | `60` | Minimum fuzzy match score (0–100) |
| `max_results` | `200` | Maximum search results to display |
| `debounce_ms` | `300` | Search input debounce delay |
| `preview_export_on_index` | `true` | Export and persist previews during indexing |
| `preview_image_size` | `700` | PNG size in pixels for indexed previews |
| `ui_text_scale` | `1.0` | Global UI text scale multiplier (`0.8` to `2.0`) |
| `theme` | `"dark"` | UI theme (`"dark"` only currently) |

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Contributing

Pull requests welcome. Please open an issue first for major changes.
