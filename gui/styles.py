"""
Dark theme QSS stylesheet + palette helper.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
BG_DARK        = "#1e1e1e"
BG_MEDIUM      = "#252526"
BG_LIGHT       = "#2d2d30"
BG_HOVER       = "#3e3e42"
BG_SELECTED    = "#094771"
ACCENT         = "#0078d4"
ACCENT_HOVER   = "#1a86d9"
ACCENT_PRESSED = "#006aba"
TEXT_PRIMARY   = "#d4d4d4"
TEXT_SECONDARY = "#9d9d9d"
TEXT_DISABLED  = "#5a5a5a"
BORDER         = "#3f3f46"
SUCCESS        = "#4ec9b0"
WARNING        = "#dcdcaa"
ERROR_COLOR    = "#f44747"
SCORE_HIGH     = "#4ec9b0"
SCORE_MED      = "#dcdcaa"
SCORE_LOW      = "#9d9d9d"


# ---------------------------------------------------------------------------
# QSS
# ---------------------------------------------------------------------------

DARK_QSS = f"""
/* ── Global ── */
* {{
    outline: none;
}}

QWidget {{
    background-color: {BG_DARK};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 9pt;
}}

QMainWindow {{
    background-color: {BG_DARK};
}}

/* ── Toolbar / Menu Bar ── */
QMenuBar {{
    background-color: {BG_MEDIUM};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item:selected {{
    background-color: {BG_HOVER};
}}
QMenu {{
    background-color: {BG_LIGHT};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 22px 5px 22px;
}}
QMenu::item:selected {{
    background-color: {BG_SELECTED};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 3px 8px;
}}

/* ── Status Bar ── */
QStatusBar {{
    background-color: {ACCENT};
    color: #ffffff;
    font-size: 8.5pt;
}}
QStatusBar QLabel {{
    color: #ffffff;
    background-color: transparent;
}}

/* ── Line Edit / Search Bar ── */
QLineEdit {{
    background-color: {BG_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 10px;
    color: {TEXT_PRIMARY};
    selection-background-color: {BG_SELECTED};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled {{
    color: {TEXT_DISABLED};
    background-color: {BG_MEDIUM};
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {BG_LIGHT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 5px 14px;
    min-width: 70px;
}}
QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT_PRESSED};
    color: #ffffff;
}}
QPushButton:disabled {{
    color: {TEXT_DISABLED};
    border-color: {BORDER};
}}
QPushButton#accentButton {{
    background-color: {ACCENT};
    color: #ffffff;
    border: none;
}}
QPushButton#accentButton:hover {{
    background-color: {ACCENT_HOVER};
}}
QPushButton#accentButton:pressed {{
    background-color: {ACCENT_PRESSED};
}}
QPushButton#dangerButton {{
    background-color: #6b1a1a;
    color: #f08080;
    border: 1px solid #8b2222;
}}
QPushButton#dangerButton:hover {{
    background-color: #8b2222;
}}

/* ── Table View ── */
QTableView {{
    background-color: {BG_MEDIUM};
    alternate-background-color: {BG_LIGHT};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    selection-background-color: {BG_SELECTED};
    selection-color: #ffffff;
}}
QTableView::item {{
    padding: 3px 6px;
}}
QHeaderView::section {{
    background-color: {BG_LIGHT};
    color: {TEXT_SECONDARY};
    padding: 5px 8px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    font-weight: bold;
    font-size: 8.5pt;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QHeaderView::section:hover {{
    background-color: {BG_HOVER};
    color: {TEXT_PRIMARY};
}}

/* ── Scroll Bars ── */
QScrollBar:vertical {{
    background: {BG_MEDIUM};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BG_HOVER};
    border-radius: 5px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_SECONDARY};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG_MEDIUM};
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {BG_HOVER};
    border-radius: 5px;
    min-width: 20px;
}}

/* ── Progress Bar ── */
QProgressBar {{
    background-color: {BG_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    height: 16px;
    text-align: center;
    color: {TEXT_PRIMARY};
    font-size: 8pt;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}

/* ── Dialogs ── */
QDialog {{
    background-color: {BG_DARK};
}}

/* ── Labels ── */
QLabel {{
    background-color: transparent;
    color: {TEXT_PRIMARY};
}}
QLabel#sectionHeader {{
    font-size: 10pt;
    font-weight: bold;
    color: {TEXT_PRIMARY};
    padding-bottom: 4px;
}}
QLabel#subLabel {{
    color: {TEXT_SECONDARY};
    font-size: 8.5pt;
}}

/* ── List Widget ── */
QListWidget {{
    background-color: {BG_MEDIUM};
    border: 1px solid {BORDER};
    border-radius: 4px;
}}
QListWidget::item {{
    padding: 5px 8px;
}}
QListWidget::item:selected {{
    background-color: {BG_SELECTED};
    color: #ffffff;
}}
QListWidget::item:hover {{
    background-color: {BG_HOVER};
}}

/* ── Splitter ── */
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:hover {{
    background-color: {ACCENT};
}}

/* ── Tabs ── */
QTabWidget::pane {{
    border: 1px solid {BORDER};
}}
QTabBar::tab {{
    background-color: {BG_MEDIUM};
    color: {TEXT_SECONDARY};
    padding: 6px 16px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {TEXT_PRIMARY};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover {{
    color: {TEXT_PRIMARY};
    background-color: {BG_HOVER};
}}

/* ── ToolTip ── */
QToolTip {{
    background-color: {BG_LIGHT};
    color: {TEXT_PRIMARY};
    border: 1px solid {ACCENT};
    padding: 4px 8px;
    border-radius: 3px;
}}

/* ── Combo Box ── */
QComboBox {{
    background-color: {BG_LIGHT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    color: {TEXT_PRIMARY};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_LIGHT};
    border: 1px solid {BORDER};
    selection-background-color: {BG_SELECTED};
}}

/* ── GroupBox ── */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
    font-weight: bold;
    color: {TEXT_SECONDARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
"""


def apply_dark_theme(app: QApplication) -> None:
    """Apply dark palette and QSS to the application."""
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(BG_DARK))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base,            QColor(BG_MEDIUM))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(BG_LIGHT))
    palette.setColor(QPalette.ColorRole.ToolTipBase,     QColor(BG_LIGHT))
    palette.setColor(QPalette.ColorRole.ToolTipText,     QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text,            QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button,          QColor(BG_LIGHT))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.BrightText,      QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link,            QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(BG_SELECTED))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

    # Disabled colors
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(TEXT_DISABLED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(TEXT_DISABLED))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_DISABLED))

    app.setPalette(palette)
    app.setStyleSheet(DARK_QSS)
