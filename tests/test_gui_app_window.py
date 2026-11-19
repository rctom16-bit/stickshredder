"""The new application window: rail, language, theme, device refresh, demo flow."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QTimer  # noqa: E402

from tests.gui_fixtures import (  # noqa: E402,F401
    app_config,
    gui_reset,
    isolated_profile,
    make_device,
)


@pytest.fixture()
def window(qtbot, app_config, gui_reset, monkeypatch):
    import gui.pages.delete_page as delete_page
    from gui.app_window import AppWindow

    monkeypatch.setattr(delete_page, "list_devices", lambda: [make_device("F:", 1)])
    monkeypatch.setattr(delete_page, "media_type_of", lambda d: "flash")
    win = AppWindow(app_config)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitUntil(lambda: win.delete_page.selected_device is not None, timeout=3000)
    return win


def test_rail_has_the_four_items_and_starts_on_delete(window):
    assert [b.text() for b in window.rail_buttons.values()] == [
        "Löschen", "Zertifikate", "Schlüssel", "Optionen"]
    assert window.rail_buttons["delete"].isChecked()
    assert window.pages.currentWidget() is window.delete_page
    assert window.rail.width() == 88


@pytest.mark.parametrize("key, attr", [
    ("certificates", "certificates_page"),
    ("keys", "keys_page"),
    ("options", "options_page"),
    ("delete", "delete_page"),
])
def test_every_rail_item_opens_its_page(qtbot, window, key, attr):
    qtbot.mouseClick(window.rail_buttons["options"], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.rail_buttons[key], Qt.MouseButton.LeftButton)
    assert window.pages.currentWidget() is getattr(window, attr)
    assert [k for k, b in window.rail_buttons.items() if b.isChecked()] == [key]


def test_rail_has_no_menus_or_dialogs_left(window):
    for button in window.rail_buttons.values():
        assert button.menu() is None
    assert not hasattr(window, "options_menu")
    assert not hasattr(window, "open_history")
    assert not hasattr(window, "open_settings")


def test_certificates_page_reloads_the_history_when_opened(qtbot, window):
    from core.log import log_wipe_to_csv

    assert window.certificates_page.table.rowCount() == 0
    log_wipe_to_csv({"date": "2026-11-18 12:00:00", "device_model": "SanDisk",
                     "serial_number": "123", "result": "OK", "cert_number": 1})
    qtbot.mouseClick(window.rail_buttons["certificates"], Qt.MouseButton.LeftButton)
    assert window.certificates_page.table.rowCount() == 1


def test_saving_options_updates_the_delete_page(window):
    page = window.options_page
    page.method_combo.setCurrentIndex(page.method_combo.findData("bsi"))
    page.verify_combo.setCurrentIndex(page.verify_combo.findData("sample"))
    page.save_button.click()
    assert window.delete_page.options.method_key == "bsi"
    assert window.delete_page.options.verify_mode == "sample"


def test_options_page_and_rail_share_theme_and_language(qtbot, window):
    page = window.options_page
    page.theme_buttons["light"].click()
    assert (window.config.theme, window.theme.mode) == ("light", "light")
    page.language_buttons["en"].click()
    assert window.rail_buttons["options"].text() == "Options"
    assert page.save_button.text() == "Save"
    qtbot.mouseClick(window.theme_button, Qt.MouseButton.LeftButton)
    assert page.theme_buttons["dark"].isChecked()
    qtbot.mouseClick(window.language_button, Qt.MouseButton.LeftButton)
    assert page.language_buttons["de"].isChecked()


def test_language_switch_changes_visible_labels_and_is_saved(qtbot, window, isolated_profile):
    assert window.delete_page.delete_button.text() == "Jetzt sicher löschen"
    qtbot.mouseClick(window.language_button, Qt.MouseButton.LeftButton)
    assert window.delete_page.delete_button.text() == "Securely delete now"
    assert window.rail_buttons["delete"].text() == "Delete"
    assert window.config.ui_language == "en"
    saved = json.loads((isolated_profile / "config.json").read_text(encoding="utf-8"))
    assert saved["ui_language"] == "en"
    qtbot.mouseClick(window.language_button, Qt.MouseButton.LeftButton)
    assert window.rail_buttons["delete"].text() == "Löschen"


def test_theme_button_switches_live_and_is_saved(qtbot, window, isolated_profile):
    before = window.theme.mode
    qtbot.mouseClick(window.theme_button, Qt.MouseButton.LeftButton)
    after = window.theme.mode
    assert after != before
    assert window.config.theme == after
    saved = json.loads((isolated_profile / "config.json").read_text(encoding="utf-8"))
    assert saved["theme"] == after


def test_theme_can_go_back_to_following_windows(window):
    window.set_theme_setting("dark")
    window.set_theme_setting("system")
    assert window.config.theme == "system"
    assert window.theme.setting == "system"


def test_usb_plug_triggers_a_debounced_refresh(qtbot, window):
    from gui.app_window import is_device_change

    assert is_device_change(0x0219, 0x8000)      # DBT_DEVICEARRIVAL
    assert is_device_change(0x0219, 0x8004)      # DBT_DEVICEREMOVECOMPLETE
    assert is_device_change(0x0219, 0x0007)      # DBT_DEVNODES_CHANGED
    assert not is_device_change(0x0219, 0x0018)  # DBT_CONFIGCHANGED
    assert not is_device_change(0x0010, 0x8000)  # not WM_DEVICECHANGE

    calls = []
    window.delete_page.refresh_devices = lambda: calls.append(1)
    for _ in range(3):
        window.handle_device_change()
    qtbot.waitUntil(lambda: calls == [1], timeout=4000)
    qtbot.wait(300)
    assert calls == [1]


def test_no_refresh_while_a_wipe_runs(qtbot, window):
    calls = []
    window.delete_page.refresh_devices = lambda: calls.append(1)
    window.delete_page.is_busy = lambda: True
    try:
        window.handle_device_change()
        qtbot.wait(window.DEVICE_CHANGE_DELAY_MS + 300)
        assert calls == []
    finally:
        del window.delete_page.is_busy  # closing the window must not ask


def _confirm_when_asked(qtbot, word="LÖSCHEN"):
    """Type the confirmation word into the next modal dialog and confirm."""
    from PySide6.QtWidgets import QApplication

    from gui.dialogs import ConfirmWipeDialog

    def answer():
        dlg = QApplication.activeModalWidget()
        if not isinstance(dlg, ConfirmWipeDialog):
            QTimer.singleShot(20, answer)
            return
        dlg.word_edit.setText(word)
        dlg.confirm_button.click()

    QTimer.singleShot(0, answer)


def test_demo_flow_reaches_the_successful_result(qtbot, window, monkeypatch):
    from gui.app_window import AppWindow

    monkeypatch.setattr(AppWindow, "confirm_demo", lambda self: True)
    qtbot.mouseClick(window.rail_buttons["options"], Qt.MouseButton.LeftButton)
    window.options_page.demo_button.click()
    assert window.pages.currentWidget() is window.delete_page
    page = window.delete_page
    assert page.selected_device.device_id.startswith("\\\\.\\DemoDevice")

    _confirm_when_asked(qtbot)
    qtbot.mouseClick(page.delete_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.current_view() == "result", timeout=60000)

    view = page.result_view
    assert view.eyebrow_label.text() == "Ergebnis: Erfolgreich"
    assert view.panel.property("tone") == "ok"
    assert view.qr.has_code()
    assert view.fingerprint_label.text().startswith("Schlüssel ")
    assert view.cert_path and view.cert_path.endswith(".pdf")
    qtbot.waitUntil(lambda: page.worker is None, timeout=10000)

    # "Nächsten Datenträger löschen" goes back to the selection.
    qtbot.mouseClick(view.next_button, Qt.MouseButton.LeftButton)
    assert page.current_view() == "select"


def test_closing_during_a_wipe_asks_first(qtbot, window, monkeypatch):
    import gui.app_window as app_window

    class Busy:
        def isRunning(self):  # noqa: N802
            return True

        def cancel(self):
            self.cancelled = True

        def wait(self, _ms):
            return True

    window.delete_page.worker = Busy()
    monkeypatch.setattr(app_window, "ask_yes_no", lambda *a, **k: False)
    assert window.close() is False
    window.delete_page.worker = None


def test_native_device_broadcast_reaches_the_debounce(window):
    import ctypes
    import ctypes.wintypes

    import shiboken6
    from PySide6.QtCore import QByteArray

    msg = ctypes.wintypes.MSG()
    msg.message = 0x0219
    msg.wParam = 0x8000
    window.nativeEvent(QByteArray(b"windows_generic_MSG"),
                       shiboken6.VoidPtr(ctypes.addressof(msg)))
    assert window._device_timer.isActive()
    window._device_timer.stop()
