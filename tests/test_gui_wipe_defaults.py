"""Wipe defaults that the old main window's tests covered, now checked on the
Löschen page and the options dialog (the old window is gone)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from tests.gui_fixtures import (  # noqa: E402,F401
    app_config,
    gui_reset,
    isolated_profile,
    make_device,
)


@pytest.fixture()
def page(qtbot, app_config, gui_reset):
    from gui.pages.delete_page import DeletePage

    widget = DeletePage(app_config, list_devices_fn=lambda: [], media_fn=lambda d: "flash")
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_full_verification_is_the_default_and_internal_disks_stay_locked(page):
    assert page.options.verify_mode == "full"
    assert not page.allow_internal_checkbox.isChecked()
    page.set_devices([make_device("F:", 1)])
    assert page.verify_tile.value_text() == "Jeder Sektor"


def test_full_verification_adds_one_read_to_the_estimate():
    from dataclasses import replace

    from gui.wipe_options import ESTIMATE_MBPS, WipeOptions, estimate_seconds

    size = 8 * 1024 ** 3
    full = WipeOptions(verify_mode="full")
    sample = replace(full, verify_mode="sample")
    one_read = size / (1024 * 1024) / ESTIMATE_MBPS
    assert estimate_seconds(full, size) == pytest.approx(estimate_seconds(sample, size) + one_read)


def test_format_controls_follow_their_checkbox(qtbot, gui_reset):
    from gui.dialogs import WipeOptionsDialog
    from gui.wipe_options import WipeOptions

    dlg = WipeOptionsDialog(WipeOptions())
    qtbot.addWidget(dlg)
    assert not dlg.reformat_check.isChecked()
    assert not dlg.fs_combo.isEnabled() and not dlg.label_edit.isEnabled()
    assert [dlg.fs_combo.itemData(i) for i in range(dlg.fs_combo.count())] == [
        "exfat", "fat32", "ntfs"]
    assert dlg.fs_combo.currentData() == "exfat"
    assert dlg.label_edit.text() == "USB"
    dlg.reformat_check.setChecked(True)
    assert dlg.fs_combo.isEnabled() and dlg.label_edit.isEnabled()
    assert dlg.options().reformat == "exfat"
    dlg.reformat_check.setChecked(False)
    assert not dlg.fs_combo.isEnabled()
    assert dlg.options().reformat == "none"
