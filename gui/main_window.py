"""
Main application window — QWebEngineView + QWebChannel SPA.
"""
import logging
from pathlib import Path
from typing import Any, Dict

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QCloseEvent, QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from core.aliases import AliasResolver
from core.database import Database
from core.indexer import IndexerWorker
from core.search_engine import SearchEngine
from gui.bridge import Backend

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, config: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._config = config

        # ── Core objects ──────────────────────────────────────────────
        self._db = Database(config["db_path"])
        self._alias_resolver = self._build_alias_resolver()
        self._search_engine = SearchEngine(self._db, self._alias_resolver, config)

        # ── QWebChannel backend ───────────────────────────────────────
        self._backend = Backend(
            db=self._db,
            search_engine=self._search_engine,
            alias_resolver=self._alias_resolver,
            config=self._config,
            parent=self,
        )
        self._backend.errorOccurred.connect(self._on_native_error)

        # ── QWebEngineView ────────────────────────────────────────────
        self._view = QWebEngineView(self)
        scale = float(self._config.get("ui_text_scale", 1.0) or 1.0)
        self._view.setZoomFactor(max(0.8, min(2.0, scale)))
        channel = QWebChannel(self)
        channel.registerObject("backend", self._backend)
        self._view.page().setWebChannel(channel)

        # ── Window chrome ─────────────────────────────────────────────
        self.setWindowTitle("Block Search — AutoCAD Library")
        self.setMinimumSize(1100, 660)
        self.resize(1360, 820)
        self.setCentralWidget(self._view)

        # ── Load HTML ─────────────────────────────────────────────────
        res_dir = Path(self._config.get("_resources_dir", self._config.get("_base_dir", ".")))
        html_path = res_dir / "resources" / "ui" / "index.html"
        self._view.setUrl(QUrl.fromLocalFile(str(html_path)))

        # ── Window icon from SVG ──────────────────────────────────────
        icon_path = res_dir / "resources" / "icon.svg"
        if icon_path.is_file():
            renderer = QSvgRenderer(str(icon_path))
            icon = QIcon()
            for size in (16, 32, 48, 64, 128, 256):
                px = QPixmap(size, size)
                px.fill(Qt.GlobalColor.transparent)
                painter = QPainter(px)
                renderer.render(painter)
                painter.end()
                icon.addPixmap(px)
            self.setWindowIcon(icon)
            QApplication.setWindowIcon(icon)

        log.info("Main window ready — loaded %s", html_path)

    # ------------------------------------------------------------------
    # Alias resolver
    # ------------------------------------------------------------------

    def _build_alias_resolver(self) -> AliasResolver:
        resolver = AliasResolver()
        base = Path(self._config.get("_resources_dir", self._config.get("_base_dir", ".")))
        aliases_file = base / "resources" / "aliases.json"
        if aliases_file.exists():
            resolver.load_from_file(str(aliases_file))
        resolver.load_from_db(self._db.get_aliases())
        return resolver

    # ------------------------------------------------------------------
    # Error handler (fall back to native dialog)
    # ------------------------------------------------------------------

    def _on_native_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Error", msg)

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        # Stop any running indexer
        self._backend.cancelIndexing()
        # Close database
        try:
            self._db.close()
        except Exception:
            pass
        event.accept()
        log.info("Main window closed")
