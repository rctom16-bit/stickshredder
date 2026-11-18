"""The Löschen page: disk selection, options, confirmation, progress and result."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from tests.gui_fixtures import (  # noqa: E402,F401
    app_config,
    gui_reset,
    isolated_profile,
    make_device,
)


@pytest.fixture()
def page(qtbot, app_config, gui_reset):
    from gui.pages.delete_page import DeletePage
    from gui.theme import ThemeManager

    ThemeManager(gui_reset, "dark").apply()
    widget = DeletePage(app_config, list_devices_fn=lambda: [], media_fn=lambda d: "flash")
    qtbot.addWidget(widget)
    widget.resize(1192, 800)
    widget.show()
    return widget


# -- Selection -----------------------------------------------------------------


def test_first_removable_disk_is_selected(page):
    system = make_device("C:", 0, "SK hynix PVC10", connection="NVMe",
                         removable=False, system=True, internal=True)
    stick = make_device("F:", 1)
    page.set_devices([system, stick])
    assert page.selected_device is stick
    assert page.hero_letter.text() == "F:"
    assert "SN 0901C3F2CFA4" in page.hero_subtitle.text()
    assert page.delete_button.isEnabled()


def test_system_disk_row_is_not_selectable(qtbot, page):
    system = make_device("C:", 0, "SK hynix PVC10", connection="NVMe",
                         removable=False, system=True, internal=True)
    stick = make_device("F:", 1)
    page.set_devices([system, stick])
    row = page.row_for(system)
    assert not row.is_selectable()
    assert "Systemplatte · gesperrt" in row.chip_texts()
    qtbot.mouseClick(row, Qt.MouseButton.LeftButton)
    assert page.selected_device is stick
    assert page.select_device(system) is False
    assert page.selected_device is stick


def test_only_a_system_disk_leaves_nothing_to_delete(page):
    system = make_device("C:", 0, removable=False, system=True, internal=True)
    page.set_devices([system])
    assert page.selected_device is None
    assert page.hero_letter.text() == "—"
    assert not page.delete_button.isEnabled()


def test_internal_disk_needs_the_opt_in(qtbot, page):
    internal = make_device("", 2, "WDC WD10EZEX", connection="SATA",
                           removable=False, internal=True)
    page.set_devices([internal])
    assert page.selected_device is None
    assert not page.row_for(internal).is_selectable()
    qtbot.mouseClick(page.row_for(internal), Qt.MouseButton.LeftButton)
    assert page.selected_device is None

    page.allow_internal_checkbox.setChecked(True)
    row = page.row_for(internal)
    assert row.is_selectable()
    qtbot.mouseClick(row, Qt.MouseButton.LeftButton)
    assert page.selected_device is internal
    assert page.hero_letter.text() == "—"  # no drive letter

    page.allow_internal_checkbox.setChecked(False)
    assert page.selected_device is None


def test_one_row_per_physical_disk(page):
    e = make_device("E:", 1)
    g = make_device("G:", 1)
    other = make_device("H:", 3, "Kingston DT", serial="AA11")
    page.set_devices([e, g, other])
    ids = [r.device.device_id for r in page.disk_rows()]
    assert len(ids) == len(set(ids))
    assert page.selected_device.device_id.endswith("PhysicalDrive1")


def test_selected_disk_moves_out_of_the_list(qtbot, page):
    a = make_device("E:", 1)
    b = make_device("H:", 3, "Kingston DT", serial="AA11")
    page.set_devices([a, b])
    assert [r.device for r in page.disk_rows()] == [b]
    qtbot.mouseClick(page.row_for(b), Qt.MouseButton.LeftButton)
    assert page.selected_device is b
    assert [r.device for r in page.disk_rows()] == [a]


def test_tiles_show_the_recommendation(page):
    page.set_devices([make_device("F:", 1)])
    assert page.method_tile.value_text() == "1× Nullen"
    assert page.method_tile.note_text() == "Empfohlen"
    assert page.verify_tile.value_text() == "Jeder Sektor"
    assert page.class_tile.value_text() == "NIST Clear"
    assert "Minute" in page.estimate_label.text()
    assert "LÖSCHEN" in page.estimate_label.text()


def test_flash_with_schutzklasse_3_shows_destroy_warning(page):
    from dataclasses import replace

    page.set_options(replace(page.options, schutzklasse=3))
    page.set_devices([make_device("F:", 1)])
    assert page.warning_label.isVisible()
    assert "physisch vernichten" in page.warning_label.text()


def test_refresh_uses_the_device_lister(app_config, qtbot, gui_reset):
    from gui.pages.delete_page import DeletePage

    calls = []

    def lister():
        calls.append(1)
        return [make_device("F:", 1)]

    page = DeletePage(app_config, list_devices_fn=lister, media_fn=lambda d: "flash")
    qtbot.addWidget(page)
    page.refresh_devices()
    assert calls == [1]
    assert page.selected_device is not None


# -- Options -------------------------------------------------------------------


def test_options_dialog_returns_the_choice(qtbot, page):
    from gui.dialogs import WipeOptionsDialog

    dlg = WipeOptionsDialog(page.options, recommended_method="zero")
    qtbot.addWidget(dlg)
    dlg.method_buttons["bsi"].setChecked(True)
    dlg.verify_sample_radio.setChecked(True)
    dlg.sk_combo.setCurrentIndex(2)
    dlg.operator_edit.setText("Robin")
    dlg.reformat_check.setChecked(True)
    dlg.fs_combo.setCurrentIndex(dlg.fs_combo.findData("ntfs"))
    dlg.label_edit.setText("ARCHIV")
    opts = dlg.options()
    assert (opts.method_key, opts.verify_mode, opts.schutzklasse, opts.operator) == (
        "bsi", "sample", 3, "Robin")
    assert (opts.reformat, opts.reformat_label) == ("ntfs", "ARCHIV")
    assert "nur wenn eine Vorgabe es verlangt" in dlg.note_for("bsi")


def test_multi_pass_method_is_not_called_recommended(page):
    from dataclasses import replace

    page.set_devices([make_device("F:", 1)])
    page.set_options(replace(page.options, method_key="standard"))
    assert page.method_tile.value_text() == "3× Zufall"
    assert page.method_tile.note_text() != "Empfohlen"


def test_options_reach_the_worker(page, monkeypatch):
    from dataclasses import replace

    import gui.pages.delete_page as delete_page
    from wipe.methods import BsiVsitr

    captured = {}

    class FakeWorker:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            for name in ("progress_updated", "verify_progress", "phase_changed",
                         "device_completed", "all_completed", "error",
                         "status_message", "stall_detected"):
                setattr(self, name, _FakeSignal())

        def start(self):
            captured["started"] = True

        def isRunning(self):  # noqa: N802 — Qt name
            return False

    monkeypatch.setattr(delete_page, "WipeWorker", FakeWorker)
    stick = make_device("F:", 1)
    page.set_devices([stick])
    page.set_options(replace(
        page.options, method_key="bsi", verify_mode="sample", schutzklasse=3,
        operator="Robin", reformat="exfat", reformat_label="ABC"))
    page.start_wipe(stick)
    assert captured["started"]
    assert captured["devices"] == [stick]
    assert isinstance(captured["wipe_method"], BsiVsitr)
    assert (captured["verify_mode"], captured["schutzklasse"], captured["operator"]) == (
        "sample", 3, "Robin")
    assert (captured["reformat"], captured["reformat_label"]) == ("exfat", "ABC")
    assert page.current_view() == "running"


class _FakeSignal:
    def connect(self, _slot):
        pass


# -- Confirmation --------------------------------------------------------------


def test_confirmation_names_every_letter_model_serial_and_size(qtbot, gui_reset):
    from gui.dialogs import ConfirmWipeDialog

    disk = make_device("E:", 1)
    disk.disk_letters = ["E:", "G:"]
    dlg = ConfirmWipeDialog([disk])
    qtbot.addWidget(dlg)
    text = dlg.summary_text()
    for part in ("E:", "G:", "SanDisk 3.2Gen1", "0901C3F2CFA4", "57,7 GB"):
        assert part in text


def test_confirmation_rejects_a_wrong_word(qtbot, gui_reset):
    from PySide6.QtWidgets import QDialog

    from gui.dialogs import ConfirmWipeDialog

    dlg = ConfirmWipeDialog([make_device("E:", 1)])
    qtbot.addWidget(dlg)
    for wrong in ("", "LOESCHEN", "DELETE", "LÖSCHEN!"):
        dlg.word_edit.setText(wrong)
        assert not dlg.confirm_button.isEnabled(), wrong
        dlg.accept()
        assert dlg.result() != QDialog.DialogCode.Accepted, wrong
    dlg.word_edit.setText(" löschen ")
    assert dlg.confirm_button.isEnabled()


def test_confirmation_accepts_delete_in_english(qtbot, gui_reset):
    from gui import i18n
    from gui.dialogs import ConfirmWipeDialog

    i18n.set_language("en")
    dlg = ConfirmWipeDialog([make_device("E:", 1)])
    qtbot.addWidget(dlg)
    dlg.word_edit.setText("DELETE")
    assert dlg.confirm_button.isEnabled()
    dlg.word_edit.setText("LÖSCHEN")
    assert dlg.confirm_button.isEnabled()


def test_cancelled_confirmation_starts_nothing(page, monkeypatch):
    import gui.pages.delete_page as delete_page

    started = []
    monkeypatch.setattr(delete_page.DeletePage, "start_wipe",
                        lambda self, d: started.append(d))
    monkeypatch.setattr(delete_page, "ask_confirmation", lambda *a, **k: False)
    page.set_devices([make_device("F:", 1)])
    page.delete_button.click()
    assert started == []


def test_unsafe_disk_is_refused_before_confirmation(page, monkeypatch):
    import gui.pages.delete_page as delete_page

    asked = []
    monkeypatch.setattr(delete_page, "ask_confirmation",
                        lambda *a, **k: asked.append(1) or True)
    monkeypatch.setattr(delete_page, "is_safe_to_wipe",
                        lambda d, allow_internal=False: (False, "nein"))
    monkeypatch.setattr(delete_page, "show_message", lambda *a, **k: None)
    monkeypatch.setattr(delete_page.DeletePage, "start_wipe",
                        lambda self, d: asked.append("started"))
    page.set_devices([make_device("F:", 1)])
    page.delete_button.click()
    assert asked == []


# -- Running -------------------------------------------------------------------


@pytest.fixture()
def running(page, monkeypatch):
    """The page in its running state with a fake worker."""
    import gui.pages.delete_page as delete_page

    class FakeWorker:
        def __init__(self, **kwargs):
            for name in ("progress_updated", "verify_progress", "phase_changed",
                         "device_completed", "all_completed", "error",
                         "status_message", "stall_detected"):
                setattr(self, name, _FakeSignal())
            self.cancelled = False

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def isRunning(self):  # noqa: N802
            return True

    monkeypatch.setattr(delete_page, "WipeWorker", FakeWorker)
    stick = make_device("F:", 1)
    page.set_devices([stick])
    page.start_wipe(stick)
    return page


def test_progress_updates_percentage_and_tiles(running):
    gib = 1024 ** 3
    running._on_progress(0, 1, 1, 31 * gib, 62 * gib, 118.0)
    view = running.running_view
    # Overwrite and full verify weigh the same: half the overwrite = 25 %.
    assert view.percent_label.text() == "25"
    assert view.speed_tile.value_text() == "118 MB/s"
    assert view.eta_tile.value_text().startswith("~")
    assert view.stepper.state(0) == "active"
    running._on_phase_changed(0, "verifying")
    running._on_verify_progress(0, 0.24, 24, 100, 80.0)
    assert view.percent_label.text() == "62"  # the artboard's numbers


def test_progress_survives_32_bit_overflow(running):
    running._on_progress(0, 1, 1, -1_073_741_824, -1, 50.0)
    assert 0 <= int(running.running_view.percent_label.text()) <= 100


def test_verify_phase_moves_the_stepper(running):
    running._on_progress(0, 1, 1, 100, 100, 50.0)
    running._on_phase_changed(0, "verifying")
    running._on_verify_progress(0, 0.24, 24, 100, 80.0)
    view = running.running_view
    assert view.stepper.state(0) == "done"
    assert view.stepper.state(1) == "active"
    assert view.stepper.fraction(1) == pytest.approx(0.24)
    assert "Prüfung" in view.protocol_texts()[-1] or any(
        "Prüfung" in t for t in view.protocol_texts())


def test_stall_shows_a_visible_warning(running):
    running._on_stall_detected(0, 75, "USB controller may have hung.")
    view = running.running_view
    assert view.stall_label.isVisible()
    assert "75" in view.stall_label.text()
    assert view.progress_bar.tone() == "warning"
    assert any("75" in t for t in view.protocol_texts())


def test_status_messages_are_translated_for_the_protocol(running):
    running._on_status_message("Generating certificate...")
    assert running.running_view.protocol_texts()[-1].endswith(
        "Zertifikat wird erstellt und signiert")
    assert running.running_view.stepper.state(3) == "active"


def test_cancel_asks_and_cancels_the_worker(running, monkeypatch):
    import gui.pages.delete_page as delete_page

    monkeypatch.setattr(delete_page, "ask_yes_no", lambda *a, **k: True)
    running.running_view.cancel_button.click()
    assert running.worker.cancelled
    assert not running.running_view.cancel_button.isEnabled()


# -- Result --------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, eyebrow, tone",
    [("ok", "Ergebnis: Erfolgreich", "ok"),
     ("limited", "Ergebnis: Mit Einschränkungen", "limited"),
     ("failed", "Ergebnis: Fehlgeschlagen", "failed")],
)
def test_result_shows_three_levels(page, status, eyebrow, tone):
    from gui.pages.wipe_views import WipeOutcome

    outcome = WipeOutcome(
        device=make_device("F:", 1), status=status, cert_path="",
        payload={"n": 15, "r": status, "p": 1, "vm": "full", "ve": 0, "bs": 0,
                 "dc": 61_933_428_736, "sl": "1-2", "op": "Robin",
                 "dm": "SanDisk 3.2Gen1", "ds": "0901C3F2CFA4", "m": "ZeroFill"},
        token="", fingerprint="65B6-0673-D6ED-884B-0000-0000-0000-0000",
        duration_seconds=1062, method_key="zero", verify_mode="full",
        reformat="exfat", reformat_label="SANDISK",
    )
    page.show_result(outcome)
    view = page.result_view
    assert page.current_view() == "result"
    assert view.eyebrow_label.text() == eyebrow
    assert view.panel.property("tone") == tone
    rows = view.summary_rows()
    assert rows["Dauer"] == "17 Min. 42 Sek."
    # A failed wipe is never reported as formatted.
    assert rows["Formatiert"] == ("nein" if status == "failed" else "exFAT · „SANDISK“")
    assert view.cert_number_label.text() == "Nr. 0015"


def test_qr_matrix_matches_the_certificate_qr():
    from gui.widgets import qr_matrix

    matrix = qr_matrix("https://example.invalid/#abc.def")
    assert len(matrix) >= 21
    assert all(len(row) == len(matrix) for row in matrix)
    # Finder pattern: the top-left 7x7 ring is dark.
    assert all(matrix[0][c] for c in range(7))
    assert all(matrix[r][0] for r in range(7))
