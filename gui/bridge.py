"""
Qt ↔ JavaScript bridge.
Exposed to the web UI via QWebChannel as window.backend.

JS usage (after QWebChannel handshake):
    backend.search(query, category, pathFilter, function(jsonStr) { ... });
"""
import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from core.aliases import AliasResolver
from core.database import BlockRecord, Database
from core.indexer import IndexerWorker
from utils.config import save_config

log = logging.getLogger(__name__)


class Backend(QObject):
    # ── Signals emitted to JS ──────────────────────────────────────────
    indexingProgress = Signal(int, int, str)   # current, total, statusMsg
    indexingFinished = Signal(str)             # summary
    statsChanged     = Signal(str)             # JSON stats
    searchDone       = Signal(str)             # JSON results array (unused – we use return value)
    errorOccurred    = Signal(str)

    def __init__(
        self,
        db: Database,
        search_engine,        # SearchEngine – avoid circular import typing
        alias_resolver: AliasResolver,
        config: Dict[str, Any],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._engine = search_engine
        self._aliases = alias_resolver
        self._config = config
        self._indexer: Optional[IndexerWorker] = None

    # ── Search ────────────────────────────────────────────────────────

    @Slot(str, str, str, result=str)
    def search(self, query: str, category: str, path_filter: str) -> str:
        try:
            results = self._engine.search(
                query, category=category, path_filter=path_filter
            )
            return json.dumps([_rec(r) for r in results], ensure_ascii=False)
        except Exception as exc:
            log.exception("search error")
            return json.dumps({"error": str(exc)})

    # ── Config ────────────────────────────────────────────────────────

    @Slot(result=str)
    def getConfig(self) -> str:
        return json.dumps({
            "scan_paths":        self._config.get("scan_paths", []),
            "oda_converter_path": self._config.get("oda_converter_path", ""),
            "fuzzy_threshold":   self._config.get("fuzzy_threshold", 60),
            "scan_extensions":   self._config.get("scan_extensions", [".dwg", ".dwt"]),
        })

    @Slot(str)
    def saveConfig(self, json_str: str) -> None:
        try:
            self._config.update(json.loads(json_str))
            save_config(self._config)
            self.statsChanged.emit(self._stats_json())
        except Exception as exc:
            self.errorOccurred.emit(str(exc))

    @Slot(result=str)
    def getStats(self) -> str:
        return self._stats_json()

    # ── Indexing ──────────────────────────────────────────────────────

    @Slot()
    def startIndexing(self) -> None:
        if self._indexer and self._indexer.isRunning():
            return
        if not self._config.get("scan_paths"):
            self.errorOccurred.emit("No scan paths configured.")
            return

        self._indexer = IndexerWorker(self._db, self._config, parent=self)
        self._indexer.progress.connect(
            lambda c, t: self.indexingProgress.emit(c, t, "")
        )
        self._indexer.status.connect(
            lambda m: self.indexingProgress.emit(-1, -1, m)
        )
        self._indexer.finished.connect(self._on_done)
        self._indexer.error.connect(self.errorOccurred.emit)
        self._indexer.start()

    @Slot()
    def cancelIndexing(self) -> None:
        if self._indexer and self._indexer.isRunning():
            self._indexer.cancel()
            # Emit immediately so JS gets feedback before the thread exits
            self.indexingProgress.emit(-1, -1, "Cancelling\u2026")

    # ── OS actions ────────────────────────────────────────────────────

    @Slot(str)
    def openFolder(self, path: str) -> None:
        if sys.platform != "win32":
            return
        # If it's a file path, open the folder and select the file
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif os.path.isdir(path):
            subprocess.Popen(["explorer", os.path.normpath(path)])
        else:
            # Fallback: try the parent directory
            parent = os.path.dirname(path)
            if os.path.isdir(parent):
                subprocess.Popen(["explorer", os.path.normpath(parent)])

    @Slot(str)
    def openFile(self, path: str) -> None:
        if sys.platform == "win32" and os.path.isfile(path):
            os.startfile(path)  # type: ignore[attr-defined]

    @Slot(str)
    def copyToClipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    @Slot(int)
    def recordSelection(self, block_id: int) -> None:
        try:
            self._db.increment_select_count(block_id)
        except Exception:
            pass

    # ── Dialogs ───────────────────────────────────────────────────────

    @Slot(str, result=str)
    def browseFolder(self, start: str) -> str:
        path = QFileDialog.getExistingDirectory(
            None, "Select Folder", start,
            QFileDialog.Option.ShowDirsOnly,
        )
        return path or ""

    @Slot(result=str)
    def browseODA(self) -> str:
        path, _ = QFileDialog.getOpenFileName(
            None, "Select ODAFileConverter.exe", "",
            "ODA File Converter (ODAFileConverter.exe);;All Files (*)",
        )
        return path or ""

    @Slot()
    def setupODA(self) -> None:
        """Run setup_oda.py in a subprocess to download/install ODA Converter."""
        from pathlib import Path as _Path
        base = _Path(self._config.get("_base_dir", "."))
        setup_script = base / "setup_oda.py"
        python_exe = sys.executable
        if not setup_script.is_file():
            self.errorOccurred.emit("setup_oda.py not found in project root.")
            return
        try:
            subprocess.Popen(
                [python_exe, str(setup_script)],
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            self.indexingProgress.emit(-1, -1, "ODA setup started — check vendor/ folder…")
        except Exception as exc:
            self.errorOccurred.emit(f"Failed to launch ODA setup: {exc}")

    @Slot(result=str)
    def detectODA(self) -> str:
        """
        Scan known locations for ODAFileConverter.exe.
        If found, update config and return the path; otherwise return "".
        """
        import glob as _glob
        from pathlib import Path as _Path
        from utils.config import save_config

        candidates: list[str] = []

        # 1. Current config path
        cfg_path = self._config.get("oda_converter_path", "")
        if cfg_path:
            candidates.append(cfg_path)

        # 2. System install locations (any version subfolder)
        for base in (
            r"C:\Program Files\ODA",
            r"C:\Program Files (x86)\ODA",
            r"C:\ODA",
        ):
            candidates.extend(
                _glob.glob(base + r"\**\ODAFileConverter.exe", recursive=True)
            )

        # 3. Project vendor directory
        try:
            from setup_oda import ODA_EXE
            candidates.append(str(ODA_EXE))
        except Exception:
            pass

        for path in candidates:
            if path and _Path(path).is_file():
                if self._config.get("oda_converter_path") != path:
                    self._config["oda_converter_path"] = path
                    save_config(self._config)
                self.statsChanged.emit(self._stats_json())
                return path

        return ""

    # ── Aliases ───────────────────────────────────────────────────────

    @Slot(result=str)
    def getAliases(self) -> str:
        return json.dumps(self._aliases.get_all_terms(), ensure_ascii=False)

    # ── Internal ──────────────────────────────────────────────────────

    def _on_done(self) -> None:
        self.statsChanged.emit(self._stats_json())
        fc, bc = self._db.get_file_stats()
        self.indexingFinished.emit(
            f"Done — {fc} files, {bc} blocks indexed"
        )

    def _stats_json(self) -> str:
        try:
            fc, bc = self._db.get_file_stats()
        except Exception:
            fc, bc = 0, 0
        return json.dumps({
            "file_count":  fc,
            "block_count": bc,
            "scan_paths":  self._config.get("scan_paths", []),
            "oda_ok":      os.path.isfile(
                self._config.get("oda_converter_path", "")
            ),
        })


def _rec(r: BlockRecord) -> Dict[str, Any]:
    return {
        "id":             r.id,
        "block_name":     r.block_name,
        "description":    r.description,
        "attribute_tags": r.attribute_tags,
        "filename":       r.filename,
        "folder":         r.folder,
        "file_path":      r.file_path,
        "score":          r.score,
        "select_count":   r.select_count,
    }
