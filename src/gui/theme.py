"""Design direction C „Signal“: light and dark palette, fonts and the stylesheet.

One Qt stylesheet is generated from the active :class:`Palette`. Widgets pick
their look through dynamic properties (``card``, ``role``, ``variant``,
``tone``), so switching the palette restyles everything live. The mode follows
Windows (``QStyleHints.colorScheme``) unless the user picked light or dark.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPalette

THEME_SETTINGS = ("system", "light", "dark")
FONT_DIR = Path(__file__).resolve().parent / "fonts"
ICON_DIR = (Path(__file__).resolve().parent / "icons").as_posix()


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    rail: str
    border: str
    border_strong: str
    surface: str
    inset: str
    button_bg: str
    button_border: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_hover: str
    on_accent: str
    accent_text: str
    accent_soft: str
    accent_soft_text: str
    nav_active: str
    rail_hover: str
    toggle_bg: str
    logo_body: str
    danger: str
    danger_bg: str
    danger_border: str
    danger_solid: str
    on_danger: str
    warning: str
    warning_bg: str
    warning_border: str
    success_bg: str
    success_border: str
    success_text: str
    success_eyebrow: str
    limited_bg: str
    limited_border: str
    limited_text: str
    limited_eyebrow: str
    failed_bg: str
    failed_border: str
    failed_text: str
    failed_eyebrow: str
    track: str
    stripe: str
    cert_bg: str
    cert_text: str
    cert_muted: str
    cert_body: str
    cert_placeholder: str
    log_bg: str
    log_text: str
    note_text: str


DARK = Palette(
    name="dark",
    bg="#0B1020", rail="#080C18", border="#1B2440", border_strong="#1F2B4A",
    surface="#121A2E", inset="#0E1528", button_bg="#1A2440", button_border="#2A375C",
    text="#EEF2FF", muted="#A5B0C8", faint="#6B7896",
    accent="#2DD4A7", accent_hover="#7AF0CF", on_accent="#04130E", accent_text="#2DD4A7",
    accent_soft="#0F2A24", accent_soft_text="#7AF0CF",
    nav_active="#13213A", rail_hover="#101830", toggle_bg="#121A2E", logo_body="#2DD4A7",
    danger="#FFB4AA", danger_bg="#2A1416", danger_border="#5A2A2A",
    danger_solid="#FF8A7A", on_danger="#2A0F0C",
    warning="#FFD38A", warning_bg="#2A2210", warning_border="#5A4A1F",
    success_bg="#0F2A24", success_border="#1D5446", success_text="#A9E5D3",
    success_eyebrow="#7AF0CF",
    limited_bg="#2A2210", limited_border="#5A4A1F", limited_text="#F0D9A8",
    limited_eyebrow="#FFD38A",
    failed_bg="#2A1416", failed_border="#5A2A2A", failed_text="#F3C5BE",
    failed_eyebrow="#FFB4AA",
    track="#24304F", stripe="#25B892",
    cert_bg="#F7F8FC", cert_text="#141A2C", cert_muted="#4A5470", cert_body="#3A4460",
    cert_placeholder="#A3ACC2",
    log_bg="#080C18", log_text="#C9D2E6", note_text="#C9D2E6",
)

LIGHT = Palette(
    name="light",
    bg="#F3F5FA", rail="#FFFFFF", border="#DDE3EE", border_strong="#DDE3EE",
    surface="#FFFFFF", inset="#F3F5FA", button_bg="#F3F5FA", button_border="#CDD5E3",
    text="#0B1020", muted="#4A5470", faint="#4A5470",
    accent="#2DD4A7", accent_hover="#25C49A", on_accent="#04130E", accent_text="#0B7A5E",
    accent_soft="#DDF7EF", accent_soft_text="#075A45",
    nav_active="#DDF7EF", rail_hover="#F3F5FA", toggle_bg="#FFFFFF", logo_body="#0B1020",
    danger="#B42318", danger_bg="#FEECEB", danger_border="#F1B5AE",
    danger_solid="#B42318", on_danger="#FFFFFF",
    warning="#7A4A00", warning_bg="#FFF1D6", warning_border="#F0D49A",
    success_bg="#E6F8F2", success_border="#B5E8D7", success_text="#075A45",
    success_eyebrow="#0B7A5E",
    limited_bg="#FFF1D6", limited_border="#F0D49A", limited_text="#5C3800",
    limited_eyebrow="#7A4A00",
    failed_bg="#FEECEB", failed_border="#F6C7C2", failed_text="#7A1A12",
    failed_eyebrow="#B42318",
    track="#DDE3EE", stripe="#25B892",
    cert_bg="#0B1020", cert_text="#EEF2FF", cert_muted="#A5B0C8", cert_body="#C9D2E6",
    cert_placeholder="#5B6A8C",
    log_bg="#FFFFFF", log_text="#2A3350", note_text="#3A4460",
)


# -- Fonts -----------------------------------------------------------------------


@dataclass(frozen=True)
class FontSet:
    heading: str   # Space Grotesk: headings, the big drive letter, percentages
    body: str      # Inter: everything else
    mono: str      # JetBrains Mono: serials, fingerprints, numbers


FALLBACK_FONTS = FontSet(heading="Segoe UI", body="Segoe UI", mono="Consolas")
_BUNDLED = {
    "heading": ("SpaceGrotesk", "Space Grotesk"),
    "body": ("Inter", "Inter"),
    "mono": ("JetBrainsMono", "JetBrains Mono"),
}

_fonts: FontSet = FALLBACK_FONTS


def _register(folder: Path, family: str) -> str | None:
    if not folder.is_dir():
        return None
    for ttf in sorted(folder.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(ttf))
        if font_id >= 0 and family in QFontDatabase.applicationFontFamilies(font_id):
            return family
    return None


def load_fonts(font_dir: Path | None = None) -> FontSet:
    """Register the bundled fonts; each missing one falls back to Segoe UI/Consolas."""
    global _fonts
    base = Path(font_dir) if font_dir is not None else FONT_DIR
    chosen = {}
    for role, (folder, family) in _BUNDLED.items():
        chosen[role] = _register(base / folder, family) or getattr(FALLBACK_FONTS, role)
    _fonts = FontSet(**chosen)
    return _fonts


def fonts() -> FontSet:
    return _fonts


# -- Mode --------------------------------------------------------------------------


def resolve_mode(setting: str, scheme) -> str:
    """``"light"`` or ``"dark"`` for a stored setting and the Windows scheme."""
    if setting in ("light", "dark"):
        return setting
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def palette_for(mode: str) -> Palette:
    return DARK if mode == "dark" else LIGHT


_current: Palette = DARK


def current() -> Palette:
    """The palette in use (custom-painted widgets read it while painting)."""
    return _current


def build_qpalette(p: Palette) -> QPalette:
    qp = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.bg,
        QPalette.ColorRole.WindowText: p.text,
        QPalette.ColorRole.Base: p.inset,
        QPalette.ColorRole.AlternateBase: p.surface,
        QPalette.ColorRole.Text: p.text,
        QPalette.ColorRole.Button: p.button_bg,
        QPalette.ColorRole.ButtonText: p.text,
        QPalette.ColorRole.BrightText: p.on_accent,
        QPalette.ColorRole.Highlight: p.accent,
        QPalette.ColorRole.HighlightedText: p.on_accent,
        QPalette.ColorRole.ToolTipBase: p.surface,
        QPalette.ColorRole.ToolTipText: p.text,
        QPalette.ColorRole.PlaceholderText: p.muted,
        QPalette.ColorRole.Link: p.accent_text,
        QPalette.ColorRole.Mid: p.border,
        QPalette.ColorRole.Light: p.surface,
        QPalette.ColorRole.Dark: p.rail,
    }
    for role, colour in roles.items():
        qp.setColor(role, QColor(colour))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        qp.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.faint))
    return qp


def build_qss(p: Palette, f: FontSet) -> str:
    """The application stylesheet for palette ``p`` (radii and sizes from the artboards)."""
    return f"""
