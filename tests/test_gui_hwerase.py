"""Hardware erase (Beta) on the Löschen page: offer, confirmation, worker, views.

Fakes only: the page is browsed with WMI-free fake disks, the worker runs
with fake gate/handle/drive functions, and every drive-command entry point is
replaced by a tripwire that fails the test if anything calls it while
browsing. The conftest fixtures additionally block real DeviceIoControl and
device handles.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from tests.gui_fixtures import (  # noqa: E402,F401
    app_config,
    gui_reset,
    isolated_profile,
    make_device,
)


def sata_ssd(index: int = 2, **kw):
    """A non-system SATA SSD (internal by default, like a real one)."""
    values = dict(letter="", index=index, model="Samsung SSD 870 EVO",
                  serial="S6PNNS0T123456", size=500_107_862_016, connection="SATA",
                  removable=False, internal=True)
    values.update(kw)
    return make_device(**values)


MEDIA = {}


@pytest.fixture()
def page(qtbot, app_config, gui_reset):
    from gui.pages.delete_page import DeletePage
    from gui.theme import ThemeManager

    MEDIA.clear()
    app_config.beta_features = True  # the hardware erase is a Beta feature
    ThemeManager(gui_reset, "dark").apply()
    widget = DeletePage(app_config, list_devices_fn=lambda: [],
                        media_fn=lambda d: MEDIA.get(d.device_id, "flash"))
    qtbot.addWidget(widget)
    widget.resize(1192, 800)
    widget.show()
    return widget


@pytest.fixture()
def no_drive_commands(monkeypatch):
    """Tripwires: browsing must never reach a drive-command entry point."""
    import wipe.hardware_erase as hardware_erase
    import wipe.passthrough as passthrough
    import wipe.secure_erase as secure_erase
    import wipe.transport as transport

    calls = []

    def tripwire(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} called while browsing")
        return fail

    for module, name in ((transport, "inspect_handle"), (secure_erase, "secure_erase_supported"),
                         (secure_erase, "secure_erase"), (passthrough, "ata_identify"),
                         (passthrough, "send_ata_command"), (hardware_erase, "run")):
        monkeypatch.setattr(module, name, tripwire(name))
    return calls


def show_ssd(page, device=None, media="ssd", allow_internal=True):
    device = device or sata_ssd()
    MEDIA[device.device_id] = media
    page.allow_internal_checkbox.setChecked(allow_internal)
    page.set_devices([make_device("F:", 1), device])
    page.select_device(device)
    return device


def choose_hardware(page, monkeypatch, answer=True):
    import gui.pages.delete_page as delete_page

    asked = []
    monkeypatch.setattr(delete_page, "confirm_hardware_erase",
                        lambda parent, device: asked.append(device) or answer)
    page.hardware_button.click()
    return asked


# -- When the hardware erase is offered (pure rules) ---------------------------


def test_hardware_erase_fits_only_directly_attached_sata_ssds():
    from gui.wipe_options import hardware_erase_fits

    ssd = sata_ssd()
    assert hardware_erase_fits(ssd, "ssd", 2)
    assert hardware_erase_fits(replace(ssd, connection_type="ATA"), "ssd", 2)
    for connection in ("USB", "NVMe", "RAID", "SCSI", "SAS", "Unknown", ""):
        assert not hardware_erase_fits(replace(ssd, connection_type=connection), "ssd", 2)
    for media in ("hdd", "flash", "unknown"):
        assert not hardware_erase_fits(ssd, media, 2)
    assert not hardware_erase_fits(replace(ssd, is_system_drive=True), "ssd", 2)


def test_ssds_elsewhere_get_a_short_reason():
    from gui.wipe_options import hardware_erase_hint

    ssd = sata_ssd()
    for connection in ("USB", "NVMe"):
        hint = hardware_erase_hint(replace(ssd, connection_type=connection), "ssd")
        assert "nur" in hint and "SATA" in hint
    assert hardware_erase_hint(ssd, "ssd") == ""                       # it fits
    assert hardware_erase_hint(replace(ssd, connection_type="USB"), "flash") == ""
    assert hardware_erase_hint(replace(ssd, connection_type="USB"), "hdd") == ""
    assert hardware_erase_hint(replace(ssd, is_system_drive=True), "ssd") == ""


def test_hardware_run_always_overwrites_with_one_pass_of_zeros():
    from gui.wipe_options import (
        WipeOptions, build_method, estimate_seconds, method_label, passes_of,
    )
    from wipe.methods import ZeroFill

    options = WipeOptions(method_key="bsi", hardware_erase=True)
    assert isinstance(build_method(options), ZeroFill)
    assert passes_of(options) == 1
    assert method_label(options) == "Hardware-Löschung + 1× Nullen"
    zero = WipeOptions(method_key="zero")
    assert estimate_seconds(options, 10**9) == estimate_seconds(zero, 10**9)


# -- Selection page --------------------------------------------------------------


def test_sata_ssd_offers_the_hardware_erase(page, no_drive_commands):
    show_ssd(page)
    assert page.hardware_card.isVisible()
    assert "Hardware-Löschung" in page.hardware_title.text()
    assert page.hardware_badge.text() == "Beta"
    assert "Reserve-Bereiche" in page.hardware_text.text()
    assert not page.hardware_hint.isVisible()
    assert not page.options.hardware_erase  # Beta: never on by default
    assert no_drive_commands == []


def test_disk_rows_show_the_hardware_chip(page, no_drive_commands):
    ssd = sata_ssd()
    stick = make_device("G:", 4, "Kingston DT", serial="AA11")
    MEDIA[ssd.device_id] = "ssd"
    page.allow_internal_checkbox.setChecked(True)
    page.set_devices([make_device("F:", 1), ssd, stick])
    assert "Hardware-Löschung · Beta" in page.row_for(ssd).chip_texts()
    assert "Hardware-Löschung · Beta" not in page.row_for(stick).chip_texts()
    assert no_drive_commands == []


@pytest.mark.parametrize("connection, media, hint", [
    ("USB", "ssd", True), ("NVMe", "ssd", True), ("USB", "flash", False),
    ("SATA", "hdd", False), ("SATA", "unknown", False), ("Unknown", "ssd", True),
])
def test_other_disks_get_no_hardware_erase(page, no_drive_commands, connection, media, hint):
    device = sata_ssd(connection=connection, internal=connection == "SATA")
    show_ssd(page, device, media=media)
    assert page.selected_device is device
    assert not page.hardware_card.isVisible()
    assert page.hardware_hint.isVisible() is hint
    if hint:
        assert "SATA" in page.hardware_hint.text()
    assert no_drive_commands == []


def test_cancelled_confirmation_changes_nothing(page, monkeypatch, no_drive_commands):
    show_ssd(page)
    before = (page.method_tile.value_text(), page.class_tile.value_text())
    asked = choose_hardware(page, monkeypatch, answer=False)
    assert len(asked) == 1
    assert not page.options.hardware_erase
    assert (page.method_tile.value_text(), page.class_tile.value_text()) == before
    assert page.worker is None
    assert no_drive_commands == []


def test_confirmed_choice_shows_the_hardware_procedure(page, monkeypatch, no_drive_commands):
    show_ssd(page)
    choose_hardware(page, monkeypatch)
    assert page.options.hardware_erase
    assert page.method_tile.value_text() == "Hardware-Löschung + 1× Nullen"
    assert page.method_tile.note_text() == "Empfohlen · Beta"
    assert page.class_tile.value_text() == "NIST Purge"
    assert page.verify_tile.value_text() == "Jeder Sektor"
    assert "Hardware-Löschung" in page.estimate_label.text()
    assert page.hardware_button.text() == "Nur überschreiben"
    assert no_drive_commands == []

    page.hardware_button.click()  # back to overwriting, no question asked
    assert not page.options.hardware_erase
    assert page.class_tile.value_text() == "NIST Clear"


def test_overwrite_on_a_fitting_ssd_warns_about_spare_areas(page, no_drive_commands):
    show_ssd(page)
    assert "Reserve-Bereiche" in page.warning_label.text()
    assert "Kein Hardware-Löschen verfügbar" not in page.warning_label.text()


def test_overwrite_only_with_protection_class_3_still_advises_destruction(page, monkeypatch):
    page.set_options(replace(page.options, schutzklasse=3))
    show_ssd(page)
    text = page.warning_label.text()
    assert "Reserve-Bereiche" in text and "physisch vernichtet" in text
    assert "Kein Hardware-Löschen verfügbar" not in text
    # The drive's own erase (Beta) can't be checked from outside: class 3
    # still needs physical destruction, the note now names the erase.
    choose_hardware(page, monkeypatch)
    text = page.warning_label.text()
    assert "Hardware-Löschen (Beta)" in text and "physisch vernichtet" in text


def test_choosing_another_disk_turns_the_hardware_erase_off(page, monkeypatch):
    first = show_ssd(page)
    choose_hardware(page, monkeypatch)
    second = sata_ssd(index=3, serial="S6PNNS0T999999")
    MEDIA[second.device_id] = "ssd"
    page.set_devices([make_device("F:", 1), first, second])
    page.select_device(second)
    assert not page.options.hardware_erase
    page.select_device(first)
    assert not page.options.hardware_erase  # confirm again for every disk


def test_picking_another_method_turns_the_hardware_erase_off(page, monkeypatch):
    import gui.pages.delete_page as delete_page

    class OtherMethod:
        def __init__(self, options, recommended_method="zero", parent=None):
            self._options = options

        def exec(self):
            return True

        def options(self):
            return replace(self._options, method_key="bsi")

    class SameMethod(OtherMethod):
        def options(self):
            return replace(self._options, operator="Robin")

    show_ssd(page)
    choose_hardware(page, monkeypatch)
    monkeypatch.setattr(delete_page, "WipeOptionsDialog", OtherMethod)
    page.open_options()
    assert not page.options.hardware_erase
    assert page.method_tile.value_text() == "BSI-VSITR · 7×"

    choose_hardware(page, monkeypatch)
    monkeypatch.setattr(delete_page, "WipeOptionsDialog", SameMethod)
    page.open_options()  # only the operator changed: the hardware erase stays
    assert page.options.hardware_erase and page.options.operator == "Robin"


def test_hardware_choice_reaches_the_worker(page, monkeypatch):
    import gui.pages.delete_page as delete_page
    from wipe.methods import ZeroFill

    captured = {}

    class FakeWorker:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            for name in ("progress_updated", "verify_progress", "phase_changed",
                         "device_completed", "all_completed", "error", "status_message",
                         "stall_detected", "hardware_erase_started", "hardware_erase_finished"):
                setattr(self, name, _FakeSignal())

        def start(self):
            captured["started"] = True

        def isRunning(self):  # noqa: N802 — Qt name
            return False

    monkeypatch.setattr(delete_page, "WipeWorker", FakeWorker)
    ssd = show_ssd(page)
    page.set_options(replace(page.options, method_key="bsi"))
    choose_hardware(page, monkeypatch)
    page.start_wipe(ssd)
    assert captured["hardware_erase"] is True
    assert isinstance(captured["wipe_method"], ZeroFill)
    assert captured["verify_mode"] == "full"
    assert page.running_view.stepper.state(0) == "active"
    assert page.running_view.step_names()[0] == "hardware"


class _FakeSignal:
    def connect(self, _slot):
        pass


# -- The extra confirmation dialog -----------------------------------------------


def test_dialog_needs_the_untested_path_checkbox(qtbot, gui_reset):
    from gui.pages.wipe_views import HardwareEraseDialog
    from wipe.hardware_erase import TEMP_PASSWORD

    dlg = HardwareEraseDialog(sata_ssd())
    qtbot.addWidget(dlg)
    text = dlg.full_text()
    for part in ("Beta", "frozen", "Standby", "abstecken", TEMP_PASSWORD, "Nullen"):
        assert part in text, part
    disk = dlg.disk_label.text()
    assert disk.count("Samsung SSD 870 EVO") == 1 and "SN S6PNNS0T123456" in disk
    assert not dlg.confirm_button.isEnabled()
    dlg.accept()
    assert dlg.result() == 0
    dlg.untested_check.setChecked(True)
    assert dlg.confirm_button.isEnabled()


# -- Worker: fake gate, handle and drive ----------------------------------------


class FakeZeroFill:
    """The 1x zeros after the erase (records the call, writes nothing)."""

    name = "ZeroFill"
    passes = 1
    sicherheitsstufe = "1-2"
    final_pass_is_random = False

    def __init__(self, state):
        self.state = state

    def execute(self, *args, **kwargs):
        from datetime import datetime

        from wipe.methods import WipeResult
        from wipe.verify import VerifyResult

        self.state.events.append(("zero", self.state.released == [], kwargs.get("verify_mode")))
        now = datetime.now()
        verify = VerifyResult(success=True, method=kwargs.get("verify_mode") or "none",
                              bytes_verified=8 * 1024 * 1024, expected_pattern="zeros",
                              error_count=0, mismatch_offsets=[], duration_seconds=0.1)
        return WipeResult(method_name="ZeroFill", passes=1, start_time=now, end_time=now,
                          bytes_written=8 * 1024 * 1024, success=True, error_message=None,
                          verify_result=verify)


def _status(method="ata-enhanced", *, frozen=False, supported=True, error=None):
    from wipe.secure_erase import SecureEraseResult

    return SecureEraseResult(success=False, method=method, supported=supported, frozen=frozen,
                             duration_seconds=0.01, error=error, estimated_minutes=2)


def _erased(method="ata-enhanced"):
    from wipe.secure_erase import SecureEraseResult

    return SecureEraseResult(success=True, method=method, supported=True, frozen=False,
                             duration_seconds=4.0, error=None)


@pytest.fixture()
def fake_disk(monkeypatch):
    """The worker's real-disk path with every device function replaced."""
    from types import SimpleNamespace

    import gui.wipe_worker as wipe_worker
    import wipe.hardware_erase as hardware_erase
    import wipe.secure_erase as secure_erase

    state = SimpleNamespace(events=[], released=[], closed=[], certs=[], history=[],
                            power=[], status=_status(), erase=_erased(), worker=None,
                            watchdog_during_erase=[])

    def gate(device):
        state.events.append("gate")
        return [11, 12]

    def supported(handle):
        assert handle == 777
        state.events.append("probe")
        return state.status

    def erase(handle, **kwargs):
        assert handle == 777
        state.events.append(("erase", state.released == [], kwargs["enhanced"]))
        if state.worker is not None:
            state.watchdog_during_erase.append(state.worker._last_progress_time)
        if isinstance(state.erase, Exception):
            raise state.erase
        return state.erase

    def generate(data, path, signing_key=None, history_hash=""):
        state.certs.append(data)
        return path

    def log_row(row):
        state.history.append(row)
        return "ab" * 32

    no_hidden = SimpleNamespace(supported=False, hpa_present=False, dco_present=False,
                                hpa_hidden_sectors=0, dco_hidden_sectors=0)
    monkeypatch.setattr(wipe_worker, "prepare_target", gate)
    monkeypatch.setattr(wipe_worker, "release_volumes", lambda locks: state.released.extend(locks))
    monkeypatch.setattr(wipe_worker, "open_physical_drive", lambda device_id: 777)
    monkeypatch.setattr(wipe_worker, "get_drive_size", lambda handle: 8 * 1024 * 1024)
    monkeypatch.setattr(wipe_worker, "detect_hidden_areas", lambda handle: no_hidden)
    monkeypatch.setattr(wipe_worker, "close_drive", lambda handle: state.closed.append(handle))
    monkeypatch.setattr(secure_erase, "secure_erase_supported", supported)
    monkeypatch.setattr(secure_erase, "secure_erase", erase)
    monkeypatch.setattr(hardware_erase, "_set_execution_state",
                        lambda flags: state.power.append(flags))
    monkeypatch.setattr(wipe_worker, "generate_certificate", generate)
    monkeypatch.setattr(wipe_worker, "log_wipe_to_csv", log_row)
    monkeypatch.setattr(wipe_worker, "get_next_cert_number", lambda: 9)
    return state


