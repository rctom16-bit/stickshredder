"""Dialogs of the new window: wipe options, the typed confirmation, small messages."""

from __future__ import annotations

import html
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui import theme
from gui.i18n import fmt_size, language, pick, serial_label, tr
from gui.widgets import make_card, make_label
from gui.wipe_options import FILESYSTEMS, METHOD_KEYS, MULTI_PASS_KEYS, WipeOptions
from wipe.device import DeviceInfo
from wipe.recommend import MULTI_PASS_NOTE_DE, MULTI_PASS_NOTE_EN

CONFIRM_WORD_DE = "LÖSCHEN"
CONFIRM_WORD_EN = "DELETE"


def disk_letters_text(device: DeviceInfo) -> str:
    letters = device.disk_letters or ([device.drive_letter] if device.drive_letter else [])
    return ", ".join(letters)


def _button(text: str, variant: str = "", parent=None) -> QPushButton:
    button = QPushButton(text, parent)
    if variant:
        button.setProperty("variant", variant)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


# -- Options -------------------------------------------------------------------------


class WipeOptionsDialog(QDialog):
    """„Andere Methode“: method, protection class, operator, verification, format."""

    def __init__(self, options: WipeOptions, recommended_method: str = "zero",
                 parent=None) -> None:
        super().__init__(parent)
        self._options = options
        self._recommended = recommended_method
        self.setWindowTitle(tr("options.title"))
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)
        root.addWidget(make_label(tr("options.title"), "dialog-title"))

        # Method
        root.addWidget(make_label(tr("options.method"), "field-label"))
        self.method_group = QButtonGroup(self)
        self.method_buttons: dict[str, QRadioButton] = {}
        methods = QVBoxLayout()
        methods.setSpacing(8)
        for key in METHOD_KEYS:
            radio = QRadioButton(tr(f"options.{key}"))
            self.method_group.addButton(radio)
            self.method_buttons[key] = radio
            methods.addWidget(radio)
            radio.toggled.connect(self._on_method_toggled)
        root.addLayout(methods)

        custom = QHBoxLayout()
        custom.setSpacing(10)
        custom.setContentsMargins(28, 0, 0, 0)
        self.custom_passes_spin = QSpinBox()
        self.custom_passes_spin.setRange(1, 35)
        self.custom_passes_spin.setValue(options.custom_passes)
        self.custom_pattern_combo = QComboBox()
        for key in ("zero", "ones", "random"):
            self.custom_pattern_combo.addItem(tr(f"pattern.{key}"), key)
        self.custom_pattern_combo.setCurrentIndex(
            max(0, self.custom_pattern_combo.findData(options.custom_pattern)))
        custom.addWidget(make_label(tr("options.passes"), "hint"))
        custom.addWidget(self.custom_passes_spin)
        custom.addSpacing(8)
        custom.addWidget(make_label(tr("options.pattern"), "hint"))
        custom.addWidget(self.custom_pattern_combo, 1)
        root.addLayout(custom)

        self.method_note = make_label("", "hint", wrap=True)
        root.addWidget(self.method_note)

        # Protection class, operator
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        grid.addWidget(make_label(tr("options.class"), "field-label"), 0, 0)
        grid.addWidget(make_label(tr("options.operator"), "field-label"), 0, 1)
        self.sk_combo = QComboBox()
        for sk in (1, 2, 3):
            self.sk_combo.addItem(tr(f"options.class_{sk}"), sk)
        self.sk_combo.setCurrentIndex(max(0, self.sk_combo.findData(options.schutzklasse)))
        self.operator_edit = QLineEdit(options.operator)
        self.operator_edit.setPlaceholderText(tr("options.operator_placeholder"))
        grid.addWidget(self.sk_combo, 1, 0)
        grid.addWidget(self.operator_edit, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        # Verification
        root.addWidget(make_label(tr("options.verify"), "field-label"))
        self.verify_full_radio = QRadioButton(tr("options.verify_full"))
        self.verify_sample_radio = QRadioButton(tr("options.verify_sample"))
        verify_group = QButtonGroup(self)
        verify_group.addButton(self.verify_full_radio)
        verify_group.addButton(self.verify_sample_radio)
        (self.verify_sample_radio if options.verify_mode == "sample"
         else self.verify_full_radio).setChecked(True)
        verify = QVBoxLayout()
        verify.setSpacing(8)
        verify.addWidget(self.verify_full_radio)
        verify.addWidget(self.verify_sample_radio)
        root.addLayout(verify)

        # Format
        root.addWidget(make_label(tr("options.format"), "field-label"))
        fmt = QHBoxLayout()
        fmt.setSpacing(10)
        self.reformat_check = QCheckBox(tr("options.format_check"))
        self.fs_combo = QComboBox()
        for fs in FILESYSTEMS:
            self.fs_combo.addItem(tr(f"options.fs_{fs}"), fs)
        self.label_edit = QLineEdit(options.reformat_label or "USB")
        self.label_edit.setMaxLength(32)
        self.label_edit.setPlaceholderText(tr("options.label"))
        if options.reformat in FILESYSTEMS:
            self.reformat_check.setChecked(True)
            self.fs_combo.setCurrentIndex(self.fs_combo.findData(options.reformat))
        fmt.addWidget(self.reformat_check)
        fmt.addWidget(self.fs_combo, 1)
        fmt.addWidget(self.label_edit, 1)
        root.addLayout(fmt)
        self.reformat_check.toggled.connect(self._sync_enabled)

        # Buttons
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = _button(tr("common.cancel"))
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button = _button(tr("options.apply"), "accent")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.apply_button)
        root.addLayout(buttons)

        self.method_buttons.get(options.method_key, self.method_buttons["zero"]).setChecked(True)
        self._sync_enabled()

    def note_for(self, method_key: str) -> str:
        if method_key in MULTI_PASS_KEYS:
            return pick(MULTI_PASS_NOTE_DE, MULTI_PASS_NOTE_EN)
        if method_key == "custom":
            return tr("options.note_custom")
        if method_key == self._recommended:
            return tr("options.note_recommended")
        return ""

    def _checked_method(self) -> str:
        for key, radio in self.method_buttons.items():
            if radio.isChecked():
                return key
        return "zero"

    def _on_method_toggled(self, _checked: bool) -> None:
        self._sync_enabled()

    def _sync_enabled(self) -> None:
        if not hasattr(self, "reformat_check"):
            return
        key = self._checked_method()
        custom = key == "custom"
        self.custom_passes_spin.setEnabled(custom)
        self.custom_pattern_combo.setEnabled(custom)
        self.method_note.setText(self.note_for(key))
        self.method_note.setProperty("role", "warning" if key in MULTI_PASS_KEYS else "hint")
        self.method_note.style().unpolish(self.method_note)
        self.method_note.style().polish(self.method_note)
        on = self.reformat_check.isChecked()
        self.fs_combo.setEnabled(on)
        self.label_edit.setEnabled(on)

    def options(self) -> WipeOptions:
        reformat = self.fs_combo.currentData() if self.reformat_check.isChecked() else "none"
        return replace(
            self._options,
            method_key=self._checked_method(),
            custom_passes=self.custom_passes_spin.value(),
            custom_pattern=self.custom_pattern_combo.currentData() or "zero",
            schutzklasse=int(self.sk_combo.currentData() or 2),
            operator=self.operator_edit.text().strip(),
            verify_mode="sample" if self.verify_sample_radio.isChecked() else "full",
            reformat=reformat or "none",
            reformat_label=self.label_edit.text().strip() or "USB",
        )


