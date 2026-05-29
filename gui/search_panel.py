"""
Search panel — a polished search bar with live debounce, filter controls,
and a result-count badge.
"""
from typing import Any, Dict, Optional

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)


class SearchPanel(QWidget):
    # Emitted after debounce delay with the trimmed query string
    search_requested = Signal(str)
    clear_requested  = Signal()

    def __init__(self, config: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._debounce_ms: int = config.get("debounce_ms", 300)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire_search)
        self._setup_ui()
        self._setup_shortcuts()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Search icon label
        icon_label = QLabel("🔍")
        icon_label.setFixedWidth(22)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        # Search input
        self._edit = QLineEdit()
        self._edit.setPlaceholderText(
            "Search blocks… (e.g. motor, MCCB, mtr, breaker)"
        )
        self._edit.setClearButtonEnabled(True)
        self._edit.setMinimumHeight(34)
        font = QFont("Segoe UI", 10)
        self._edit.setFont(font)
        self._edit.textChanged.connect(self._on_text_changed)
        self._edit.returnPressed.connect(self._fire_search)
        layout.addWidget(self._edit, stretch=1)

        # Result count badge
        self._count_label = QLabel("")
        self._count_label.setObjectName("subLabel")
        self._count_label.setFixedWidth(100)
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._count_label)

    def _setup_shortcuts(self) -> None:
        # Ctrl+F focuses the search bar
        sc = QShortcut(QKeySequence("Ctrl+F"), self.window() if self.window() else self)
        sc.activated.connect(self.focus)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def focus(self) -> None:
        self._edit.setFocus()
        self._edit.selectAll()

    def set_result_count(self, count: int) -> None:
        if count == 0:
            self._count_label.setText("")
        else:
            self._count_label.setText(f"{count} result{'s' if count != 1 else ''}")

    def current_query(self) -> str:
        return self._edit.text().strip()

    def clear(self) -> None:
        self._edit.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        self._timer.start(self._debounce_ms)
        if not text.strip():
            self._count_label.setText("")
            self.clear_requested.emit()

    def _fire_search(self) -> None:
        query = self._edit.text().strip()
        if query:
            self.search_requested.emit(query)