def run_worker(qtbot, app_config, state, device=None, **kwargs):
    from gui.wipe_worker import WipeWorker

    device = device or sata_ssd()
    worker = WipeWorker(devices=[device], wipe_method=FakeZeroFill(state), config=app_config,
                        verify_mode="full", hardware_erase=True, **kwargs)
    state.worker = worker
    signals = {"started": [], "finished": [], "completed": [], "errors": []}
    worker.hardware_erase_started.connect(lambda *a: signals["started"].append(a))
    worker.hardware_erase_finished.connect(lambda *a: signals["finished"].append(a))
    worker.device_completed.connect(lambda *a: signals["completed"].append(a))
    worker.error.connect(lambda *a: signals["errors"].append(a))
    with qtbot.waitSignal(worker.all_completed, timeout=20000):
        worker.start()
    worker.wait(5000)
    return signals


@pytest.mark.parametrize("status, reason", [
    (_status("frozen", frozen=True), "frozen"),
    (_status("unsupported", supported=False), "unsupported"),
    (_status("unsupported", supported=False, error="disk is on port driver iaStorVD"),
     "no_path"),
])
def test_worker_stops_before_any_write_when_the_drive_says_no(
        qtbot, app_config, fake_disk, status, reason):
    fake_disk.status = status
    signals = run_worker(qtbot, app_config, fake_disk)

    assert fake_disk.events == ["gate", "probe"]       # no erase, no zeros
    assert fake_disk.certs == [] and fake_disk.history == []
    assert fake_disk.released == [11, 12]
    assert fake_disk.closed == [777]
    assert signals["started"] == []
    [(index, report)] = signals["finished"]
    assert (index, report.status, report.reason) == (0, "refused", reason)
    assert signals["completed"] == [(0, False, "")]
    assert signals["errors"] and "nicht möglich" in signals["errors"][0][1]


