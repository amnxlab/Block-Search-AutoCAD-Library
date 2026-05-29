"""
Index Manager Dialog — lets the user manage scan paths, trigger rescans,
view indexing progress, and clear the cache.
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.database import Database
from core.indexer import IndexerWorker

log = logging.getLogger(__name__)


class IndexManagerDialog(QDialog):
    def __init__(
        self,
        db: Database,
        config: Dict[str, Any],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._config = config
        self._indexer: Optional[IndexerWorker] = None

        self.setWindowTitle("Index Manager")
        self.setMinimumSize(700, 480)
        self.resize(750, 500)
        self.setModal(True)

        self._setup_ui()
        self._load_paths()
        self._refresh_stats()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 12)

        # ── Scan Paths Section ──
        paths_group = QGroupBox("Scan Paths")
        paths_layout = QVBoxLayout(paths_group)
        paths_layout.setSpacing(6)

        self._path_list = QListWidget()
        self._path_list.setAcceptDrops(True)
        self._path_list.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self._path_list.setMinimumHeight(160)
        self._path_list.setToolTip("Drag & drop folders here, or use the Add button")
        paths_layout.addWidget(self._path_list)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("+ Add Folder")
        self._btn_add.setObjectName("accentButton")
        self._btn_add.clicked.connect(self._add_path)

        self._btn_remove = QPushButton("Remove Selected")
        self._btn_remove.clicked.connect(self._remove_path)

        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_remove)
        btn_row.addStretch()
        paths_layout.addLayout(btn_row)

        root.addWidget(paths_group)

        # ── Indexing Section ──
        index_group = QGroupBox("Indexing")
        index_layout = QVBoxLayout(index_group)
        index_layout.setSpacing(6)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("")
        index_layout.addWidget(self._progress_bar)

        self._status_label = QLabel("Idle")
        self._status_label.setObjectName("subLabel")
        self._status_label.setWordWrap(True)
        index_layout.addWidget(self._status_label)

        scan_btn_row = QHBoxLayout()
        self._btn_scan = QPushButton("▶  Start Indexing")
        self._btn_scan.setObjectName("accentButton")
        self._btn_scan.setMinimumWidth(140)
        self._btn_scan.clicked.connect(self._toggle_scan)

        self._btn_clear = QPushButton("🗑  Clear Cache")
        self._btn_clear.setObjectName("dangerButton")
        self._btn_clear.setToolTip("Remove all indexed data and rebuild from scratch")
        self._btn_clear.clicked.connect(self._clear_cache)

        self._stats_label = QLabel("")
        self._stats_label.setObjectName("subLabel")

        scan_btn_row.addWidget(self._btn_scan)
        scan_btn_row.addWidget(self._btn_clear)
        scan_btn_row.addStretch()
        scan_btn_row.addWidget(self._stats_label)
        index_layout.addLayout(scan_btn_row)

        root.addWidget(index_group)

        # ── ODA Converter path ──
        oda_group = QGroupBox("ODA File Converter")
        oda_layout = QHBoxLayout(oda_group)
        oda_layout.setSpacing(8)

        self._oda_label = QLabel(self._config.get("oda_converter_path", "Not configured"))
        self._oda_label.setObjectName("subLabel")
        self._oda_label.setWordWrap(True)
        oda_layout.addWidget(self._oda_label, stretch=1)

        btn_oda = QPushButton("Browse…")
        btn_oda.clicked.connect(self._browse_oda)
        oda_layout.addWidget(btn_oda)

        root.addWidget(oda_group)
        root.addStretch()

        # ── Dialog Buttons ──
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _load_paths(self) -> None:
        self._path_list.clear()
        for p in self._config.get("scan_paths", []):
            self._add_path_item(p)

    def _add_path_item(self, path: str) -> None:
        item = QListWidgetItem(path)
        item.setToolTip(path)
        exists = os.path.isdir(path)
        if not exists:
            item.setForeground(Qt.GlobalColor.red)
            item.setToolTip(f"{path}\n⚠ Directory not found")
        self._path_list.addItem(item)

    def _add_path(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Scan Folder", "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder and folder not in self._get_current_paths():
            self._add_path_item(folder)

    def _remove_path(self) -> None:
        for item in self._path_list.selectedItems():
            self._path_list.takeItem(self._path_list.row(item))

    def _get_current_paths(self):
        return [
            self._path_list.item(i).text()
            for i in range(self._path_list.count())
        ]

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _toggle_scan(self) -> None:
        if self._indexer and self._indexer.isRunning():
            self._indexer.cancel()
            self._btn_scan.setText("▶  Start Indexing")
            self._status_label.setText("Cancelling…")
            return

        # Save paths to config before scanning
        paths = self._get_current_paths()
        self._config["scan_paths"] = paths

        if not paths:
            QMessageBox.warning(self, "No Paths", "Add at least one scan folder first.")
            return

        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("0%")
        self._btn_scan.setText("⏹  Stop")
        self._btn_clear.setEnabled(False)

        self._indexer = IndexerWorker(self._db, self._config, parent=self)
        self._indexer.progress.connect(self._on_progress)
        self._indexer.status.connect(self._status_label.setText)
        self._indexer.finished.connect(self._on_scan_finished)
        self._indexer.error.connect(lambda e: self._status_label.setText(f"Error: {e}"))
        self._indexer.start()

    def _on_progress(self, current: int, total: int) -> None:
        if total > 0:
            pct = int(100 * current / total)
            self._progress_bar.setValue(pct)
            self._progress_bar.setFormat(f"{current} / {total}  ({pct}%)")

    def _on_scan_finished(self) -> None:
        self._btn_scan.setText("▶  Start Indexing")
        self._btn_clear.setEnabled(True)
        self._progress_bar.setValue(100)
        self._refresh_stats()

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _clear_cache(self) -> None:
        reply = QMessageBox.question(
            self,
            "Clear Index Cache",
            "This will remove ALL indexed file and block data.\n"
            "You will need to rescan to rebuild the index.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._db.clear_all()
            self._refresh_stats()
            self._status_label.setText("Cache cleared. Click Start Indexing to rebuild.")
            self._progress_bar.setValue(0)
            self._progress_bar.setFormat("")

    # ------------------------------------------------------------------
    # ODA browser
    # ------------------------------------------------------------------

    def _browse_oda(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select ODAFileConverter.exe",
            "",
            "ODA File Converter (ODAFileConverter.exe);;All Files (*)",
        )
        if path:
            self._config["oda_converter_path"] = path
            self._oda_label.setText(path)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def _refresh_stats(self) -> None:
        try:
            fc, bc = self._db.get_file_stats()
            self._stats_label.setText(f"{fc} files · {bc} blocks indexed")
        except Exception:
            self._stats_label.setText("")

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        if self._indexer and self._indexer.isRunning():
            self._indexer.cancel()
            self._indexer.wait(3000)
        self._config["scan_paths"] = self._get_current_paths()
        self.accept()

    def closeEvent(self, event) -> None:
        if self._indexer and self._indexer.isRunning():
            self._indexer.cancel()
            self._indexer.wait(3000)
        event.accept()

    # ------------------------------------------------------------------
    # Drag-and-drop support on the path list
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path) and path not in self._get_current_paths():
                self._add_path_item(path)
