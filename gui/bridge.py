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
import time
import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from core.aliases import AliasResolver
from core.database import BlockRecord, Database
from core.dwg_parser import parse_dwg
from core.indexer import IndexerWorker
from utils.config import save_config

log = logging.getLogger(__name__)


class PreviewPrecacheWorker(QThread):
    progress = Signal(int, int, str)    # current, total, status
    logLine = Signal(str, str)          # text, css class
    finishedSummary = Signal(str)

    def __init__(
        self,
        db_path: str,
        render_func: Callable[[BlockRecord], Optional[str]],
        cache_path_func: Callable[[BlockRecord], str],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._db_path = db_path
        self._render_func = render_func
        self._cache_path_func = cache_path_func

    def run(self) -> None:
        db = Database(self._db_path)
        try:
            blocks = db.get_all_blocks_for_precache()
            total = len(blocks)
            if total == 0:
                self.finishedSummary.emit("Preview pre-cache skipped: no blocks.")
                return

            self.logLine.emit("Preview pre-cache started...", "log-status")
            rendered = cached = failed = 0
            for idx, rec in enumerate(blocks, start=1):
                cache_path = self._cache_path_func(rec)
                if cache_path and os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
                    cached += 1
                else:
                    out = self._render_func(rec)
                    if out and os.path.isfile(out):
                        rendered += 1
                    else:
                        failed += 1

                if idx % 20 == 0 or idx == total:
                    self.progress.emit(idx, total, f"Pre-caching previews: {idx}/{total}")

            self.finishedSummary.emit(
                f"Preview pre-cache done: {rendered} rendered, {cached} cached, {failed} failed."
            )
        finally:
            db.close()


class Backend(QObject):
    # ── Signals emitted to JS ──────────────────────────────────────────
    indexingProgress = Signal(int, int, str)   # current, total, statusMsg
    indexingLogLine  = Signal(str, str)        # text, css_class
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
        self._poll_timer: Optional[QTimer] = None

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

        self._indexer = IndexerWorker(self._config, parent=self)
        self._indexer.finished.connect(self._on_done)
        self._indexer.error.connect(self.errorOccurred.emit)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_indexer)
        self._poll_timer.start()

        self._indexer.start()

    @Slot()
    def cancelIndexing(self) -> None:
        if self._indexer and self._indexer.isRunning():
            self._indexer.cancel()

    def _poll_indexer(self) -> None:
        """Called every 100 ms from QTimer — runs on main thread."""
        if self._indexer is None:
            return
        state = self._indexer.get_state()
        # Deliver accumulated log lines first
        for text, css in state.get("log_lines", []):
            self.indexingLogLine.emit(text, css)
        # Then emit progress update
        self.indexingProgress.emit(
            state["current"], state["total"], state["status_msg"]
        )

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

    @Slot(int, result=str)
    def getBlockGeometry(self, block_id: int) -> str:
        try:
            rec = self._db.get_block_by_id(block_id)
            if rec is None:
                return json.dumps({"error": "Block not found"})
            if not rec.geometry:
                return json.dumps({
                    "error": "Geometry is not available for this block. Reindex to regenerate geometry.",
                })

            return json.dumps({
                "id": rec.id,
                "block_name": rec.block_name,
                "bounds": rec.bounds,
                "entities": rec.geometry,
                "entity_count": rec.entity_count,
            }, ensure_ascii=False)
        except Exception as exc:
            log.exception("getBlockGeometry error")
            return json.dumps({"error": str(exc)})

    @Slot(int, result=str)
    def getBlockAccuratePreview(self, block_id: int) -> str:
        """Return indexed preview image path without runtime rendering."""
        try:
            rec = self._db.get_block_by_id(block_id)
            if rec is None:
                return json.dumps({"error": "Block not found"})

            preview_path = self._resolve_preview_path(rec.preview_path)
            if preview_path and os.path.isfile(preview_path):
                return json.dumps({
                    "image_path": preview_path,
                    "source": "indexed",
                    "block_name": rec.block_name,
                }, ensure_ascii=False)

            return json.dumps({
                "error": "Indexed preview unavailable",
                "source": "indexed_missing",
                "block_name": rec.block_name,
            })
        except Exception as exc:
            log.exception("getBlockAccuratePreview error")
            return json.dumps({"error": str(exc)})

    def _resolve_preview_path(self, preview_path: str) -> str:
        if not preview_path:
            return ""
        if os.path.isabs(preview_path):
            return preview_path
        base = Path(self._config.get("_base_dir", "."))
        return str((base / preview_path).resolve())

    def _try_autocad_preview(self, rec: BlockRecord) -> Optional[str]:
        if not rec.file_path or not os.path.isfile(rec.file_path):
            return None

        # Fast path: serve already-rendered preview without touching COM.
        cached_png = self._preview_cache_path(rec)
        if cached_png and os.path.isfile(cached_png) and os.path.getsize(cached_png) > 0:
            return cached_png

        coinit_done = False
        try:
            import pythoncom  # type: ignore[import]
            pythoncom.CoInitialize()
            coinit_done = True
        except Exception:
            coinit_done = False

        try:
            import win32com.client as win32  # type: ignore[import]
        except ImportError:
            if coinit_done:
                try:
                    pythoncom.CoUninitialize()  # type: ignore[name-defined]
                except Exception:
                    pass
            return None

        if not cached_png:
            return None

        tmp_png = cached_png + ".tmp"
        doc = None
        try:
            acad = win32.Dispatch("AutoCAD.Application")
            acad.Visible = False

            doc = acad.Documents.Open(rec.file_path, True)
            try:
                doc.Blocks.Item(rec.block_name)
            except Exception:
                return None

            block_dwg = cached_png + ".block.dwg"
            if not self._autocad_wblock_export(doc, rec.block_name, block_dwg):
                # Fallback to legacy in-place insert/export if WBLOCK fails.
                import win32com.client
                origin = win32com.client.VARIANT(
                    win32com.client.pythoncom.VT_ARRAY | win32com.client.pythoncom.VT_R8,
                    [0.0, 0.0, 0.0],
                )
                ref = doc.ModelSpace.InsertBlock(origin, rec.block_name, 1, 1, 1, 0)
                self._autocad_export_active_doc_png(acad, tmp_png)
                try:
                    ref.Delete()
                except Exception:
                    pass
            else:
                block_doc = None
                try:
                    block_doc = acad.Documents.Open(block_dwg, True)
                    self._autocad_export_active_doc_png(acad, tmp_png)
                finally:
                    try:
                        if block_doc is not None:
                            block_doc.Close(False)
                    except Exception:
                        pass
                    try:
                        if os.path.isfile(block_dwg):
                            os.remove(block_dwg)
                    except Exception:
                        pass

            if os.path.isfile(tmp_png) and os.path.getsize(tmp_png) > 0:
                os.replace(tmp_png, cached_png)
                return cached_png
            return None
        except Exception as exc:
            log.debug("AutoCAD viewport preview failed: %s", exc)
            return None
        finally:
            try:
                if os.path.isfile(tmp_png):
                    os.remove(tmp_png)
            except Exception:
                pass
            try:
                if doc is not None:
                    doc.Close(False)
            except Exception:
                pass
            if coinit_done:
                try:
                    pythoncom.CoUninitialize()  # type: ignore[name-defined]
                except Exception:
                    pass

    def _autocad_wblock_export(self, source_doc: Any, block_name: str, output_dwg: str) -> bool:
        try:
            if os.path.isfile(output_dwg):
                os.remove(output_dwg)
        except Exception:
            pass

        try:
            safe_name = block_name.replace('"', "")
            cmd = (
                f'_.-WBLOCK\n"{output_dwg}"\nB\n"{safe_name}"\n'
            )
            source_doc.SendCommand(cmd)

            for _ in range(60):
                if os.path.isfile(output_dwg) and os.path.getsize(output_dwg) > 0:
                    return True
                time.sleep(0.2)
            return False
        except Exception:
            return False

    def _autocad_export_active_doc_png(self, acad: Any, out_png: str) -> None:
        acad.ActiveDocument.SendCommand("_.ZOOM\nE\n")
        acad.ActiveDocument.SendCommand(f'_.-EXPORT\nP\n"{out_png}"\n')
        for _ in range(60):
            if os.path.isfile(out_png) and os.path.getsize(out_png) > 0:
                break
            time.sleep(0.2)

    def _preview_cache_path(self, rec: BlockRecord) -> str:
        try:
            base_dir = Path(self._config.get("_base_dir", "."))
            cache_dir = base_dir / "temp" / "oda_work" / "preview_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)

            st = os.stat(rec.file_path)
            key_src = "|".join([
                os.path.normcase(os.path.normpath(rec.file_path)),
                rec.block_name.lower(),
                str(int(st.st_mtime)),
                str(st.st_size),
            ])
            key = hashlib.sha1(key_src.encode("utf-8", errors="ignore")).hexdigest()
            return str(cache_dir / f"{key}.png")
        except Exception:
            return ""

    def _backfill_block_geometry(self, rec: BlockRecord) -> None:
        if not rec.file_path or not os.path.isfile(rec.file_path):
            return

        oda_exe = self._config.get("oda_converter_path", "")
        parsed = parse_dwg(
            rec.file_path,
            oda_exe=oda_exe if oda_exe else None,
            skip_anonymous=True,
            temp_dir=None,
        )
        if parsed.error and not parsed.blocks:
            return

        for block in parsed.blocks:
            if block.name.lower() == rec.block_name.lower():
                self._db.update_block_geometry(
                    rec.id or 0,
                    block.entities,
                    block.bounds,
                )
                return

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
        # Stop the poller
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer.deleteLater()
            self._poll_timer = None
        # Final flush of any remaining log lines
        if self._indexer is not None:
            state = self._indexer.get_state()
            for text, css in state.get("log_lines", []):
                self.indexingLogLine.emit(text, css)
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
        "entity_count":   r.entity_count,
        "bounds":         r.bounds,
    }
