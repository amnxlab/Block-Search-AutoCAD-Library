"""
Results table — QTableView backed by a custom QAbstractTableModel.
Displays block search results with sortable columns and right-click actions.
"""
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QMenu,
    QTableView,
    QWidget,
)

from core.database import BlockRecord
from gui.styles import (
    SCORE_HIGH,
    SCORE_LOW,
    SCORE_MED,
    TEXT_SECONDARY,
)

# Columns
COL_BLOCK_NAME  = 0
COL_FILE_NAME   = 1
COL_FOLDER      = 2
COL_FULL_PATH   = 3
COL_SCORE       = 4
_COLUMNS = ["Block Name", "File Name", "Folder", "Full Path", "Score"]


class BlockTableModel(QAbstractTableModel):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._data: List[BlockRecord] = []

    def set_results(self, results: List[BlockRecord]) -> None:
        self.beginResetModel()
        self._data = results
        self.endResetModel()

    def record_at(self, row: int) -> Optional[BlockRecord]:
        if 0 <= row < len(self._data):
            return self._data[row]
        return None

    def clear(self) -> None:
        self.set_results([])

    # QAbstractTableModel interface
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._data)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return _COLUMNS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole and orientation == Qt.Orientation.Horizontal:
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._data):
            return None
        rec = self._data[row]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._cell_text(rec, col)

        if role == Qt.ItemDataRole.ForegroundRole:
            if col == COL_SCORE:
                return _score_color(rec.score)
            if col == COL_FULL_PATH:
                return QColor(TEXT_SECONDARY)

        if role == Qt.ItemDataRole.FontRole:
            if col == COL_BLOCK_NAME:
                font = QFont()
                font.setBold(True)
                return font
            if col == COL_SCORE:
                font = QFont("Consolas", 8)
                font.setBold(True)
                return font

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == COL_SCORE:
                return Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        if role == Qt.ItemDataRole.UserRole:
            return rec  # carry the full record for context menu

        return None

    @staticmethod
    def _cell_text(rec: BlockRecord, col: int) -> str:
        if col == COL_BLOCK_NAME:
            return rec.block_name
        if col == COL_FILE_NAME:
            return rec.filename
        if col == COL_FOLDER:
            return os.path.basename(rec.folder) if rec.folder else ""
        if col == COL_FULL_PATH:
            return rec.file_path
        if col == COL_SCORE:
            return f"{rec.score:.0f}"
        return ""


def _score_color(score: float) -> QColor:
    if score >= 80:
        return QColor(SCORE_HIGH)
    if score >= 50:
        return QColor(SCORE_MED)
    return QColor(SCORE_LOW)


# ---------------------------------------------------------------------------
# Table View widget
# ---------------------------------------------------------------------------

class ResultsTable(QTableView):
    # Emitted when user wants to open the folder of a selected block
    open_folder_requested = Signal(str)          # folder path
    open_file_requested   = Signal(str)          # file path
    preview_requested     = Signal(object)       # BlockRecord
    block_selected        = Signal(object)       # BlockRecord (for history tracking)
    copy_path_requested   = Signal(str)
    copy_name_requested   = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._model = BlockTableModel(self)
        self.setModel(self._model)
        self._setup_ui()
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(28)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(COL_BLOCK_NAME, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_FILE_NAME,  QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_FOLDER,     QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_FULL_PATH,  QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_SCORE,      QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(COL_SCORE, 55)
        hdr.setStretchLastSection(False)

    def _setup_shortcuts(self) -> None:
        # Enter → open folder
        enter_sc = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        enter_sc.activated.connect(self._on_enter)
        # Ctrl+C → copy path
        copy_sc = QShortcut(QKeySequence.StandardKey.Copy, self)
        copy_sc.activated.connect(self._on_copy_path)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_results(self, results: List[BlockRecord]) -> None:
        self._model.set_results(results)
        if results:
            self.selectRow(0)

    def clear(self) -> None:
        self._model.clear()

    def selected_record(self) -> Optional[BlockRecord]:
        indexes = self.selectedIndexes()
        if not indexes:
            return None
        return self._model.record_at(indexes[0].row())

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_enter(self) -> None:
        rec = self.selected_record()
        if rec:
            self.block_selected.emit(rec)
            self.open_folder_requested.emit(rec.folder)

    def _on_copy_path(self) -> None:
        rec = self.selected_record()
        if rec:
            self.copy_path_requested.emit(rec.file_path)

    def _show_context_menu(self, pos) -> None:
        rec = self.selected_record()
        if not rec:
            return

        menu = QMenu(self)
        menu.addAction("Open DWG File",              lambda: self._emit_open_file(rec))
        menu.addAction("Open Containing Folder",     lambda: self._emit_open_folder(rec))
        menu.addSeparator()
        menu.addAction("Preview Block",              lambda: self._emit_preview(rec))
        menu.addSeparator()
        menu.addAction("Copy Full Path",             lambda: self._emit_copy_path(rec))
        menu.addAction("Copy Block Name",            lambda: self._emit_copy_name(rec))
        menu.exec(self.viewport().mapToGlobal(pos))

    def _emit_open_file(self, rec: BlockRecord) -> None:
        self.block_selected.emit(rec)
        self.open_file_requested.emit(rec.file_path)

    def _emit_open_folder(self, rec: BlockRecord) -> None:
        self.block_selected.emit(rec)
        self.open_folder_requested.emit(rec.folder)

    def _emit_preview(self, rec: BlockRecord) -> None:
        self.block_selected.emit(rec)
        self.preview_requested.emit(rec)

    def _emit_copy_path(self, rec: BlockRecord) -> None:
        self.copy_path_requested.emit(rec.file_path)

    def _emit_copy_name(self, rec: BlockRecord) -> None:
        self.copy_name_requested.emit(rec.block_name)