# -- Confirmation ------------------------------------------------------------------


class ConfirmWipeDialog(QDialog):
    """Names every drive letter, model, serial and size; needs the typed word."""

    def __init__(self, devices: list[DeviceInfo], warnings: list[str] | tuple = (),
                 parent=None) -> None:
        super().__init__(parent)
        self._devices = list(devices)
        self.setWindowTitle(tr("confirm.window_title"))
        self.setMinimumWidth(560)
        mono = theme.fonts().mono

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        self.title_label = make_label(tr("confirm.title"), "dialog-title", wrap=True)
        root.addWidget(self.title_label)
        root.addWidget(make_label(tr("confirm.text"), "muted", wrap=True))

        box = make_card("tile")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(16, 14, 16, 14)
        box_layout.setSpacing(6)
        self._disk_lines: list[str] = []
        for device in self._devices:
            letters = disk_letters_text(device) or tr("confirm.no_letter")
            serial = serial_label(device.serial_number)
            size = fmt_size(device.capacity_bytes)
            self._disk_lines.append(f"{letters} · {device.model} · {serial} · {size}")
            label = make_label("", "dialog-disk", wrap=True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setText(
                f"<b>{html.escape(letters)}</b> · {html.escape(device.model)} · "
                f"<span style=\"font-family:'{mono}'\">{html.escape(serial)}</span>"
                f" · {html.escape(size)}"
            )
            box_layout.addWidget(label)
        root.addWidget(box)

        for warning in warnings:
            root.addWidget(make_label(warning, "warning", wrap=True))

        root.addWidget(make_label(tr("confirm.prompt"), "field-label"))
        self.word_edit = QLineEdit()
        self.word_edit.setProperty("role", "confirm-word")
        self.word_edit.setPlaceholderText(tr("confirm.placeholder"))
        self.word_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.word_edit.textChanged.connect(self._sync)
        self.word_edit.returnPressed.connect(self.accept)
        root.addWidget(self.word_edit)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = _button(tr("common.cancel"))
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = _button(tr("confirm.button"), "danger")
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)
        root.addLayout(buttons)
        self._sync()
        self.word_edit.setFocus()

    def accepted_words(self) -> set[str]:
        words = {CONFIRM_WORD_DE}
        if language() == "en":
            words.add(CONFIRM_WORD_EN)
        return words

    def is_word_ok(self) -> bool:
        return self.word_edit.text().strip().upper() in self.accepted_words()

    def _sync(self) -> None:
        self.confirm_button.setEnabled(self.is_word_ok())

    def accept(self) -> None:
        if not self.is_word_ok():
            return
        super().accept()

    def summary_text(self) -> str:
        return "\n".join(self._disk_lines)


def ask_confirmation(parent: QWidget | None, devices: list[DeviceInfo],
                     warnings: list[str] | tuple = ()) -> bool:
    dialog = ConfirmWipeDialog(devices, warnings, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


# -- Small messages -----------------------------------------------------------------


def ask_yes_no(parent: QWidget | None, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(tr("common.yes"), QMessageBox.ButtonRole.YesRole)
    no = box.addButton(tr("common.no"), QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(no)
    box.exec()
    return box.clickedButton() is yes


def ask_ok_cancel(parent: QWidget | None, title: str, text: str, ok_text: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setText(text)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(tr("common.cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(ok)
    box.exec()
    return box.clickedButton() is ok


def show_message(parent: QWidget | None, title: str, text: str, kind: str = "info") -> None:
    box = QMessageBox(parent)
    box.setIcon({"warning": QMessageBox.Icon.Warning,
                 "error": QMessageBox.Icon.Critical}.get(kind, QMessageBox.Icon.Information))
    box.setWindowTitle(title)
    box.setText(text)
    box.addButton(tr("common.ok"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()
