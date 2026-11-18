"""The Optionen page: what the settings dialog offered, plus appearance, demo
mode and „Über“.

Company details, logo, certificate folder and language, and the wipe defaults
(operator, method, verification, protection class) are edited here and saved
to ``AppConfig`` with „Speichern“, checked the way the old dialog checked them.
Theme and UI language take effect at once: the page asks the window
(``theme_requested`` / ``language_requested``), which also owns the rail toggles.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.config import APP_VERSION
from core.log import audit_log
from gui.i18n import tr
from gui.widgets import make_card, make_label, set_prop

GITHUB_URL = "https://github.com/rctom16-bit/stickshredder"
THEMES = ("light", "dark", "system")
UI_LANGUAGES = ("de", "en")
CERT_LANGUAGES = ("de", "en", "both")
METHODS = ("quick", "standard", "bsi")      # AppConfig.default_wipe_method values
VERIFY_MODES = ("full", "sample")
LABEL_WIDTH = 160  # the labels of the two left-hand sections line up
_METHOD_TEXT = {"quick": "method.zero", "standard": "method.standard", "bsi": "method.bsi"}


def pick_image(parent, title: str, file_filter: str) -> str:
    path, _ = QFileDialog.getOpenFileName(parent, title, "", file_filter)
    return path


def pick_folder(parent, title: str, start: str) -> str:
    return QFileDialog.getExistingDirectory(parent, title, start)


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def _button(variant: str = "") -> QPushButton:
    button = QPushButton()
    if variant:
        button.setProperty("variant", variant)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _set_combo(combo: QComboBox, value) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)


class OptionsPage(QWidget):
    """Grouped settings with „Speichern“; appearance applies immediately."""

    saved = Signal()
    theme_requested = Signal(str)
    language_requested = Signal(str)
    demo_requested = Signal()

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setObjectName("PageRoot")
        self._status: tuple[str, dict, str] | None = None
        self._labels: list[tuple] = []  # (label, key)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("ScrollBody")
        scroll.setWidget(body)
        outer.addWidget(scroll)
        root = QVBoxLayout(body)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(20)

        head = QHBoxLayout()
        head.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.title_label = make_label("", "h1")
        self.subtitle_label = make_label("", "muted", wrap=True)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        head.addLayout(titles, 1)
        self.status_label = make_label("", "status", wrap=True)
        self.status_label.hide()
        self.reset_button = _button()
        self.reset_button.clicked.connect(self.load_from_config)
        self.save_button = _button("accent")
        self.save_button.clicked.connect(self.save)
        head.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)

        columns = QHBoxLayout()
        columns.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(20)
        right = QVBoxLayout()
        right.setSpacing(20)
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)
        root.addLayout(columns)
        root.addStretch(1)

        left.addWidget(self._build_cert_section())
        left.addWidget(self._build_wipe_section())
        left.addStretch(1)
        right.addWidget(self._build_look_section())
        right.addWidget(self._build_demo_section())
        right.addWidget(self._build_about_section())
        right.addStretch(1)

        self.retranslate()
        self.load_from_config()

    # ── Sections ──────────────────────────────────────────────────────────────

    def _section(self, title_key: str, hint_key: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = make_card("surface")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(14)
        title = make_label("", "card-title")
        self._labels.append((title, title_key))
        layout.addWidget(title)
        if hint_key:
            hint = make_label("", "hint", wrap=True)
            self._labels.append((hint, hint_key))
            layout.addWidget(hint)
        return card, layout

    def _form(self, layout: QVBoxLayout, label_width: int = 0) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, label_width)
        layout.addLayout(grid)
        return grid

    def _row(self, grid: QGridLayout, key: str, field) -> None:
        row = grid.rowCount()
        label = make_label("", "field-label")
        self._labels.append((label, key))
        grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if isinstance(field, QWidget):
            grid.addWidget(field, row, 1)
        else:
            grid.addLayout(field, row, 1)

    def _build_cert_section(self) -> QFrame:
        card, layout = self._section("settings.section_cert", "settings.section_cert_hint")
        grid = self._form(layout, LABEL_WIDTH)
        self.company_edit = QLineEdit()
        self._row(grid, "settings.company", self.company_edit)
        self.address_edit = QPlainTextEdit()
        self.address_edit.setFixedHeight(84)
        self.address_edit.setTabChangesFocus(True)
        self._row(grid, "settings.address", self.address_edit)

        logo = QHBoxLayout()
        logo.setSpacing(8)
        self.logo_edit = QLineEdit()
        self.logo_edit.setReadOnly(True)
        self.logo_browse_button = _button("small")
        self.logo_browse_button.clicked.connect(self._browse_logo)
        self.logo_clear_button = _button("small")
        self.logo_clear_button.clicked.connect(self.logo_edit.clear)
        logo.addWidget(self.logo_edit, 1)
        logo.addWidget(self.logo_browse_button)
        logo.addWidget(self.logo_clear_button)
        self._row(grid, "settings.logo", logo)

        folder = QHBoxLayout()
        folder.setSpacing(8)
        self.cert_dir_edit = QLineEdit()
        self.cert_dir_browse_button = _button("small")
        self.cert_dir_browse_button.clicked.connect(self._browse_cert_dir)
        folder.addWidget(self.cert_dir_edit, 1)
        folder.addWidget(self.cert_dir_browse_button)
        self._row(grid, "settings.cert_dir", folder)

        self.cert_language_combo = QComboBox()
        for lang in CERT_LANGUAGES:
            self.cert_language_combo.addItem("", lang)
        self._row(grid, "settings.cert_language", self.cert_language_combo)
        return card

    def _build_wipe_section(self) -> QFrame:
        card, layout = self._section("settings.section_wipe", "settings.section_wipe_hint")
        grid = self._form(layout, LABEL_WIDTH)
        self.operator_edit = QLineEdit()
        self._row(grid, "options.operator", self.operator_edit)
        self.method_combo = QComboBox()
        for method in METHODS:
            self.method_combo.addItem("", method)
        self._row(grid, "options.method", self.method_combo)
        self.verify_combo = QComboBox()
        for mode in VERIFY_MODES:
            self.verify_combo.addItem("", mode)
        self._row(grid, "options.verify", self.verify_combo)
        self.sk_combo = QComboBox()
        for level in (1, 2, 3):
            self.sk_combo.addItem("", level)
        self._row(grid, "options.class", self.sk_combo)
        return card

    def _choice_row(self, grid: QGridLayout, key: str, values, signal) -> dict:
        row = QHBoxLayout()
        row.setSpacing(18)
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons = {}
        for value in values:
            button = QRadioButton()
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _c=False, v=value: signal.emit(v))
            group.addButton(button)
            row.addWidget(button)
            buttons[value] = button
        row.addStretch(1)
        self._row(grid, key, row)
        return buttons

    def _build_look_section(self) -> QFrame:
        card, layout = self._section("settings.section_look", "settings.section_look_hint")
        grid = self._form(layout)
        self.theme_buttons = self._choice_row(grid, "settings.theme", THEMES,
                                              self.theme_requested)
        self.language_buttons = self._choice_row(grid, "settings.language", UI_LANGUAGES,
                                                 self.language_requested)
        return card

    def _build_demo_section(self) -> QFrame:
        card, layout = self._section("demo.title")
        self.demo_text = make_label("", "muted", wrap=True)
        layout.addWidget(self.demo_text)
        self.demo_button = _button()
        self.demo_button.clicked.connect(self.demo_requested.emit)
        layout.addWidget(self.demo_button, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _build_about_section(self) -> QFrame:
        card, layout = self._section("settings.section_about")
        self.version_label = make_label("", "tile-value-mono")
        self.about_text = make_label("", "muted", wrap=True)
        self.github_button = _button("link")
        self.github_button.clicked.connect(lambda: open_url(GITHUB_URL))
        layout.addWidget(self.version_label)
        layout.addWidget(self.about_text)
        layout.addWidget(self.github_button, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    # ── Load / save ───────────────────────────────────────────────────────────

    def load_from_config(self) -> None:
        cfg = self.config
        self.company_edit.setText(cfg.company.name)
        self.address_edit.setPlainText(cfg.company.address)
        self.logo_edit.setText(cfg.company.logo_path)
        self.cert_dir_edit.setText(cfg.cert_output_dir)
        _set_combo(self.cert_language_combo, cfg.cert_language)
        self.operator_edit.setText(cfg.operator_name)
        method = "quick" if cfg.default_wipe_method == "zero" else cfg.default_wipe_method
        _set_combo(self.method_combo, method)
        _set_combo(self.verify_combo, cfg.default_verify_mode)
        _set_combo(self.sk_combo, int(cfg.default_schutzklasse or 2))
        self.sync_appearance(getattr(cfg, "theme", "system"), getattr(cfg, "ui_language", "de"))
        self._status = None
        self.status_label.hide()

    def _show_status(self, key: str, tone: str, **values) -> None:
        self._status = (key, values, tone)
        self.status_label.setText(tr(key, **values))
        set_prop(self.status_label, "tone", tone)
        self.status_label.show()

    def save(self) -> bool:
        """Check and store the fields in ``AppConfig`` (theme and language are live)."""
        import core.config as settings

        name = self.company_edit.text().strip()
        address = self.address_edit.toPlainText().strip()
        operator = self.operator_edit.text().strip()
        if address and not name:
            self._show_status("settings.err_company", "danger")
            self.company_edit.setFocus()
            return False
        if operator and len(operator) < 2:
            self._show_status("settings.err_operator", "danger")
            self.operator_edit.setFocus()
            return False
        cfg = self.config
        cfg.company.name = name
        cfg.company.address = address
        cfg.company.logo_path = self.logo_edit.text().strip()
        cfg.cert_output_dir = self.cert_dir_edit.text().strip() or str(settings.DEFAULT_CERT_OUTPUT)
        cfg.cert_language = self.cert_language_combo.currentData() or "de"
        cfg.operator_name = operator
        cfg.default_wipe_method = self.method_combo.currentData() or "quick"
        cfg.default_verify_mode = self.verify_combo.currentData() or "full"
        cfg.default_schutzklasse = int(self.sk_combo.currentData() or 2)
        self.cert_dir_edit.setText(cfg.cert_output_dir)
        try:
            cfg.save()
        except OSError as exc:
            audit_log(f"Settings could not be saved: {exc}")
            self._show_status("settings.save_failed", "danger", error=str(exc))
            return False
        self._show_status("settings.saved", "ok")
        self.saved.emit()
        return True

    def sync_appearance(self, theme_setting: str, lang: str) -> None:
        """Show the window's current theme setting and UI language (no signals)."""
        button = self.theme_buttons.get(theme_setting) or self.theme_buttons["system"]
        button.setChecked(True)
        (self.language_buttons.get(lang) or self.language_buttons["de"]).setChecked(True)

    def _browse_logo(self) -> None:
        path = pick_image(self, tr("settings.pick_logo"), tr("settings.image_filter"))
        if path:
            self.logo_edit.setText(path)

    def _browse_cert_dir(self) -> None:
        path = pick_folder(self, tr("settings.pick_folder"), self.cert_dir_edit.text())
        if path:
            self.cert_dir_edit.setText(path)

    # ── Language and theme ────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self.title_label.setText(tr("settings.title"))
        self.subtitle_label.setText(tr("settings.subtitle"))
        self.save_button.setText(tr("settings.save"))
        self.reset_button.setText(tr("settings.reset"))
        for label, key in self._labels:
            label.setText(tr(key))
        self.company_edit.setPlaceholderText(tr("settings.company_placeholder"))
        self.logo_edit.setPlaceholderText(tr("settings.logo_placeholder"))
        self.operator_edit.setPlaceholderText(tr("options.operator_placeholder"))
        self.logo_browse_button.setText(tr("settings.browse"))
        self.logo_clear_button.setText(tr("settings.clear"))
        self.cert_dir_browse_button.setText(tr("settings.browse"))
        for i, lang in enumerate(CERT_LANGUAGES):
            self.cert_language_combo.setItemText(i, tr(f"settings.lang_{lang}"))
        for i, method in enumerate(METHODS):
            text = tr(_METHOD_TEXT[method])
            if method == "quick":
                text = f"{text} · {tr('note.recommended')}"
            self.method_combo.setItemText(i, text)
        for i, mode in enumerate(VERIFY_MODES):
            self.verify_combo.setItemText(i, tr(f"options.verify_{mode}"))
        for i, level in enumerate((1, 2, 3)):
            self.sk_combo.setItemText(i, tr(f"options.class_{level}"))
        for value, button in self.theme_buttons.items():
            button.setText(tr(f"settings.theme_{value}"))
        for value, button in self.language_buttons.items():
            button.setText(tr(f"settings.ui_{value}"))
        self.demo_text.setText(tr("settings.demo_text"))
        self.demo_button.setText(tr("demo.start"))
        self.version_label.setText(f"StickShredder · {tr('settings.version', version=APP_VERSION)}")
        self.about_text.setText(tr("about.text"))
        self.github_button.setText(tr("settings.github"))
        if self._status is not None:
            key, values, tone = self._status
            self.status_label.setText(tr(key, **values))

    def on_theme_changed(self) -> None:
        for widget in self.findChildren(QWidget):
            widget.update()