* {{ font-family: "{f.body}"; }}
QWidget {{ color: {p.text}; font-size: 14px; }}
QMainWindow, QDialog, QWidget#AppRoot, QWidget#PageRoot {{ background: {p.bg}; }}
QScrollArea, QScrollArea > QWidget > QWidget#ScrollBody {{ background: transparent; border: none; }}
QToolTip {{ color: {p.text}; background: {p.surface}; border: 1px solid {p.border}; padding: 6px 8px; }}

/* Rail */
QFrame#Rail {{ background: {p.rail}; border: none; border-right: 1px solid {p.border}; }}
QToolButton[rail="true"] {{
    color: {p.muted}; background: transparent; border: none; border-radius: 12px;
    font-size: 11px; padding: 6px 0px;
}}
QToolButton[rail="true"]:hover {{ background: {p.rail_hover}; }}
QToolButton[rail="true"]:checked {{ color: {p.accent_text}; background: {p.nav_active}; font-weight: 600; }}
QToolButton[rail="true"]::menu-indicator {{ image: none; width: 0px; }}
QPushButton#ThemeToggle, QPushButton#LanguageToggle {{
    min-width: 44px; max-width: 44px; min-height: 44px; max-height: 44px; padding: 0px;
    border-radius: 22px; border: 1px solid {p.border}; background: {p.toggle_bg};
    color: {p.text}; font-size: 12px; font-weight: 600;
}}
QPushButton#ThemeToggle:hover, QPushButton#LanguageToggle:hover {{ border-color: {p.accent}; }}

