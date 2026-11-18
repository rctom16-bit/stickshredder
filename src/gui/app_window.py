"""The StickShredder 2.0 window: 88 px rail on the left, pages on the right.

Löschen is the new page; Zertifikate opens the existing history dialog,
Optionen offers the existing settings dialog and the demo mode, Schlüssel is
a placeholder until the key management page arrives.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.config import APP_VERSION
from core.log import audit_log
from gui import i18n, theme
from gui.dialogs import ask_ok_cancel, ask_yes_no
from gui.history_dialog import HistoryDialog
from gui.i18n import tr
from gui.pages.delete_page import DeletePage
from gui.settings_dialog import SettingsDialog
from gui.theme import ThemeManager
from gui.widgets import Logo, RailButton, icon_pixmap, make_card, make_label
from gui.wipe_options import WipeOptions

WM_DEVICECHANGE = 0x0219
_DEVICE_EVENTS = {
    0x8000,  # DBT_DEVICEARRIVAL (a volume appeared)
    0x8004,  # DBT_DEVICEREMOVECOMPLETE
    0x0007,  # DBT_DEVNODES_CHANGED (also disks without a volume)
}


def is_device_change(message: int, wparam: int) -> bool:
    """True for the Windows broadcasts sent when a disk is plugged in or removed."""
    return message == WM_DEVICECHANGE and int(wparam) in _DEVICE_EVENTS


class KeysPlaceholderPage(QWidget):
    """„Schlüssel“ — replaced by the key management page in the next wave."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PageRoot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        card = make_card("surface")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(28, 28, 28, 28)
        inner.setSpacing(10)
        self.title_label = make_label("", "placeholder-title")
        self.text_label = make_label("", "muted", wrap=True)
        inner.addWidget(self.title_label)
        inner.addWidget(self.text_label)
        layout.addWidget(card)
        layout.addStretch(1)
        self.retranslate()

    def retranslate(self) -> None:
        self.title_label.setText(tr("keys.title"))
        self.text_label.setText(tr("keys.text"))