def test_worker_hardware_erase_then_zeros_fill_the_certificate(qtbot, app_config, fake_disk):
    from cert.payload import result_status

    signals = run_worker(qtbot, app_config, fake_disk)

    assert fake_disk.events == ["gate", "probe", ("erase", True, True), ("zero", True, "full")]
    assert fake_disk.released == [11, 12]
    assert signals["started"] == [(0, "ata-enhanced", 2)]
    [(_, report)] = signals["finished"]
    assert report.status == "erased" and report.method == "ata-enhanced"
    [cert] = fake_disk.certs
    assert cert.secure_erase_used is True
    assert cert.secure_erase_method == "ata-enhanced"
    assert cert.wipe_method == "ATA Secure Erase (enhanced) + ZeroFill"
    assert cert.sicherheitsstufe == "1-2"
    assert cert.passes == 1
    assert cert.verify_method == "full" and cert.verification_passed is True
    assert cert.wipe_success is True
    assert result_status(cert) == "ok"
    assert fake_disk.history[0]["method"] == "ATA Secure Erase (enhanced) + ZeroFill"
    assert signals["completed"][0][:2] == (0, True)
    assert signals["errors"] == []


def test_worker_exception_in_the_erase_releases_the_locks_and_fails(
        qtbot, app_config, fake_disk):
    fake_disk.erase = RuntimeError("bridge gone")
    signals = run_worker(qtbot, app_config, fake_disk)

    assert ("zero", True, "full") not in fake_disk.events
    assert fake_disk.released == [11, 12]
    assert fake_disk.closed == [777]
    assert fake_disk.certs == []
    [(_, report)] = signals["finished"]
    assert report.status == "failed" and report.password_may_be_set
    assert "bridge gone" in report.detail
    assert signals["completed"] == [(0, False, "")]