/* Cards */
QFrame[card="surface"] {{ background: {p.surface}; border: 1px solid {p.border_strong}; border-radius: 20px; }}
QFrame[card="small"] {{ background: {p.surface}; border: 1px solid {p.border_strong}; border-radius: 16px; }}
QFrame[card="note"] {{ background: {p.surface}; border: 1px solid {p.border_strong}; border-radius: 14px; }}
QFrame[card="tile"] {{ background: {p.inset}; border: none; border-radius: 12px; }}
QFrame[card="row"] {{ background: {p.inset if p.name == "dark" else p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
QFrame[card="row"][selectable="true"]:hover {{ border-color: {p.accent}; }}
QFrame[card="row"]:focus {{ border-color: {p.accent}; }}
QFrame[card="cert"] {{ background: {p.cert_bg}; border: none; border-radius: 20px; }}
QFrame[card="log"] {{ background: {p.log_bg}; border: 1px solid {p.border}; border-radius: 20px; }}
QFrame[card="result"][tone="ok"] {{ background: {p.success_bg}; border: 1px solid {p.success_border}; border-radius: 20px; }}
QFrame[card="result"][tone="limited"] {{ background: {p.limited_bg}; border: 1px solid {p.limited_border}; border-radius: 20px; }}
QFrame[card="result"][tone="failed"] {{ background: {p.failed_bg}; border: 1px solid {p.failed_border}; border-radius: 20px; }}
QFrame[card="banner"] {{ background: {p.warning_bg}; border: 1px solid {p.warning_border}; border-radius: 12px; }}

/* Text roles */
QLabel {{ background: transparent; border: none; }}
QLabel[role="hero-letter"] {{ font-family: "{f.heading}"; font-size: 72px; font-weight: 700; color: {p.accent_text}; }}
QLabel[role="hero-letter"][muted="true"] {{ color: {p.faint}; }}
QLabel[role="h1"] {{ font-family: "{f.heading}"; font-size: 26px; font-weight: 600; }}
QLabel[role="h1-running"] {{ font-family: "{f.heading}"; font-size: 28px; font-weight: 600; }}
QLabel[role="h1-result"] {{ font-family: "{f.heading}"; font-size: 32px; font-weight: 700; }}
QLabel[role="placeholder-title"] {{ font-family: "{f.heading}"; font-size: 28px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {p.muted}; font-size: 14px; }}
QLabel[role="hint"] {{ color: {p.muted}; font-size: 13px; }}
QLabel[role="section"] {{ color: {p.muted}; font-size: 14px; font-weight: 600; }}
QLabel[role="tile-label"] {{ color: {p.muted}; font-size: 12px; }}
QLabel[role="tile-value"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="tile-value-mono"] {{ font-family: "{f.mono}"; font-size: 17px; }}
QLabel[role="tile-note"] {{ color: {p.muted}; font-size: 12px; }}
QLabel[role="tile-note"][tone="accent"] {{ color: {p.accent_text}; font-weight: 600; }}
QLabel[role="tile-note"][tone="warning"] {{ color: {p.warning}; font-weight: 600; }}
QLabel[role="warning"] {{ color: {p.warning}; font-size: 13px; }}
QLabel[role="danger"] {{ color: {p.danger}; font-size: 13px; }}
QLabel[role="row-letter"] {{ font-family: "{f.heading}"; font-size: 20px; font-weight: 700; color: {p.text}; }}
QLabel[role="row-letter"][locked="true"] {{ color: {p.faint}; }}
QLabel[role="row-text"] {{ font-size: 14px; color: {p.text}; }}
QLabel[role="row-text"][locked="true"] {{ color: {p.muted}; }}
QLabel[role="chip"] {{ font-size: 12px; font-weight: 600; border-radius: 6px; padding: 4px 8px; }}
QLabel[role="chip"][tone="danger"] {{ color: {p.danger}; background: {p.danger_bg}; }}
QLabel[role="chip"][tone="accent"] {{ color: {p.accent_soft_text}; background: {p.accent_soft}; }}
QLabel[role="chip"][tone="warning"] {{ color: {p.warning}; background: {p.warning_bg}; }}
QLabel[role="chip"][tone="neutral"] {{ color: {p.muted}; background: {p.button_bg}; }}
QLabel[role="percent"] {{ font-family: "{f.heading}"; font-size: 96px; font-weight: 700; color: {p.text}; }}
QLabel[role="percent-unit"] {{ font-family: "{f.heading}"; font-size: 44px; font-weight: 700; color: {p.muted}; }}
QLabel[role="log-line"] {{ font-family: "{f.mono}"; font-size: 12px; color: {p.log_text}; }}
QLabel[role="log-line"][tone="accent"] {{ color: {p.accent_text}; }}
QLabel[role="log-line"][tone="warning"] {{ color: {p.warning}; }}
QLabel[role="log-line"][tone="danger"] {{ color: {p.danger}; }}
QLabel[role="banner"] {{ color: {p.warning}; font-size: 13px; }}
QLabel[role="note"] {{ color: {p.note_text}; font-size: 13px; }}
QLabel[role="result-eyebrow"] {{ font-size: 13px; font-weight: 600; }}
QLabel[role="result-eyebrow"][tone="ok"] {{ color: {p.success_eyebrow}; }}
QLabel[role="result-eyebrow"][tone="limited"] {{ color: {p.limited_eyebrow}; }}
QLabel[role="result-eyebrow"][tone="failed"] {{ color: {p.failed_eyebrow}; }}
QLabel[role="result-key"] {{ font-size: 14px; }}
QLabel[role="result-key"][tone="ok"] {{ color: {p.success_text}; }}
QLabel[role="result-key"][tone="limited"] {{ color: {p.limited_text}; }}
QLabel[role="result-key"][tone="failed"] {{ color: {p.failed_text}; }}
QLabel[role="result-value"] {{ font-size: 14px; color: {p.text}; }}
QLabel[role="cert-eyebrow"] {{ font-size: 12px; font-weight: 600; color: {p.cert_muted}; }}
QLabel[role="cert-title"] {{ font-family: "{f.heading}"; font-size: 20px; font-weight: 700; color: {p.cert_text}; }}
QLabel[role="cert-number"] {{ font-family: "{f.heading}"; font-size: 22px; font-weight: 700; color: {p.cert_text}; }}
QLabel[role="cert-body"] {{ font-size: 13px; color: {p.cert_body}; }}
QLabel[role="cert-lines"] {{ font-size: 14px; color: {p.cert_body}; }}
QLabel[role="cert-mono"] {{ font-family: "{f.mono}"; font-size: 12px; color: {p.cert_body}; }}
QLabel[role="history-label"] {{ font-size: 13px; color: {p.muted}; }}
QLabel[role="history-value"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="history-status"] {{ font-size: 13px; font-weight: 600; color: {p.accent_text}; }}
QLabel[role="history-status"][tone="danger"] {{ color: {p.danger}; }}
QLabel[role="history-status"][tone="muted"] {{ color: {p.muted}; font-weight: 400; }}
QLabel[role="dialog-title"] {{ font-family: "{f.heading}"; font-size: 22px; font-weight: 700; }}
QLabel[role="dialog-disk"] {{ font-size: 14px; color: {p.text}; }}
QLabel[role="field-label"] {{ font-size: 13px; font-weight: 600; color: {p.muted}; }}

/* Buttons */
QPushButton {{
    font-size: 14px; color: {p.text}; background: {p.button_bg};
    border: 1px solid {p.button_border}; border-radius: 10px; padding: 0px 16px; min-height: 44px;
}}
QPushButton:hover {{ border-color: {p.accent}; }}
QPushButton:focus {{ border-color: {p.accent}; }}
QPushButton:disabled {{ color: {p.faint}; border-color: {p.border}; }}
QPushButton[variant="primary"] {{
    font-family: "{f.heading}"; font-size: 20px; font-weight: 700; color: {p.on_accent};
    background: {p.accent}; border: none; border-radius: 14px; min-height: 64px; padding: 0px 24px;
}}
QPushButton[variant="primary"]:hover {{ background: {p.accent_hover}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.track}; color: {p.muted}; }}
QPushButton[variant="accent"] {{
    font-size: 15px; font-weight: 700; color: {p.on_accent}; background: {p.accent};
    border: none; border-radius: 12px; padding: 0px 20px; min-height: 48px;
}}
QPushButton[variant="accent"]:hover {{ background: {p.accent_hover}; }}
QPushButton[variant="accent"]:disabled {{ background: {p.track}; color: {p.muted}; }}
QPushButton[variant="large"] {{ font-size: 15px; border-radius: 12px; padding: 0px 18px; min-height: 48px; }}
QPushButton[variant="danger-outline"] {{
    color: {p.danger}; background: transparent; border: 1px solid {p.danger_border}; padding: 0px 18px;
}}
QPushButton[variant="danger-outline"]:hover {{ background: {p.danger_bg}; }}
QPushButton[variant="danger"] {{
    font-weight: 700; color: {p.on_danger}; background: {p.danger_solid}; border: none; padding: 0px 20px;
}}
QPushButton[variant="danger"]:disabled {{ background: {p.track}; color: {p.muted}; }}
QPushButton[variant="link"] {{
    color: {p.accent_text}; background: transparent; border: none; padding: 0px; min-height: 24px;
    font-size: 13px; font-weight: 600; text-align: left;
}}
QPushButton[variant="link"]:hover {{ color: {p.accent_hover}; }}
QPushButton[variant="small"] {{ font-size: 13px; min-height: 32px; padding: 0px 12px; border-radius: 8px; }}

/* Inputs */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {p.inset}; color: {p.text}; border: 1px solid {p.button_border};
    border-radius: 10px; padding: 8px 12px; selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {p.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {p.faint}; border-color: {p.border}; }}
QLineEdit[role="confirm-word"] {{ font-family: "{f.heading}"; font-size: 20px; font-weight: 700; padding: 10px 14px; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 28px; border: none; }}
QComboBox::down-arrow {{ image: url({ICON_DIR}/chevron_down.svg); width: 10px; height: 6px; }}
QSpinBox {{ padding-right: 28px; min-width: 64px; }}
QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 24px; border: none; background: transparent; }}
QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; border: none; background: transparent; }}
QSpinBox::up-arrow {{ image: url({ICON_DIR}/chevron_up.svg); width: 10px; height: 6px; }}
QSpinBox::down-arrow {{ image: url({ICON_DIR}/chevron_down.svg); width: 10px; height: 6px; }}
QComboBox QAbstractItemView {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border};
    selection-background-color: {p.nav_active}; selection-color: {p.text}; outline: none;
}}
QCheckBox, QRadioButton {{ spacing: 10px; background: transparent; min-height: 26px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border: 1px solid {p.faint}; background: {p.inset};
}}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QRadioButton::indicator:checked {{ border: 4px solid {p.accent}; background: {p.on_accent}; width: 10px; height: 10px; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; image: url({ICON_DIR}/check.svg); }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {p.accent}; }}