class AppWindow(QMainWindow):
    """Main window of StickShredder 2.0 (design direction „Signal“)."""

    DEVICE_CHANGE_DELAY_MS = 1200

    def __init__(self, config, theme_manager: ThemeManager | None = None, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        i18n.set_language(getattr(config, "ui_language", "de"))
        self.theme = theme_manager or ThemeManager(QApplication.instance(),
                                                   getattr(config, "theme", "system"))
        if theme_manager is None:
            self.theme.apply()
        self.theme.changed.connect(self._on_theme_changed)

        self.setWindowTitle(f"StickShredder {APP_VERSION}")
        self.setMinimumSize(1040, 720)
        self.resize(1280, 800)

        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.rail = self._build_rail()
        layout.addWidget(self.rail)

        self.pages = QStackedWidget()
        self.delete_page = DeletePage(config)
        self.keys_page = KeysPlaceholderPage()
        self.pages.addWidget(self.delete_page)
        self.pages.addWidget(self.keys_page)
        layout.addWidget(self.pages, 1)
        self.delete_page.busy_changed.connect(self._on_busy_changed)

        self._device_timer = QTimer(self)
        self._device_timer.setSingleShot(True)
        self._device_timer.setInterval(self.DEVICE_CHANGE_DELAY_MS)
        self._device_timer.timeout.connect(self._refresh_after_device_change)

        self.retranslate()
        self._on_theme_changed(self.theme.palette)
        self._show_page("delete")
        # Scan after the window is on screen (WMI takes a moment).
        QTimer.singleShot(200, self.delete_page.refresh_devices)

    # ── Rail ──────────────────────────────────────────────────────────────────

    def _build_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("Rail")
        rail.setFixedWidth(88)
        column = QVBoxLayout(rail)
        column.setContentsMargins(0, 20, 0, 20)
        column.setSpacing(8)
        column.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.logo = Logo()
        column.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(8)

        self.rail_buttons: dict[str, RailButton] = {}
        for key in ("delete", "certificates", "keys", "options"):
            button = RailButton(key)
            button.setCheckable(key in ("delete", "keys"))
            self.rail_buttons[key] = button
            column.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.rail_buttons["delete"].clicked.connect(lambda: self._show_page("delete"))
        self.rail_buttons["keys"].clicked.connect(lambda: self._show_page("keys"))
        self.rail_buttons["certificates"].clicked.connect(self.open_history)

        self.options_menu = QMenu(self)
        self.settings_action = QAction(self)
        self.settings_action.triggered.connect(self.open_settings)
        self.demo_action = QAction(self)
        self.demo_action.triggered.connect(self.start_demo_mode)
        self.about_action = QAction(self)
        self.about_action.triggered.connect(self.show_about)
        self.theme_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for setting in ("system", "light", "dark"):
            action = QAction(self)
            action.setCheckable(True)
            action.triggered.connect(lambda _c=False, s=setting: self.set_theme_setting(s))
            group.addAction(action)
            self.theme_actions[setting] = action
        self.options_menu.addAction(self.settings_action)
        self.options_menu.addAction(self.demo_action)
        self.options_menu.addSeparator()
        for action in self.theme_actions.values():
            self.options_menu.addAction(action)
        self.options_menu.addSeparator()
        self.options_menu.addAction(self.about_action)
        options = self.rail_buttons["options"]
        options.setMenu(self.options_menu)
        options.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        column.addStretch(1)
        self.language_button = QPushButton()
        self.language_button.setObjectName("LanguageToggle")
        self.language_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.language_button.clicked.connect(self.toggle_language)
        column.addWidget(self.language_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("ThemeToggle")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.setIconSize(QSize(18, 18))
        self.theme_button.clicked.connect(self.toggle_theme)
        self.theme_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.theme_button.customContextMenuRequested.connect(self._theme_context_menu)
        column.addWidget(self.theme_button, 0, Qt.AlignmentFlag.AlignHCenter)
        return rail

    def _show_page(self, name: str) -> None:
        page = self.keys_page if name == "keys" else self.delete_page
        self.pages.setCurrentWidget(page)
        self.rail_buttons["delete"].setChecked(name != "keys")
        self.rail_buttons["keys"].setChecked(name == "keys")

    def _on_busy_changed(self, busy: bool) -> None:
        """While a wipe runs the rail shows only „Löschen“ (as on the artboard)."""
        for key in ("certificates", "keys", "options"):
            self.rail_buttons[key].setVisible(not busy)
        if busy:
            self._show_page("delete")

    # ── Rail actions ──────────────────────────────────────────────────────────

    def open_history(self) -> None:
        HistoryDialog(self.config, self).exec()
        self.delete_page._update_preview()

    def open_settings(self) -> None:
        if SettingsDialog(self.config, self).exec():
            self.delete_page.set_options(WipeOptions.from_config(self.config))

    def show_about(self) -> None:
        QMessageBox.about(self, tr("menu.about"),
                          f"StickShredder {APP_VERSION}\n\n{tr('about.text')}")

    def confirm_demo(self) -> bool:
        return ask_ok_cancel(self, tr("demo.title"), tr("demo.text"), tr("demo.start"))

    def start_demo_mode(self) -> None:
        page = self.delete_page
        if page.is_busy() or not self.confirm_demo():
            return
        self._show_page("delete")
        page.stack.setCurrentWidget(page.select_view)
        page.add_demo_device()

    # ── Language and theme ────────────────────────────────────────────────────

    def _save_config(self) -> None:
        try:
            self.config.save()
        except OSError as exc:
            audit_log(f"Settings could not be saved: {exc}")

    def set_language(self, lang: str) -> None:
        i18n.set_language(lang)
        self.config.ui_language = i18n.language()
        self._save_config()
        self.retranslate()

    def toggle_language(self) -> None:
        self.set_language("de" if i18n.language() == "en" else "en")

    def set_theme_setting(self, setting: str) -> None:
        self.config.theme = setting if setting in theme.THEME_SETTINGS else "system"
        self._save_config()
        self.theme.set_setting(self.config.theme)

    def toggle_theme(self) -> None:
        self.set_theme_setting("light" if self.theme.mode == "dark" else "dark")

    def _theme_context_menu(self, pos) -> None:
        menu = QMenu(self)
        for action in self.theme_actions.values():
            menu.addAction(action)
        menu.exec(self.theme_button.mapToGlobal(pos))

    def retranslate(self) -> None:
        for key, button in self.rail_buttons.items():
            button.setText(tr(f"nav.{key}"))
        self.rail.setAccessibleName(tr("rail.main_menu"))
        self.settings_action.setText(tr("menu.settings"))
        self.demo_action.setText(tr("menu.demo"))
        self.about_action.setText(tr("menu.about"))
        for setting, action in self.theme_actions.items():
            action.setText(tr(f"menu.theme_{setting}"))
        self.language_button.setText(tr("rail.language_button"))
        self.language_button.setToolTip(tr("rail.language_tip"))
        self._update_theme_button()
        self.delete_page.retranslate()
        self.keys_page.retranslate()

    def _update_theme_button(self) -> None:
        p = self.theme.palette
        dark = self.theme.mode == "dark"
        self.theme_button.setIcon(icon_pixmap("sun" if dark else "moon", 18, p.text))
        tip = tr("rail.theme_to_light") if dark else tr("rail.theme_to_dark")
        self.theme_button.setToolTip(f"{tip}\n{tr('rail.theme_hint')}")
        self.theme_button.setAccessibleName(tip)
        for setting, action in self.theme_actions.items():
            action.setChecked(setting == self.theme.setting)

    def _on_theme_changed(self, _palette) -> None:
        for button in self.rail_buttons.values():
            button.refresh_icon()
        self._update_theme_button()
        self.delete_page.on_theme_changed()
        self.logo.update()

    # ── Disk plug / unplug ────────────────────────────────────────────────────

    def handle_device_change(self) -> None:
        """Debounce Windows device broadcasts into one rescan."""
        self._device_timer.start()

    def _refresh_after_device_change(self) -> None:
        page = self.delete_page
        if page.is_busy() or page.current_view() != "select":
            return
        page.refresh_devices()

    def nativeEvent(self, event_type, message):  # noqa: N802 — Qt name
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if is_device_change(msg.message, msg.wParam):
                    self.handle_device_change()
        except (TypeError, ValueError, OverflowError):
            pass
        return super().nativeEvent(event_type, message)

    # ── Closing ───────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        worker = self.delete_page.worker
        if self.delete_page.is_busy():
            if not ask_yes_no(self, tr("msg.busy_title"), tr("msg.busy_text")):
                event.ignore()
                return
            worker.cancel()
            worker.wait(5000)
        event.accept()
