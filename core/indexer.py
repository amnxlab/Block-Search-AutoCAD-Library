"""
Background indexer -- QThread that walks file system paths, parses DWG/DXF
files, and stores extracted block metadata into the SQLite database.

Design
------
Progress is NOT communicated via cross-thread Qt signals (which flood the
main-thread event queue and starve QWebChannel).  Instead the worker writes
into a thread-safe state dict that the main thread polls via a QTimer.

Signals (fired at most once each)
----------------------------------
finished()   -- scan completed normally
error(msg)   -- fatal error string
"""
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from core.database import BlockRecord, Database, FileRecord
from core.dwg_parser import ParseResult, parse_dwg
from core.preview_exporter import build_preview_relative_path, render_preview_from_geometry

log = logging.getLogger(__name__)

# Increment this when parser/render extraction behavior changes and files must be re-parsed.
INDEXER_PARSER_REV = 3


class IndexerWorker(QThread):
    # Only one-shot signals cross the thread boundary
    finished = Signal()
    error    = Signal(str)

    def __init__(self, config, paths=None, parent=None):
        super().__init__(parent)
        self._config    = config
        self._paths     = paths
        self._cancelled = False
        self._lock  = threading.Lock()
        self._state = {
            "current":    0,
            "total":      0,
            "basename":   "",
            "status_msg": "Starting...",
            "log_lines":  [],
            "done":       False,
            "summary":    "",
        }

    def get_state(self):
        """Drain and return state snapshot. Called from main thread via QTimer."""
        with self._lock:
            snap = dict(self._state)
            snap["log_lines"] = list(self._state["log_lines"])
            self._state["log_lines"] = []
            return snap

    def cancel(self):
        self._cancelled = True

    def _upd(self, **kwargs):
        with self._lock:
            self._state.update(kwargs)

    def _log(self, text, css="log-status"):
        with self._lock:
            self._state["log_lines"].append((text, css))

    def run(self):
        try:
            db = Database(self._config["db_path"])
            try:
                self._run_scan(db)
            finally:
                db.close()
        except Exception as exc:
            log.exception("Indexer fatal error")
            self._upd(status_msg=f"Fatal error: {exc}", done=True)
            self.error.emit(str(exc))
        finally:
            self._upd(done=True)
            self.finished.emit()

    def _run_scan(self, db):
        scan_paths = self._paths or self._config.get("scan_paths", [])
        extensions = [e.lower() for e in self._config.get("scan_extensions", [".dwg", ".dwt"])]
        oda_exe    = self._config.get("oda_converter_path", "")
        skip_anon  = self._config.get("skip_anonymous_blocks", True)

        _base    = Path(self._config.get("_base_dir", "."))
        _oda_tmp = _base / "temp" / "oda_work"
        _oda_tmp.mkdir(parents=True, exist_ok=True)
        temp_dir = str(_oda_tmp)

        if not scan_paths:
            msg = "No scan paths configured."
            log.warning(msg)
            self._upd(status_msg=msg)
            self._log(msg)
            return

        self._upd(status_msg="Collecting files...")
        self._log("Collecting files...")
        log.info("Collecting files...")

        all_files = []
        for root_path in scan_paths:
            if not os.path.isdir(root_path):
                log.warning("Scan path not found: %s", root_path)
                continue
            for dirpath, _dirs, filenames in os.walk(root_path):
                for fname in filenames:
                    if Path(fname).suffix.lower() in extensions:
                        all_files.append(os.path.join(dirpath, fname))

        total = len(all_files)
        if total == 0:
            msg = "No DWG/DWT files found in configured paths."
            log.warning(msg)
            self._upd(status_msg=msg)
            self._log(msg)
            return

        msg = f"Found {total} file(s). Starting indexing..."
        log.info(msg)
        self._upd(current=0, total=total, status_msg=msg)
        self._log(msg)

        dwg_exts      = {e for e in extensions if e in (".dwg", ".dwt")}
        has_dwg_files = any(Path(f).suffix.lower() in dwg_exts for f in all_files)
        oda_available = bool(oda_exe and os.path.isfile(oda_exe))
        if has_dwg_files and not oda_available:
            warn = (f"ODA Converter not found -- {total} DWG file(s) will be skipped. "
                    "Configure the path in Settings.")
            log.warning(warn)
            self._log(warn)

        indexed = skipped = errors = oda_skipped = parser_rev_reindexed = 0
        preview_rendered = preview_failed = 0
        export_previews = bool(self._config.get("preview_export_on_index", True))
        preview_size = int(self._config.get("preview_image_size", 700) or 700)

        existing = {}
        for fp in db.get_all_file_paths():
            rec = db.get_file_record(fp)
            if rec:
                existing[fp] = rec

        for idx, file_path in enumerate(all_files, start=1):
            if self._cancelled:
                log.info("Indexing cancelled by user.")
                self._upd(current=idx, status_msg="Indexing cancelled.")
                self._log("Indexing cancelled.", "log-status")
                return

            try:
                stat  = os.stat(file_path)
                mtime = stat.st_mtime
                fsize = stat.st_size
            except OSError:
                errors += 1
                self._upd(current=idx)
                continue

            if file_path in existing:
                cached = existing[file_path]
                if (
                    abs(cached.mtime - mtime) < 1.0
                    and cached.file_size == fsize
                    and cached.parser_rev == INDEXER_PARSER_REV
                ):
                    skipped += 1
                    self._upd(current=idx, status_msg=f"Indexing files: {idx} / {total}")
                    continue
                if (
                    abs(cached.mtime - mtime) < 1.0
                    and cached.file_size == fsize
                    and cached.parser_rev != INDEXER_PARSER_REV
                ):
                    parser_rev_reindexed += 1

            basename = os.path.basename(file_path)
            log.info("[%d/%d] %s", idx, total, basename)
            self._upd(current=idx, basename=basename,
                      status_msg=f"Indexing files: {idx} / {total}")
            self._log(f"[{idx}/{total}] {basename}", "log-file")

            result = parse_dwg(
                file_path,
                oda_exe=oda_exe if oda_exe else None,
                skip_anonymous=skip_anon,
                temp_dir=temp_dir,
            )

            # One retry for transient ODA/IO issues before declaring failure.
            if result.error and not result.blocks:
                retryable = result.source in ("failed", "oda_converted")
                if retryable:
                    result = parse_dwg(
                        file_path,
                        oda_exe=oda_exe if oda_exe else None,
                        skip_anonymous=skip_anon,
                        temp_dir=temp_dir,
                    )

            if result.error and not result.blocks:
                if result.source == "oda_missing":
                    log.debug("ODA not available, skipping: %s", file_path)
                    oda_skipped += 1
                else:
                    log.error("Parse error -- %s: %s", basename, result.error)
                    self._log(f"Error: {basename}", "log-status")
                    errors += 1
                continue

            # Never replace previously indexed content with an empty parse result.
            if file_path in existing and existing[file_path].block_count > 0 and len(result.blocks) == 0:
                warn = f"Skipped overwrite for {basename}: parser returned 0 blocks (kept previous index)."
                log.warning(warn)
                self._log(warn, "log-status")
                errors += 1
                continue

            file_rec = FileRecord(
                path=file_path,
                filename=basename,
                folder=os.path.dirname(file_path),
                mtime=mtime,
                file_size=fsize,
                parser_rev=INDEXER_PARSER_REV,
                scan_date=time.time(),
                block_count=len(result.blocks),
            )
            file_id = db.upsert_file(file_rec)
            db.delete_blocks_for_file(file_id)
            block_rows = [
                BlockRecord(
                    file_id=file_id,
                    block_name=b.name,
                    description=b.description,
                    attribute_tags=b.attribute_tags,
                    geometry=b.entities,
                    bounds=b.bounds,
                    entity_count=len(b.entities),
                )
                for b in result.blocks
            ]
            inserted_ids = db.insert_blocks_batch(block_rows)

            if export_previews and inserted_ids:
                base_dir = Path(self._config.get("_base_dir", "."))
                for block_id, block_row in zip(inserted_ids, block_rows):
                    rel_path = build_preview_relative_path(
                        file_path=file_path,
                        block_name=block_row.block_name,
                        file_mtime=mtime,
                        file_size=fsize,
                        block_id=block_id,
                    )
                    out_path = base_dir / rel_path
                    ok = render_preview_from_geometry(
                        entities=block_row.geometry,
                        bounds=block_row.bounds,
                        output_path=str(out_path),
                        image_size=preview_size,
                    )
                    if ok:
                        db.update_block_preview_path(block_id, rel_path)
                        preview_rendered += 1
                    else:
                        db.update_block_preview_path(block_id, "__FAILED__")
                        preview_failed += 1

            indexed += 1

        preview_backfilled = 0
        if export_previews and not self._cancelled:
            backfilled, failed = self._backfill_missing_previews(db, preview_size)
            preview_backfilled += backfilled
            preview_rendered += backfilled
            preview_failed += failed
            if backfilled or failed:
                self._log(
                    f"Preview backfill: {backfilled} rendered, {failed} failed.",
                    "log-status",
                )

        self._purge_missing(db, all_files)

        fc, bc = db.get_file_stats()
        parts = [f"Done. Indexed {indexed} new/changed", f"{skipped} unchanged"]
        if parser_rev_reindexed:
            parts.append(f"{parser_rev_reindexed} reindexed (parser upgrade)")
        if oda_skipped:
            parts.append(f"{oda_skipped} DWG skipped (no ODA)")
        if errors:
            parts.append(f"{errors} error(s)")
        if export_previews:
            parts.append(f"{preview_rendered} previews exported")
            if preview_backfilled:
                parts.append(f"{preview_backfilled} from unchanged files")
            if preview_failed:
                parts.append(f"{preview_failed} preview(s) failed")
        parts.append(f"DB: {fc} files, {bc} blocks")
        summary = ". ".join(parts) + "."
        log.info(summary)
        self._upd(current=total, total=total, status_msg=summary, summary=summary)
        self._log(summary, "log-done")

        if oda_skipped and not oda_available:
            err_msg = (
                f"{oda_skipped} DWG file(s) could not be indexed because ODA File "
                "Converter is not installed. Click Download ODA in the sidebar."
            )
            log.error(err_msg)
            self.error.emit(err_msg)

    def _purge_missing(self, db, found_files):
        found_set = set(found_files)
        for fp in db.get_all_file_paths():
            if fp not in found_set and not os.path.isfile(fp):
                log.info("Removing stale DB entry: %s", fp)
                db.delete_file(fp)

    def _backfill_missing_previews(self, db: Database, preview_size: int) -> tuple[int, int]:
        base_dir = Path(self._config.get("_base_dir", "."))
        rendered = 0
        failed = 0
        stat_cache: Dict[str, os.stat_result] = {}

        blocks = db.get_blocks_missing_preview_path()
        total = len(blocks)
        if total == 0:
            return 0, 0

        self._log("Backfilling previews for unchanged indexed blocks...", "log-status")
        for idx, rec in enumerate(blocks, start=1):
            if self._cancelled:
                break

            if not rec.file_path or not os.path.isfile(rec.file_path) or not rec.geometry:
                failed += 1
                continue

            st = stat_cache.get(rec.file_path)
            if st is None:
                try:
                    st = os.stat(rec.file_path)
                    stat_cache[rec.file_path] = st
                except OSError:
                    failed += 1
                    continue

            rel_path = build_preview_relative_path(
                file_path=rec.file_path,
                block_name=rec.block_name,
                file_mtime=st.st_mtime,
                file_size=st.st_size,
                block_id=int(rec.id or 0),
            )
            out_path = base_dir / rel_path

            ok = render_preview_from_geometry(
                entities=rec.geometry,
                bounds=rec.bounds,
                output_path=str(out_path),
                image_size=preview_size,
            )
            if ok:
                db.update_block_preview_path(int(rec.id or 0), rel_path)
                rendered += 1
            else:
                db.update_block_preview_path(int(rec.id or 0), "__FAILED__")
                failed += 1

            if idx % 10 == 0:
                self._upd(status_msg=f"Backfilling previews: {idx} / {total} (ok: {rendered}, failed: {failed})")
            if idx % 50 == 0:
                self._log(f"Backfill progress: {idx}/{total} (ok: {rendered}, failed: {failed})", "log-status")

        return rendered, failed