/* Menus, scrollbars, legacy dialogs (history / settings) */
QMenu {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; padding: 6px; border-radius: 10px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.nav_active}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.button_border}; border-radius: 4px; min-height: 32px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.button_border}; border-radius: 4px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 12px; margin-top: 14px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0px 4px; color: {p.muted}; }}
QTableView, QTableWidget, QListWidget, QTreeView {{
    background: {p.surface}; alternate-background-color: {p.inset}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 10px; gridline-color: {p.border};
    selection-background-color: {p.nav_active}; selection-color: {p.text};
}}
QHeaderView::section {{ background: {p.inset}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border}; padding: 6px 8px; }}
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 10px; }}
QTabBar::tab {{ background: transparent; color: {p.muted}; padding: 8px 14px; }}
QTabBar::tab:selected {{ color: {p.accent_text}; border-bottom: 2px solid {p.accent}; }}
QMessageBox QLabel {{ color: {p.text}; }}
"""


class ThemeManager(QObject):
    """Applies the palette for the stored setting and follows Windows when set to "system"."""

    changed = Signal(object)  # Palette

    def __init__(self, app, setting: str = "system", font_set: FontSet | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._setting = setting if setting in THEME_SETTINGS else "system"
        self._fonts = font_set
        self._mode = "dark"
        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._on_scheme_changed)

    @property
    def setting(self) -> str:
        return self._setting

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def palette(self) -> Palette:
        return palette_for(self._mode)

    def set_setting(self, setting: str) -> None:
        self._setting = setting if setting in THEME_SETTINGS else "system"
        self.apply()

    def apply(self) -> None:
        global _current
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hints is not None else Qt.ColorScheme.Unknown
        self._mode = resolve_mode(self._setting, scheme)
        palette = palette_for(self._mode)
        _current = palette
        self._app.setPalette(build_qpalette(palette))
        self._app.setStyleSheet(build_qss(palette, self._fonts or fonts()))
        self.changed.emit(palette)

    def _on_scheme_changed(self, _scheme) -> None:
        if self._setting == "system":
            self.apply()
