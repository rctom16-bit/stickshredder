"""QSS stylesheet for StickShredder — clean, professional look for IT admins."""

import os as _os

def _icon_path(name: str) -> str:
    """Return the forward-slash absolute path to an icon file (QSS needs forward slashes)."""
    return _os.path.join(_os.path.dirname(__file__), "icons", name).replace("\\", "/")

APP_STYLESHEET = """
/* ── Global ──────────────────────────────────────────────────────────── */
QWidget {
    font-family: "Segoe UI", "Arial", sans-serif;
    font-size: 13px;
    color: #1a202c;
    background-color: #f7fafc;
}

QMainWindow {
    background-color: #f7fafc;
}

/* ── Menu bar ────────────────────────────────────────────────────────── */
QMenuBar {
    background-color: #1a365d;
    color: #ffffff;
    padding: 2px;
    font-size: 13px;
}

QMenuBar::item {
    padding: 6px 12px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: #2a4a7f;
}

QMenu {
    background-color: #ffffff;
    border: 1px solid #cbd5e0;
    border-radius: 4px;
    padding: 4px;
}

QMenu::item {
    padding: 6px 24px;
    border-radius: 3px;
}

QMenu::item:selected {
    background-color: #e2e8f0;
    color: #1a365d;
}

/* ── Group boxes ─────────────────────────────────────────────────────── */
QGroupBox {
    font-weight: bold;
    font-size: 13px;
    color: #1a365d;
    border: 1px solid #cbd5e0;
    border-radius: 6px;
    margin-top: 12px;
    padding: 16px 10px 10px 10px;
    background-color: #ffffff;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    left: 12px;
}

/* ── Tables ──────────────────────────────────────────────────────────── */
QTableWidget, QTableView {
    background-color: #ffffff;
    alternate-background-color: #f0f4f8;
    border: 1px solid #cbd5e0;
    border-radius: 6px;
    gridline-color: #e2e8f0;
    selection-background-color: #bee3f8;
    selection-color: #1a202c;
    font-size: 12px;
}

QHeaderView::section {
    background-color: #1a365d;
    color: #ffffff;
    font-weight: bold;
    font-size: 12px;
    padding: 6px 8px;
    border: none;
    border-right: 1px solid #2a4a7f;
}

QHeaderView::section:first {
    border-top-left-radius: 5px;
}

QHeaderView::section:last {
    border-top-right-radius: 5px;
    border-right: none;
}

/* ── Buttons ─────────────────────────────────────────────────────────── */
QPushButton {
    background-color: #1a365d;
    color: #ffffff;
    border: none;
    border-radius: 5px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 500;
    min-height: 20px;
}

QPushButton:hover {
    background-color: #2a4a7f;
}

QPushButton:pressed {
    background-color: #12284a;
}

QPushButton:disabled {
    background-color: #a0aec0;
    color: #e2e8f0;
}

QPushButton#refreshButton {
    background-color: #2b6cb0;
    padding: 6px 14px;
}

QPushButton#refreshButton:hover {
    background-color: #3182ce;
}

/* The big wipe button — attention-grabbing */
QPushButton#wipeButton {
    background-color: #c53030;
    color: #ffffff;
    font-size: 16px;
    font-weight: bold;
    padding: 14px 32px;
    border-radius: 8px;
    min-height: 36px;
}

QPushButton#wipeButton:hover {
    background-color: #e53e3e;
}

QPushButton#wipeButton:pressed {
    background-color: #9b2c2c;
}

QPushButton#wipeButton:disabled {
    background-color: #a0aec0;
    color: #e2e8f0;
}

QPushButton#cancelButton {
    background-color: #718096;
}

QPushButton#cancelButton:hover {
    background-color: #4a5568;
}

/* ── Combo boxes ─────────────────────────────────────────────────────── */
QComboBox {
    background-color: #ffffff;
    border: 1px solid #cbd5e0;
    border-radius: 5px;
    padding: 6px 10px;
    min-height: 20px;
}

QComboBox:hover {
    border-color: #1a365d;
}

QComboBox:focus {
    border-color: #1a365d;
    border-width: 2px;
}

QComboBox:disabled {
    background-color: #edf2f7;
    color: #a0aec0;
    border-color: #e2e8f0;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox::down-arrow {
    image: url(%%DROPDOWN%%);
    width: 10px;
    height: 6px;
}

QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #cbd5e0;
    selection-background-color: #bee3f8;
    selection-color: #1a202c;
}

/* ── Spin boxes ──────────────────────────────────────────────────────── */
QSpinBox {
    background-color: #ffffff;
    border: 1px solid #cbd5e0;
    border-radius: 5px;
    padding: 6px 10px;
    padding-right: 24px;
    min-height: 20px;
}

QSpinBox:hover {
    border-color: #1a365d;
}

QSpinBox:focus {
    border-color: #1a365d;
    border-width: 2px;
}

QSpinBox:disabled {
    background-color: #edf2f7;
    color: #a0aec0;
    border-color: #e2e8f0;
}

QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #cbd5e0;
    border-top-right-radius: 5px;
    background-color: #f0f4f8;
}

QSpinBox::up-button:hover {
    background-color: #e2e8f0;
}

QSpinBox::up-arrow {
    image: url(%%ARROW_UP%%);
    width: 10px;
    height: 6px;
}

QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    border-left: 1px solid #cbd5e0;
    border-bottom-right-radius: 5px;
    background-color: #f0f4f8;
}

QSpinBox::down-button:hover {
    background-color: #e2e8f0;
}

QSpinBox::down-arrow {
    image: url(%%ARROW_DOWN%%);
    width: 10px;
    height: 6px;
}

/* ── Line edits / text edits ─────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #ffffff;
    border: 1px solid #cbd5e0;
    border-radius: 5px;
    padding: 6px 10px;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #1a365d;
    border-width: 2px;
}

QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    background-color: #edf2f7;
    color: #a0aec0;
    border-color: #e2e8f0;
}

/* ── Progress bars ───────────────────────────────────────────────────── */
QProgressBar {
    background-color: #e2e8f0;
    border: none;
    border-radius: 6px;
    text-align: center;
    font-size: 11px;
    font-weight: bold;
    color: #1a202c;
    min-height: 22px;
}

QProgressBar::chunk {
    background-color: #2b6cb0;
    border-radius: 6px;
}

/* ── Labels ──────────────────────────────────────────────────────────── */
QLabel {
    color: #1a202c;
    background-color: transparent;
}

QLabel#sectionLabel {
    font-size: 14px;
    font-weight: bold;
    color: #1a365d;
}

QLabel#statusLabel {
    font-size: 12px;
    color: #4a5568;
}

QLabel#warningLabel {
    color: #c53030;
    font-weight: bold;
}

/* ── Tab widget ──────────────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #cbd5e0;
    border-radius: 6px;
    background-color: #ffffff;
    top: -1px;
}

QTabBar::tab {
    background-color: #e2e8f0;
    color: #4a5568;
    padding: 8px 18px;
    border: 1px solid #cbd5e0;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-size: 12px;
}

QTabBar::tab:selected {
    background-color: #ffffff;
    color: #1a365d;
    font-weight: bold;
}

QTabBar::tab:hover:!selected {
    background-color: #cbd5e0;
}

/* ── Status bar ──────────────────────────────────────────────────────── */
QStatusBar {
    background-color: #1a365d;
    color: #ffffff;
    font-size: 12px;
    padding: 4px;
}

QStatusBar QLabel {
    color: #ffffff;
}

/* ── Scroll bars ─────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background-color: #f0f4f8;
    width: 10px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background-color: #a0aec0;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background-color: #718096;
}

QScrollBar:horizontal {
    background-color: #f0f4f8;
    height: 10px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background-color: #a0aec0;
    border-radius: 5px;
    min-width: 30px;
}

QScrollBar::add-line, QScrollBar::sub-line {
    width: 0px;
    height: 0px;
}

/* ── Check boxes ─────────────────────────────────────────────────────── */
QCheckBox {
    spacing: 6px;
    background-color: transparent;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 3px;
    border: 2px solid #cbd5e0;
    background-color: #ffffff;
}

QCheckBox::indicator:checked {
    background-color: #1a365d;
    border-color: #1a365d;
}

QCheckBox::indicator:disabled {
    background-color: #e2e8f0;
    border-color: #e2e8f0;
}

/* ── Dialog ──────────────────────────────────────────────────────────── */
QDialog {
    background-color: #f7fafc;
}

/* ── Tooltip ─────────────────────────────────────────────────────────── */
QToolTip {
    background-color: #1a365d;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 12px;
}

/* ── Dialog button box ──────────────────────────────────────────────── */
QDialogButtonBox QPushButton[text="Save"],
QDialogButtonBox QPushButton[text="OK"],
QDialogButtonBox QPushButton[text="Speichern"] {
    background-color: #276749;
    color: #ffffff;
}

QDialogButtonBox QPushButton[text="Save"]:hover,
QDialogButtonBox QPushButton[text="OK"]:hover,
QDialogButtonBox QPushButton[text="Speichern"]:hover {
    background-color: #2f855a;
}

QDialogButtonBox QPushButton[text="Save"]:pressed,
QDialogButtonBox QPushButton[text="OK"]:pressed,
QDialogButtonBox QPushButton[text="Speichern"]:pressed {
    background-color: #1e5631;
}

QDialogButtonBox QPushButton[text="Cancel"],
QDialogButtonBox QPushButton[text="Abbrechen"] {
    background-color: #718096;
    color: #ffffff;
}

QDialogButtonBox QPushButton[text="Cancel"]:hover,
QDialogButtonBox QPushButton[text="Abbrechen"]:hover {
    background-color: #4a5568;
}

QDialogButtonBox QPushButton[text="Cancel"]:pressed,
QDialogButtonBox QPushButton[text="Abbrechen"]:pressed {
    background-color: #2d3748;
}

/* ── Message box ────────────────────────────────────────────────────── */
QMessageBox {
    background-color: #ffffff;
}

QMessageBox QLabel {
    color: #1a202c;
    font-size: 13px;
    padding: 8px 4px;
}

QMessageBox QPushButton {
    min-width: 80px;
    padding: 8px 20px;
}

/* ── Splitter ────────────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #cbd5e0;
    width: 2px;
}
""".replace("%%ARROW_UP%%", _icon_path("arrow_up.svg")).replace("%%ARROW_DOWN%%", _icon_path("arrow_down.svg")).replace("%%DROPDOWN%%", _icon_path("dropdown.svg"))
