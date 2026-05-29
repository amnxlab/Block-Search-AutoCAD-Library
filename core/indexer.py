"""
Background indexer — QThread that walks file system paths, parses DWG/DXF
files, and stores extracted block metadata into the SQLite database.

Signals
-------
progress(current, total)   emitted after each file is processed
status(message)            human-readable status string
finished()                 emitted when the scan completes normally
error(message)             emitted on a fatal error
file_indexed(path)         emitted each time a file is successfully indexed
"""
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from core.database import BlockRecord, Database, FileRecord
from core.dwg_parser import ParseResult, parse_dwg

log = logging.getLogger(__name__)


class IndexerWorker(QThread):
    # Signals
    progress = Signal(int, int)        # (current, total)
    status = Signal(str)               # human-readable message
    finished = Signal()
    error = Signal(str)
    file_indexed = Signal(str)         # file path

    def __init__(
        self,
        db: Database,
        config: Dict[str, Any],
        paths: Optional[List[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._config = config
        self._paths = paths  # None = use config["scan_paths"]
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    # ------------------------------------------------------------------
    # QThread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            self._run_scan()
        except Exception as exc:
            log.exception("Indexer fatal error")
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def _run_scan(self) -> None:
        scan_paths: List[str] = self._paths or self._config.get("scan_paths", [])
        extensions: List[str] = [
            e.lower() for e in self._config.get("scan_extensions", [".dwg", ".dwt"])
        ]
        oda_exe: str = self._config.get("oda_converter_path", "")
        skip_anon: bool = self._config.get("skip_anonymous_blocks", True)

        # Use a project-local temp folder for ODA conversions — never touches
        # the original scan paths or system temp.
        _base = Path(self._config.get("_base_dir", "."))
        _oda_tmp = _base / "temp" / "oda_work"
        _oda_tmp.mkdir(parents=True, exist_ok=True)
        temp_dir: str = str(_oda_tmp)

        if not scan_paths:
            self.status.emit("No scan paths configured.")
            return

        # --- Step 1: Collect all files ---
        self.status.emit("Collecting files…")
        all_files: List[str] = []
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
            self.status.emit("No DWG/DWT files found in configured paths.")
            return

        self.status.emit(f"Found {total} file(s). Starting indexing…")
        self.progress.emit(0, total)

        # --- Pre-flight: warn immediately if ODA is missing for DWG files ---
        dwg_exts = {e for e in extensions if e in (".dwg", ".dwt")}
        has_dwg_files = any(Path(f).suffix.lower() in dwg_exts for f in all_files)
        oda_available = bool(oda_exe and os.path.isfile(oda_exe))
        if has_dwg_files and not oda_available:
            self.status.emit(
                f"⚠ ODA Converter not found — {total} DWG file(s) will be skipped. "
                "Configure the path in Settings."
            )

        # --- Step 2: Process each file ---
        indexed = 0
        skipped = 0
        errors = 0
        oda_skipped = 0

        # Get existing DB paths for change detection (mtime + size)
        existing: Dict[str, FileRecord] = {}
        for fp in self._db.get_all_file_paths():
            rec = self._db.get_file_record(fp)
            if rec:
                existing[fp] = rec

        for idx, file_path in enumerate(all_files, start=1):
            if self._cancelled:
                self.status.emit("Indexing cancelled.")
                self.progress.emit(idx, total)
                return

            self.progress.emit(idx, total)
            self.status.emit(f"[{idx}/{total}] {os.path.basename(file_path)}")

            try:
                stat = os.stat(file_path)
                mtime = stat.st_mtime
                fsize = stat.st_size
            except OSError:
                errors += 1
                continue

            # Skip if unchanged
            if file_path in existing:
                cached = existing[file_path]
                if abs(cached.mtime - mtime) < 1.0 and cached.file_size == fsize:
                    skipped += 1
                    continue

            # Parse the file
            result: ParseResult = parse_dwg(
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
                    log.warning("Parse error %s: %s", file_path, result.error)
                    errors += 1
                continue

            # Upsert file record
            file_rec = FileRecord(
                path=file_path,
                filename=os.path.basename(file_path),
                folder=os.path.dirname(file_path),
                mtime=mtime,
                file_size=fsize,
                scan_date=time.time(),
                block_count=len(result.blocks),
            )
            file_id = self._db.upsert_file(file_rec)

            # Replace blocks for this file
            self._db.delete_blocks_for_file(file_id)

            block_records = [
                BlockRecord(
                    file_id=file_id,
                    block_name=b.name,
                    description=b.description,
                    attribute_tags=b.attribute_tags,
                )
                for b in result.blocks
            ]
            self._db.insert_blocks_batch(block_records)

            indexed += 1
            self.file_indexed.emit(file_path)

        # Remove DB entries for files that no longer exist on disk
        self._purge_missing(all_files)

        fc, bc = self._db.get_file_stats()
        parts = [
            f"Done. Indexed {indexed} new/changed",
            f"{skipped} unchanged",
        ]
        if oda_skipped:
            parts.append(
                f"{oda_skipped} DWG file(s) skipped — ODA Converter not configured"
            )
        if errors:
            parts.append(f"{errors} error(s)")
        parts.append(f"DB: {fc} files, {bc} blocks")
        self.status.emit(". ".join(parts) + ".")
        if oda_skipped and not oda_available:
            self.error.emit(
                f"{oda_skipped} DWG file(s) could not be indexed because ODA File Converter "
                "is not installed. Click 'Download ODA' in the sidebar to set it up."
            )

    def _purge_missing(self, found_files: List[str]) -> None:
        """Remove DB records for files that are no longer on disk."""
        found_set = set(found_files)
        for db_path in self._db.get_all_file_paths():
            if db_path not in found_set and not os.path.isfile(db_path):
                log.info("Removing stale DB entry: %s", db_path)
                self._db.delete_file(db_path)