def test_worker_crash_inside_the_procedure_still_releases_the_locks(
        qtbot, app_config, fake_disk, monkeypatch):
    import wipe.hardware_erase as hardware_erase

    def crash(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(hardware_erase, "run", crash)
    signals = run_worker(qtbot, app_config, fake_disk)
    assert fake_disk.released == [11, 12]
    assert fake_disk.closed == [777]
    assert signals["completed"] == [(0, False, "")]


def test_worker_stall_watchdog_is_off_while_the_drive_erases(qtbot, app_config, fake_disk):
    run_worker(qtbot, app_config, fake_disk)
    assert fake_disk.watchdog_during_erase == [None]


def test_demo_ssd_fakes_the_drive_erase_then_overwrites_the_demo_file(
        qtbot, app_config, fake_disk, monkeypatch):
    import gui.wipe_worker as wipe_worker
    from gui.pages.delete_page import create_demo_ssd
    from wipe.methods import ZeroFill

    monkeypatch.setattr(wipe_worker.WipeWorker, "DEMO_ERASE_SECONDS", 0.05)
    worker = wipe_worker.WipeWorker(devices=[create_demo_ssd()], wipe_method=ZeroFill(),
                                    config=app_config, verify_mode="full", hardware_erase=True)
    started, finished = [], []
    worker.hardware_erase_started.connect(lambda *a: started.append(a))
    worker.hardware_erase_finished.connect(lambda *a: finished.append(a))
    with qtbot.waitSignal(worker.all_completed, timeout=20000):
        worker.start()
    worker.wait(5000)

    assert fake_disk.events == []                 # no gate, no drive, no handle
    assert started and finished[0][1].status == "erased"
    [cert] = fake_disk.certs
    assert cert.secure_erase_used is True
    assert cert.wipe_method == "ATA Secure Erase (enhanced) + ZeroFill"
    assert cert.verify_method == "full" and cert.verification_passed is True


# -- Running and result views -------------------------------------------------------


_ALL_SIGNALS = ("progress_updated", "verify_progress", "phase_changed", "device_completed",
                "all_completed", "error", "status_message", "stall_detected",
                "hardware_erase_started", "hardware_erase_finished")


@pytest.fixture()
def hw_running(page, monkeypatch):
    """The page running a hardware erase with a fake worker."""
    import gui.pages.delete_page as delete_page

    class FakeWorker:
        def __init__(self, **kwargs):
            for name in _ALL_SIGNALS:
                setattr(self, name, _FakeSignal())
            self.cancelled = False

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def isRunning(self):  # noqa: N802
            return True

    monkeypatch.setattr(delete_page, "WipeWorker", FakeWorker)
    ssd = show_ssd(page)
    choose_hardware(page, monkeypatch)
    page.start_wipe(ssd)
    return page


def test_running_view_shows_time_estimate_and_password_while_the_drive_erases(hw_running):
    from wipe.hardware_erase import TEMP_PASSWORD

    view = hw_running.running_view
    assert view.stepper.state(0) == "active"
    assert "gefragt" in view.hint_label.text()
    hw_running._on_hardware_erase_started(0, "ata-enhanced", 2)
    assert not view.cancel_button.isVisible()  # the drive's erase cannot be cancelled
    assert (view.speed_tile.label.text(), view.speed_tile.value_text()) == ("Verfahren", "Enhanced")
    assert (view.eta_tile.label.text(), view.eta_tile.value_text()) == ("SSD schätzt", "~ 2 Min.")
    assert (view.bad_tile.label.text(), view.bad_tile.value_text()) == ("Passwort", TEMP_PASSWORD)
    assert view.bad_tile.note_text() == "falls gesperrt"
    assert view.progress_bar.value() == 1.0
    assert "Standby" in view.hint_label.text()
    view.update_elapsed(42)
    assert view.percent_label.text() == "0:42"
    assert not view.percent_unit.isVisible()
    hw_running._update_overall()  # progress refreshes keep the elapsed time
    assert view.percent_label.text() == "0:42"
    assert any(TEMP_PASSWORD in line for line in view.protocol_texts())


def test_after_the_erase_the_overwrite_shows_its_percentage(hw_running):
    from wipe.hardware_erase import EraseReport

    view = hw_running.running_view
    hw_running._on_hardware_erase_started(0, "ata-secure-erase", 0)
    assert view.eta_tile.value_text() == "keine Angabe"
    hw_running._on_hardware_erase_finished(
        0, EraseReport("erased", method="ata-secure-erase", duration_seconds=42))
    assert (view.stepper.state(0), view.stepper.state(1)) == ("done", "active")
    assert view.cancel_button.isVisible() and view.cancel_button.isEnabled()
    assert view.speed_tile.label.text() == "Tempo"
    gib = 1024 ** 3
    hw_running._on_progress(0, 1, 1, 31 * gib, 62 * gib, 118.0)
    assert view.percent_label.text() == "25"
    assert view.percent_unit.isVisible()
    assert any("SSD meldet: gelöscht" in line for line in view.protocol_texts())


def _hw_outcome(report, status="failed", payload=None, device=None, **kwargs):
    from gui.pages.wipe_views import WipeOutcome

    return WipeOutcome(device=device or sata_ssd(), status=status, payload=payload,
                       hardware_erase=True, hardware=report, duration_seconds=65, **kwargs)


def test_result_after_a_frozen_drive_explains_and_offers_to_overwrite(page):
    from wipe.hardware_erase import EraseReport

    page.show_result(_hw_outcome(EraseReport("refused", "frozen", detail="frozen"),
                                 error="Hardware-Löschung nicht möglich"))
    view = page.result_view
    assert view.title_label.text().startswith("Hardware-Löschung nicht möglich")
    rows = view.summary_rows()
    assert rows["Hardware-Löschung"] == "nicht gestartet — nichts wurde geschrieben"
    assert "Standby" in rows["Grund"] and "abstecken" in rows["Grund"]
    assert "Fehler" not in rows
    assert "unverändert" in view.note_label.text()
    assert view.overwrite_button.isVisible()


def test_result_after_a_failed_erase_names_the_temporary_password(page):
    from wipe.hardware_erase import TEMP_PASSWORD, EraseReport

    report = EraseReport("failed", "error", method="ata-enhanced",
                         detail="SECURITY ERASE UNIT failed", password_may_be_set=True)
    page.show_result(_hw_outcome(report))
    view = page.result_view
    assert view.title_label.text().endswith("nicht sicher gelöscht")
    assert view.summary_rows()["Hardware-Löschung"] == "abgebrochen: SECURITY ERASE UNIT failed"
    assert TEMP_PASSWORD in view.note_label.text()
    assert view.overwrite_button.isVisible()


def test_result_after_the_drive_erased_itself_says_so(page):
    from wipe.hardware_erase import EraseReport

    payload = {"n": 16, "r": "ok", "p": 1, "vm": "full", "ve": 0, "bs": 0,
               "dc": 500_107_862_016, "sl": "1-2", "op": "Robin",
               "dm": "Samsung SSD 870 EVO", "ds": "S6PNNS0T123456",
               "m": "ATA Secure Erase (enhanced) + ZeroFill"}
    page.show_result(_hw_outcome(EraseReport("erased", method="ata-enhanced",
                                             duration_seconds=42), status="ok", payload=payload))
    view = page.result_view
    rows = view.summary_rows()
    assert rows["Hardware-Löschung"] == (
        "die SSD hat sich selbst gelöscht · Enhanced Secure Erase · 42 Sek.")
    assert rows["Überschrieben"].endswith("1 Durchgang Nullen")
    assert rows["Geprüft"] == "jeder Sektor · 0 Abweichungen"
    assert "NIST 800-88 Purge" in view.cert_lines_label.text()
    assert not view.overwrite_button.isVisible()


def test_final_refusal_hides_the_offer_for_that_disk(hw_running):
    from wipe.hardware_erase import EraseReport

    page = hw_running
    ssd = page._run.device
    page._on_hardware_erase_finished(0, EraseReport("refused", "no_path", detail="iaStorVD"))
    page._on_device_completed(0, False, "")
    page._on_all_completed()
    assert page.current_view() == "result"
    page.result_view.overwrite_button.click()
    assert page.current_view() == "select"
    assert not page.options.hardware_erase
    page.set_devices([make_device("F:", 1), ssd])  # this fixture's scan finds nothing
    page.select_device(ssd)
    assert not page.hardware_card.isVisible()
    assert "Anschluss" in page.hardware_hint.text()


def test_frozen_drive_keeps_the_offer(hw_running):
    from wipe.hardware_erase import EraseReport

    page = hw_running
    ssd = page._run.device
    page._on_hardware_erase_finished(0, EraseReport("refused", "frozen"))
    page._on_device_completed(0, False, "")
    page._on_all_completed()
    page.result_view.next_button.click()
    page.set_devices([make_device("F:", 1), ssd])
    page.select_device(ssd)
    assert page.hardware_card.isVisible()  # standby or replugging may help


def test_demo_ssd_counts_as_a_sata_ssd(page, no_drive_commands):
    from gui.pages.delete_page import create_demo_ssd

    ssd = create_demo_ssd()
    page.set_devices([ssd])
    assert page.selected_device is ssd
    assert page.hardware_card.isVisible()
    assert "Demo-SSD" in page.hero_subtitle.text()
    assert no_drive_commands == []


def test_demo_ssd_can_be_clicked_through(qtbot, page, monkeypatch):
    import gui.pages.delete_page as delete_page
    import gui.wipe_worker as wipe_worker

    monkeypatch.setattr(wipe_worker.WipeWorker, "DEMO_ERASE_SECONDS", 0.2)
    monkeypatch.setattr(delete_page, "ask_confirmation", lambda *a, **k: True)
    page.add_demo_device()
    ssd = next(d for d in page._devices if d.device_id == delete_page.DEMO_SSD_ID)
    assert "Hardware-Löschung · Beta" in page.row_for(ssd).chip_texts()
    assert page.select_device(ssd)
    assert page.hardware_card.isVisible()
    choose_hardware(page, monkeypatch)
    qtbot.mouseClick(page.delete_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: page.current_view() == "result", timeout=30000)

    view = page.result_view
    assert view.eyebrow_label.text() == "Ergebnis: Erfolgreich"
    assert view.summary_rows()["Hardware-Löschung"].startswith("die SSD hat sich selbst gelöscht")
    assert "Purge" in view.cert_lines_label.text()
    assert view.cert_path.endswith(".pdf")
    qtbot.waitUntil(lambda: page.worker is None, timeout=10000)


@pytest.mark.parametrize("lang", ["de", "en"])
@pytest.mark.parametrize("mode", ["dark", "light"])
def test_hardware_views_have_every_text_in_both_languages_and_themes(
        qtbot, page, gui_reset, monkeypatch, lang, mode):
    import re

    from PySide6.QtWidgets import QAbstractButton, QLabel

    from gui import i18n
    from gui.pages.wipe_views import HardwareEraseDialog, WipeOutcome
    from gui.theme import ThemeManager
    from wipe.hardware_erase import EraseReport

    i18n.set_language(lang)
    ThemeManager(gui_reset, mode).apply()
    page.retranslate()
    ssd = show_ssd(page)
    choose_hardware(page, monkeypatch)
    dialog = HardwareEraseDialog(ssd)
    qtbot.addWidget(dialog)
    page.running_view.start(ssd, page.options)
    page.running_view.begin_hardware_erase("ata-enhanced", 2)
    raw_key = re.compile(r"[a-z_]+(\.[a-zA-Z0-9_-]+)+")
    for report in (EraseReport("refused", "frozen"),
                   EraseReport("failed", "error", detail="x", password_may_be_set=True),
                   EraseReport("erased", method="ata-secure-erase", duration_seconds=3)):
        page.result_view.show_outcome(WipeOutcome(device=ssd, status="failed",
                                                  hardware_erase=True, hardware=report))
        texts = [w.text() for root in (page, dialog)
                 for w in root.findChildren(QLabel) + root.findChildren(QAbstractButton)]
        assert [t for t in texts if raw_key.fullmatch(t)] == []
    assert not page.grab().isNull() and not dialog.grab().isNull()


# -- Review fixes ---------------------------------------------------------------------


class _SyncSignal:
    """Signal stand-in that calls its slots at once."""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


def test_hardware_erase_needs_the_beta_switch(page, no_drive_commands):
    page.config.beta_features = False
    ssd = show_ssd(page)
    assert not page.hardware_card.isVisible()
    assert page.hardware_hint.isVisible()
    assert "Optionen" in page.hardware_hint.text() and "Beta" in page.hardware_hint.text()
    page.select_device(make_device("F:", 1))
    assert "Hardware-Löschung · Beta" not in page.row_for(ssd).chip_texts()

    page.config.beta_features = True
    page.set_options(page.options)  # what saving the options page does
    page.select_device(ssd)
    assert page.hardware_card.isVisible()
    assert no_drive_commands == []


def test_options_page_switches_the_beta_features(qtbot, app_config, gui_reset):
    from core.config import AppConfig
    from gui.pages.options_page import OptionsPage

    options = OptionsPage(app_config)
    qtbot.addWidget(options)
    assert app_config.beta_features is False
    assert not options.beta_check.isChecked()
    options.beta_check.setChecked(True)
    assert options.save()
    assert app_config.beta_features is True
    assert AppConfig.load().beta_features is True
    app_config.beta_features = False
    options.load_from_config()
    assert not options.beta_check.isChecked()


def test_old_config_without_the_beta_switch_loads_with_it_off(isolated_profile):
    import json

    from core.config import AppConfig

    (isolated_profile / "config.json").write_text(
        json.dumps({"operator_name": "Robin"}), encoding="utf-8")
    config = AppConfig.load()
    assert config.operator_name == "Robin" and config.beta_features is False


def test_a_new_disk_on_the_same_number_starts_fresh(hw_running):
    from wipe.hardware_erase import EraseReport

    page = hw_running
    ssd = page._run.device
    page._on_hardware_erase_finished(0, EraseReport("refused", "unsupported"))
    page._on_device_completed(0, False, "")
    page._on_all_completed()
    page.result_view.next_button.click()
    other = replace(ssd, serial_number="S6PNNS0T999999")
    MEDIA[other.device_id] = "ssd"
    page.set_devices([make_device("F:", 1), other])
    page.select_device(other)
    assert page.hardware_card.isVisible()   # another SSD now has that disk number
    page.set_devices([make_device("F:", 1), ssd])
    page.select_device(ssd)
    assert not page.hardware_card.isVisible()


def test_the_hardware_choice_does_not_carry_over_to_a_swapped_disk(page, monkeypatch):
    ssd = show_ssd(page)
    choose_hardware(page, monkeypatch)
    swapped = replace(ssd, serial_number="SWAPPED-0001")
    MEDIA[swapped.device_id] = "ssd"
    page.set_devices([make_device("F:", 1), swapped])  # same number, other disk
    assert page.selected_device is swapped
    assert not page.options.hardware_erase


def test_the_page_runs_the_hardware_erase_with_full_verification(page, monkeypatch):
    import gui.pages.delete_page as delete_page

    captured = {}

    class FakeWorker:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            for name in _ALL_SIGNALS:
                setattr(self, name, _FakeSignal())

        def start(self):
            pass

        def isRunning(self):  # noqa: N802
            return True

    monkeypatch.setattr(delete_page, "WipeWorker", FakeWorker)
    page.set_options(replace(page.options, verify_mode="sample"))
    ssd = show_ssd(page)
    choose_hardware(page, monkeypatch)
    assert page.verify_tile.value_text() == "Jeder Sektor"
    page.start_wipe(ssd)
    assert captured["verify_mode"] == "full"


def test_the_worker_verifies_every_sector_after_a_hardware_erase(qtbot, app_config, fake_disk):
    from gui.wipe_worker import WipeWorker

    worker = WipeWorker(devices=[sata_ssd()], wipe_method=FakeZeroFill(fake_disk),
                        config=app_config, verify_mode="sample", hardware_erase=True)
    assert worker.verify_mode == "full"
    with qtbot.waitSignal(worker.all_completed, timeout=20000):
        worker.start()
    worker.wait(5000)
    assert ("zero", True, "full") in fake_disk.events


def test_window_cannot_close_while_the_ssd_erases_itself(qtbot, app_config, gui_reset,
                                                          monkeypatch):
    from PySide6.QtGui import QCloseEvent

    import gui.app_window as app_window
    import gui.pages.delete_page as delete_page
    from wipe.hardware_erase import TEMP_PASSWORD

    monkeypatch.setattr(delete_page, "list_devices", lambda: [])
    shown = []
    monkeypatch.setattr(app_window, "show_message",
                        lambda parent, title, text, kind="info": shown.append(text))
    asked = []
    monkeypatch.setattr(app_window, "ask_yes_no", lambda *a, **k: asked.append(a) or True)
    window = app_window.AppWindow(app_config)
    qtbot.addWidget(window)
    window.delete_page.running_view.begin_hardware_erase("ata-enhanced", 2)
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert asked == []                       # no "quit anyway" for this step
        assert shown and TEMP_PASSWORD in shown[0] and "abstecken" in shown[0]
    finally:
        window.delete_page.running_view.stop()   # the run is over: closing works again
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_hardware_dialog_defaults_to_cancel(qtbot, gui_reset):
    from PySide6.QtTest import QTest

    from gui.pages.wipe_views import HardwareEraseDialog

    dlg = HardwareEraseDialog(sata_ssd())
    qtbot.addWidget(dlg)
    assert dlg.cancel_button.isDefault()
    assert not dlg.confirm_button.isDefault() and not dlg.confirm_button.autoDefault()
    dlg.untested_check.setChecked(True)
    accepted, rejected = [], []
    dlg.accepted.connect(lambda: accepted.append(1))
    dlg.rejected.connect(lambda: rejected.append(1))
    dlg.show()
    QTest.keyClick(dlg, Qt.Key.Key_Return)
    assert accepted == [] and rejected == [1]


# -- „Gesperrte Platte entsperren“ ----------------------------------------------------


def test_result_offers_to_unlock_after_a_failed_erase_with_the_password(page):
    from wipe.hardware_erase import EraseReport

    page.show_result(_hw_outcome(EraseReport("failed", "error", detail="x",
                                             password_may_be_set=True)))
    assert page.result_view.unlock_button.isVisible()
    assert page.result_view.overwrite_button.isVisible()


def test_a_locked_drive_is_offered_unlock_instead_of_overwrite(page):
    from wipe.hardware_erase import EraseReport

    page.show_result(_hw_outcome(EraseReport("refused", "locked")))
    view = page.result_view
    assert view.unlock_button.isVisible()
    assert not view.overwrite_button.isVisible()  # a locked drive takes no writes
    assert "Passwort" in view.note_label.text()


@pytest.mark.parametrize("report_args", [
    ("refused", "frozen", False), ("refused", "unsupported", False), ("failed", "error", False),
])
def test_no_unlock_offer_without_a_password(page, report_args):
    from wipe.hardware_erase import EraseReport

    status, reason, password = report_args
    page.show_result(_hw_outcome(EraseReport(status, reason, password_may_be_set=password)))
    assert not page.result_view.unlock_button.isVisible()


def _unlock_setup(page, monkeypatch, *, answer=True, result=None):
    import gui.pages.delete_page as delete_page
    from wipe.secure_erase import UnlockResult

    state = {"asked": [], "started": [], "messages": []}
    monkeypatch.setattr(delete_page, "is_safe_to_wipe",
                        lambda device, allow_internal=False: (True, ""))
    monkeypatch.setattr(delete_page, "ask_yes_no",
                        lambda *a, **k: state["asked"].append(a) or answer)
    monkeypatch.setattr(delete_page, "show_message",
                        lambda parent, title, text, kind="info":
                        state["messages"].append((text, kind)))

    class FakeUnlockWorker:
        def __init__(self, device, parent=None):
            self.device = device
            self.unlock_finished = _SyncSignal()

        def start(self):
            state["started"].append(self.device)
            self.unlock_finished.emit(result or UnlockResult(True, "unlocked"))

        def isRunning(self):  # noqa: N802
            return False

        def wait(self, _ms=0):
            return True

    monkeypatch.setattr(delete_page, "UnlockWorker", FakeUnlockWorker)
    return state


def test_unlock_from_the_result_asks_runs_and_brings_the_offer_back(page, monkeypatch):
    from wipe.hardware_erase import EraseReport

    state = _unlock_setup(page, monkeypatch)
    ssd = show_ssd(page)
    page._remember_refusal(ssd, "locked")
    page.show_result(_hw_outcome(EraseReport("refused", "locked"), device=ssd))
    page.result_view.unlock_button.click()

    assert len(state["asked"]) == 1
    assert state["started"] == [ssd]
    text, kind = state["messages"][0]
    assert "Entsperrt" in text and kind == "info"
    assert not page.result_view.unlock_button.isVisible()
    page.result_view.next_button.click()
    page.set_devices([make_device("F:", 1), ssd])
    page.select_device(ssd)
    assert page.hardware_card.isVisible()       # unlocked: the offer is back


def test_cancelled_unlock_sends_nothing(page, monkeypatch):
    from wipe.hardware_erase import EraseReport

    state = _unlock_setup(page, monkeypatch, answer=False)
    page.show_result(_hw_outcome(EraseReport("refused", "locked")))
    page.result_view.unlock_button.click()
    assert state["started"] == []
    assert state["messages"] == []


def test_a_wrong_password_is_explained(page, monkeypatch):
    from wipe.hardware_erase import EraseReport
    from wipe.secure_erase import UnlockResult

    state = _unlock_setup(page, monkeypatch,
                          result=UnlockResult(False, "wrong_password", "refused"))
    ssd = show_ssd(page)
    page._remember_refusal(ssd, "locked")
    page.show_result(_hw_outcome(EraseReport("refused", "locked"), device=ssd))
    page.result_view.unlock_button.click()
    text, kind = state["messages"][0]
    assert "passt nicht" in text and kind == "warning"
    # No second try for this disk in this session: every try costs an attempt.
    assert not page.result_view.unlock_button.isVisible()
    page.show_result(_hw_outcome(EraseReport("refused", "locked"), device=ssd))
    assert not page.result_view.unlock_button.isVisible()
    page.unlock_disk(ssd)
    assert state["started"] == [ssd] and len(state["asked"]) == 1
    page.result_view.next_button.click()
    page.set_devices([make_device("F:", 1), ssd])
    page.select_device(ssd)
    assert not page.unlock_link.isVisible()
    other = sata_ssd(index=3, serial="OTHER-0001")
    page.show_result(_hw_outcome(EraseReport("refused", "locked"), device=other))
    assert page.result_view.unlock_button.isVisible()  # another disk may still try


def test_a_drive_with_someone_elses_password_offers_unlock_and_overwrite(page, monkeypatch):
    from wipe.hardware_erase import EraseReport

    _unlock_setup(page, monkeypatch)
    page.show_result(_hw_outcome(EraseReport("refused", "has_password")))
    view = page.result_view
    assert view.unlock_button.isVisible()
    assert view.overwrite_button.isVisible()   # unlocked until it is replugged: writable
    assert "Passwort" in view.summary_rows()["Grund"]
    ssd = show_ssd(page)
    page._remember_refusal(ssd, "has_password")
    page.result_view.next_button.click()
    page.set_devices([make_device("F:", 1), ssd])
    page.select_device(ssd)
    assert page.unlock_link.isVisible() and not page.hardware_card.isVisible()


def test_window_cannot_close_while_a_disk_is_being_unlocked(qtbot, app_config, gui_reset,
                                                             monkeypatch):
    from PySide6.QtGui import QCloseEvent

    import gui.app_window as app_window
    import gui.pages.delete_page as delete_page

    monkeypatch.setattr(delete_page, "list_devices", lambda: [])
    shown, asked = [], []
    monkeypatch.setattr(app_window, "show_message",
                        lambda parent, title, text, kind="info": shown.append(text))
    monkeypatch.setattr(app_window, "ask_yes_no", lambda *a, **k: asked.append(a) or True)
    window = app_window.AppWindow(app_config)
    qtbot.addWidget(window)

    class Running:
        def isRunning(self):  # noqa: N802
            return True

        def wait(self, _ms=0):
            raise AssertionError("closing must not wait for the unlock")

    window.delete_page.unlock_worker = Running()
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert asked == [] and shown and "entsperrt" in shown[0]
    finally:
        window.delete_page.unlock_worker = None
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_a_locked_disk_can_be_unlocked_from_the_selection(page, monkeypatch):
    state = _unlock_setup(page, monkeypatch)
    ssd = show_ssd(page)
    page._remember_refusal(ssd, "locked")
    page.select_device(ssd)
    assert "Passwort gesperrt" in page.hardware_hint.text()
    assert page.unlock_link.isVisible()
    page.unlock_link.click()
    assert state["started"] == [ssd]
    assert page.hardware_card.isVisible() and not page.unlock_link.isVisible()


def test_unlock_result_is_handled_on_the_gui_thread(qtbot, page, fake_disk, monkeypatch):
    """The real worker thread: its result must reach the page on the GUI thread."""
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    import gui.pages.delete_page as delete_page
    import wipe.secure_erase as secure_erase
    from wipe.secure_erase import UnlockResult

    monkeypatch.setattr(delete_page, "is_safe_to_wipe",
                        lambda device, allow_internal=False: (True, ""))
    monkeypatch.setattr(delete_page, "ask_yes_no", lambda *a, **k: True)
    monkeypatch.setattr(secure_erase, "unlock_drive",
                        lambda handle: UnlockResult(True, "unlocked"))
    threads = []
    monkeypatch.setattr(delete_page, "show_message",
                        lambda *a, **k: threads.append(QThread.currentThread()))
    ssd = show_ssd(page)
    page.unlock_disk(ssd)
    qtbot.waitUntil(lambda: bool(threads), timeout=5000)
    assert threads == [QApplication.instance().thread()]
    qtbot.waitUntil(lambda: page.unlock_worker is None, timeout=5000)


def test_unlock_worker_uses_the_gate_and_releases_everything(qtbot, fake_disk, monkeypatch):
    import wipe.secure_erase as secure_erase
    from gui.wipe_worker import UnlockWorker
    from wipe.secure_erase import UnlockResult

    handles = []
    monkeypatch.setattr(secure_erase, "unlock_drive",
                        lambda handle: handles.append(handle) or UnlockResult(True, "unlocked"))
    worker = UnlockWorker(sata_ssd())
    results = []
    worker.unlock_finished.connect(results.append)
    with qtbot.waitSignal(worker.unlock_finished, timeout=5000):
        worker.start()
    worker.wait(2000)
    assert fake_disk.events == ["gate"] and handles == [777]
    assert fake_disk.released == [11, 12] and fake_disk.closed == [777]
    assert results[0].state == "unlocked"


def test_unlock_worker_stops_at_a_refusing_gate(qtbot, fake_disk, monkeypatch):
    import gui.wipe_worker as wipe_worker
    import wipe.secure_erase as secure_erase
    from gui.wipe_worker import UnlockWorker
    from wipe.device import UnsafeTargetError

    def refuse(device):
        raise UnsafeTargetError("ist nicht mehr das ausgewählte Gerät / is no longer it")

    monkeypatch.setattr(wipe_worker, "prepare_target", refuse)
    monkeypatch.setattr(secure_erase, "unlock_drive",
                        lambda handle: pytest.fail("unlock without the gate"))
    worker = UnlockWorker(sata_ssd())
    results = []
    worker.unlock_finished.connect(results.append)
    with qtbot.waitSignal(worker.unlock_finished, timeout=5000):
        worker.start()
    worker.wait(2000)
    assert results[0].state == "error" and "is no longer it" in results[0].error
    assert fake_disk.closed == []


# -- Protection class 3 on an SSD (2.0 polish) ----------------------------------


def test_ssd_class_3_overwrite_says_destruction_is_required(page, no_drive_commands):
    from dataclasses import replace

    page.set_options(replace(page.options, schutzklasse=3))
    show_ssd(page)
    text = page.warning_label.text()
    assert page.warning_label.isVisible()
    assert "Reserve-Bereiche der SSD nicht" in text
    assert "physisch vernichtet" in text


def test_ssd_class_3_hardware_erase_still_says_destruction_is_required(
        page, monkeypatch, no_drive_commands):
    from dataclasses import replace

    page.set_options(replace(page.options, schutzklasse=3))
    show_ssd(page)
    choose_hardware(page, monkeypatch)
    assert page.options.hardware_erase is True
    text = page.warning_label.text()
    assert "Hardware-Löschen (Beta)" in text
    assert "physisch vernichtet" in text


def test_ssd_class_2_has_no_destruction_note(page, no_drive_commands):
    show_ssd(page)
    assert "vernichtet" not in page.warning_label.text()
