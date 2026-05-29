"""
Main application window.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QClipboard, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.aliases import AliasResolver
from core.database import BlockRecord, Database
from core.indexer import IndexerWorker
from core.search_engine import SearchEngine
from gui.results_table import ResultsTable
from gui.search_panel import SearchPanel
from gui.styles import apply_dark_theme
from utils.config import save_config

log = logging.getLogger(__name__)


class SearchWorker(QThread):
    """Runs search in a background thread to keep the UI responsive."""
    results_ready = Signal(list)

    def __init__(self, engine: SearchEngine, query: str, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._query = query

    def run(self) -> None:
        results = self._engine.search(self._query)
        self.results_ready.emit(results)


class MainWindow(QMainWindow):
    def __init__(self, config: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._config = config

        # Apply theme
        apply_dark_theme(QApplication.instance())

        # Core objects
        self._db = Database(config["db_path"])
        self._alias_resolver = self._build_alias_resolver()
        self._search_engine = SearchEngine(self._db, self._alias_resolver, config)
        self._indexer: Optional[IndexerWorker] = None
        self._search_worker: Optional[SearchWorker] = None

        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()
        self._refresh_status()

        log.info("Main window ready")

    # ------------------------------------------------------------------
    # Alias resolver
    # ------------------------------------------------------------------

    def _build_alias_resolver(self) -> AliasResolver:
        resolver = AliasResolver()
        # Load bundled aliases.json
        base = Path(self._config.get("_base_dir", "."))
        aliases_file = base / "resources" / "aliases.json"
        if aliases_file.exists():
            resolver.load_from_file(str(aliases_file))
        # Merge DB aliases
        resolver.load_from_db(self._db.get_aliases())
        return resolver

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle("AutoCAD Block Search Tool")
        self.setMinimumSize(1100, 650)
        self.resize(1280, 760)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 8, 12, 8)
        root_layout.setSpacing(8)

        # ── Search Panel ──
        self._search_panel = SearchPanel(self._config, self)
        self._search_panel.search_requested.connect(self._on_search)
        self._search_panel.clear_requested.connect(self._on_clear_results)
        root_layout.addWidget(self._search_panel)

        # ── Results Table ──
        self._results_table = ResultsTable(self)
        self._results_table.open_folder_requested.connect(self._open_folder)
        self._results_table.open_file_requested.connect(self._open_file)
        self._results_table.preview_requested.connect(self._on_preview)
        self._results_table.block_selected.connect(self._on_block_selected)
        self._results_table.copy_path_requested.connect(self._copy_to_clipboard)
        self._results_table.copy_name_requested.connect(self._copy_to_clipboard)
        root_layout.addWidget(self._results_table, stretch=1)

        # ── Status Bar ──
        self._status_bar = self.statusBar()
        self._status_label = QLabel("Ready")
        self._status_bar.addWidget(self._status_label, 1)
        self._stats_label = QLabel("")
        self._status_bar.addPermanentWidget(self._stats_label)

    def _setup_menu(self) -> None:
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("&File")
        index_action = QAction("&Index Manager…", self)
        index_action.setShortcut("Ctrl+I")
        index_action.triggered.connect(self._open_index_manager)
        file_menu.addAction(index_action)

        rescan_action = QAction("&Rescan All Paths", self)
        rescan_action.setShortcut("F5")
        rescan_action.triggered.connect(self._start_indexing)
        file_menu.addAction(rescan_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Help menu
        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_shortcuts(self) -> None:
        # F5 = rescan
        sc_f5 = QShortcut(QKeySequence(Qt.Key.Key_F5), self)
        sc_f5.activated.connect(self._start_indexing)
        # Ctrl+F = focus search
        sc_cf = QShortcut(QKeySequence("Ctrl+F"), self)
        sc_cf.activated.connect(self._search_panel.focus)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _on_search(self, query: str) -> None:
        # Cancel previous search worker if running
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.quit()

        self._set_status(f'Searching for "{query}"…')
        self._search_worker = SearchWorker(self._search_engine, query, self)
        self._search_worker.results_ready.connect(self._on_results_ready)
        self._search_worker.start()

    def _on_results_ready(self, results) -> None:
        self._results_table.set_results(results)
        self._search_panel.set_result_count(len(results))
        query = self._search_panel.current_query()
        if results:
            self._set_status(f'Found {len(results)} block(s) for "{query}"')
        else:
            self._set_status(f'No results found for "{query}"')

    def _on_clear_results(self) -> None:
        self._results_table.clear()
        self._refresh_status()

    # ------------------------------------------------------------------
    # Block actions
    # ------------------------------------------------------------------

    def _on_block_selected(self, rec: BlockRecord) -> None:
        """Track usage frequency."""
        if rec.id:
            try:
                self._db.increment_select_count(rec.id)
            except Exception:
                pass

    def _open_folder(self, folder: str) -> None:
        if folder and os.path.isdir(folder):
            if sys.platform == "win32":
                subprocess.Popen(["explorer", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        else:
            QMessageBox.warning(self, "Folder Not Found", f"Could not open:\n{folder}")

    def _open_file(self, file_path: str) -> None:
        if file_path and os.path.isfile(file_path):
            if sys.platform == "win32":
                os.startfile(file_path)  # type: ignore[attr-defined]
        else:
            QMessageBox.warning(self, "File Not Found", f"Could not open:\n{file_path}")

    def _copy_to_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self._set_status(f"Copied: {text}")

    def _on_preview(self, rec: BlockRecord) -> None:
        from gui.preview_widget import PreviewDialog
        dlg = PreviewDialog(rec, self._config, self)
        dlg.exec()

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def _start_indexing(self) -> None:
        if self._indexer and self._indexer.isRunning():
            self._indexer.cancel()
            return

        self._indexer = IndexerWorker(self._db, self._config, parent=self)
        self._indexer.status.connect(self._set_status)
        self._indexer.finished.connect(self._on_indexing_finished)
        self._indexer.error.connect(lambda e: self._set_status(f"Error: {e}"))
        self._indexer.start()
        self._set_status("Indexing started…")

    def _on_indexing_finished(self) -> None:
        self._refresh_status()
        # Re-execute the current search to pick up new results
        query = self._search_panel.current_query()
        if query:
            self._on_search(query)

    # ------------------------------------------------------------------
    # Index Manager
    # ------------------------------------------------------------------

    def _open_index_manager(self) -> None:
        from gui.index_manager import IndexManagerDialog
        dlg = IndexManagerDialog(self._db, self._config, self)
        if dlg.exec():
            save_config(self._config)
            # Restart alias resolver in case user added aliases
            self._alias_resolver = self._build_alias_resolver()
            self._search_engine = SearchEngine(
                self._db, self._alias_resolver, self._config
            )
            self._refresh_status()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        self._status_label.setText(msg)

    def _refresh_status(self) -> None:
        try:
            fc, bc = self._db.get_file_stats()
            self._stats_label.setText(f"  {fc} files  |  {bc} blocks  ")
            if fc == 0:
                self._set_status(
                    "No files indexed. Open Index Manager (Ctrl+I) to add scan paths."
                )
            else:
                self._set_status("Ready — type a block name to search")
        except Exception:
            self._stats_label.setText("")

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About AutoCAD Block Search Tool",
            "<h3>AutoCAD Block Search Tool</h3>"
            "<p>Searches AutoCAD block libraries using intelligent fuzzy matching, "
            "aliases, and full-text indexing.</p>"
            "<p><b>Stack:</b> Python · PySide6 · ezdxf · RapidFuzz · SQLite</p>",
        )

    def closeEvent(self, event) -> None:
        if self._indexer and self._indexer.isRunning():
            self._indexer.cancel()
            self._indexer.wait(3000)
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.quit()
            self._search_worker.wait(1000)
        self._db.close()
        event.accept()
