"""The StickShredder 2.0 window: 88 px rail on the left, pages on the right.

Four pages: Löschen (the wipe flow), Zertifikate (history, certificate check),
Schlüssel (signing key, key card, trusted keys) and Optionen (settings,
appearance, demo mode, about). The language and theme toggles at the bottom
of the rail and the Optionen page stay in sync.
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
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import APP_VERSION
from core.log import audit_log
from gui import i18n, theme
from gui.dialogs import ask_ok_cancel, ask_yes_no, show_message
from gui.i18n import tr
from gui.pages.certificates_page import CertificatesPage
from gui.pages.delete_page import DeletePage
from gui.pages.keys_page import KeysPage
from gui.pages.options_page import OptionsPage
from gui.theme import ThemeManager
from gui.widgets import Logo, RailButton, icon_pixmap
from gui.wipe_options import WipeOptions
from wipe.hardware_erase import TEMP_PASSWORD

WM_DEVICECHANGE = 0x0219
_DEVICE_EVENTS = {
    0x8000,  # DBT_DEVICEARRIVAL (a volume appeared)
    0x8004,  # DBT_DEVICEREMOVECOMPLETE
    0x0007,  # DBT_DEVNODES_CHANGED (also disks without a volume)
}


def is_device_change(message: int, wparam: int) -> bool:
    """True for the Windows broadcasts sent when a disk is plugged in or removed."""
    return message == WM_DEVICECHANGE and int(wparam) in _DEVICE_EVENTS


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
        self.certificates_page = CertificatesPage(config)
        self.keys_page = KeysPage(config)
        self.options_page = OptionsPage(config)
        self._pages = {
            "delete": self.delete_page,
            "certificates": self.certificates_page,
            "keys": self.keys_page,
            "options": self.options_page,
        }
        for page in self._pages.values():
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.delete_page.busy_changed.connect(self._on_busy_changed)
        self.options_page.saved.connect(self._on_options_saved)
        self.options_page.theme_requested.connect(self.set_theme_setting)
        self.options_page.language_requested.connect(self.set_language)
        self.options_page.demo_requested.connect(self.start_demo_mode)
        self.options_page.sync_appearance(self.theme.setting, i18n.language())

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
            button.clicked.connect(lambda _c=False, k=key: self._show_page(k))
            self.rail_buttons[key] = button
            column.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)

        # Right-click on the theme toggle: light / dark / like Windows.
        self.theme_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for setting in ("system", "light", "dark"):
            action = QAction(self)
            action.setCheckable(True)
            action.triggered.connect(lambda _c=False, s=setting: self.set_theme_setting(s))
            group.addAction(action)
            self.theme_actions[setting] = action

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
        page = self._pages.get(name, self.delete_page)
        if page is self.certificates_page:
            self.certificates_page.refresh()   # a wipe may have added an entry
        elif page is self.keys_page:
            self.keys_page.refresh()
        self.pages.setCurrentWidget(page)
        for key, button in self.rail_buttons.items():
            button.setChecked(self._pages[key] is page)

    def current_page(self) -> str:
        current = self.pages.currentWidget()
        return next(k for k, page in self._pages.items() if page is current)

    def _on_busy_changed(self, busy: bool) -> None:
        """While a wipe runs the rail shows only „Löschen“ (as on the artboard)."""
        for key in ("certificates", "keys", "options"):
            self.rail_buttons[key].setVisible(not busy)
        if busy:
            self._show_page("delete")

    # ── Page actions ──────────────────────────────────────────────────────────

    def _on_options_saved(self) -> None:
        """New defaults reach the Löschen page; the company name the key card."""
        if not self.delete_page.is_busy():
            self.delete_page.set_options(WipeOptions.from_config(self.config))
        self.keys_page.refresh()

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
        self.options_page.sync_appearance(self.theme.setting, i18n.language())

    def toggle_language(self) -> None:
        self.set_language("de" if i18n.language() == "en" else "en")

    def set_theme_setting(self, setting: str) -> None:
        self.config.theme = setting if setting in theme.THEME_SETTINGS else "system"
        self._save_config()
        self.theme.set_setting(self.config.theme)
        self.options_page.sync_appearance(self.theme.setting, i18n.language())

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
        for setting, action in self.theme_actions.items():
            action.setText(tr(f"menu.theme_{setting}"))
        self.language_button.setText(tr("rail.language_button"))
        self.language_button.setToolTip(tr("rail.language_tip"))
        self._update_theme_button()
        for page in self._pages.values():
            page.retranslate()

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
        for page in self._pages.values():
            page.on_theme_changed()
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
        page = self.delete_page
        if page.running_view.is_erasing():
            # The drive's own erase cannot be stopped; quitting changes nothing
            # for the drive but hides what happens to it.
            show_message(self, tr("msg.erasing_title"),
                         tr("msg.erasing_text", password=TEMP_PASSWORD), kind="warning")
            event.ignore()
            return
        if page.is_busy():
            if not ask_yes_no(self, tr("msg.busy_title"), tr("msg.busy_text")):
                event.ignore()
                return
            page.stop_work()
        event.accept()
