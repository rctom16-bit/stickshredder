"""wipe.hardware_erase — the one hardware-erase procedure the CLI and the GUI run.

Order (spec G2): ask the drive whether its own erase is ready; refuse before
anything is written when it is not; otherwise the drive erases itself, then
one pass of zeros plus the chosen verification. Only fakes: the drive-command
functions of ``wipe.secure_erase`` are replaced in every test.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

import wipe.hardware_erase as hw
import wipe.secure_erase as se
from wipe.methods import WipeResult
from wipe.secure_erase import SecureEraseResult

HANDLE = 4321
SIZE = 8 * 1024 * 1024


def _status(method="ata-enhanced", *, supported=True, frozen=False, error=None,
            estimated_minutes=2) -> SecureEraseResult:
    return SecureEraseResult(success=False, method=method, supported=supported,
                             frozen=frozen, duration_seconds=0.01, error=error,
                             estimated_minutes=estimated_minutes)


def _erased(method="ata-enhanced") -> SecureEraseResult:
    return SecureEraseResult(success=True, method=method, supported=True, frozen=False,
                             duration_seconds=42.0, error=None)


class FakeZeroFill:
    """Stand-in for the overwrite after the erase (records the call, writes nothing)."""

    name = "ZeroFill"
    passes = 1
    sicherheitsstufe = "1-2"

    def __init__(self, events, *, success=True):
        self.events = events
        self.success = success
        self.kwargs = None

    def execute(self, **kwargs):
        self.events.append("overwrite")
        self.kwargs = kwargs
        now = datetime.now()
        verify = SimpleNamespace(success=True, method=kwargs.get("verify_mode"),
                                 bytes_verified=SIZE, error_count=0)
        return WipeResult(method_name="ZeroFill", passes=1, start_time=now, end_time=now,
                          bytes_written=SIZE, success=self.success,
                          error_message=None if self.success else "write failed",
                          verify_result=verify, bad_sector_count=2, bad_sector_bytes=1024,
                          bad_sector_offsets=[0, 512])


@pytest.fixture()
def drive(monkeypatch):
    """Fake drive: status probe + erase recorded in ``events``; no real command."""
    state = SimpleNamespace(events=[], status=_status(), erase=_erased(), erase_kwargs=None,
                            power=[], logs=[])

    def supported(handle):
        assert handle == HANDLE
        state.events.append("probe")
        return state.status

    def erase(handle, **kwargs):
        assert handle == HANDLE
        state.events.append("erase")
        state.erase_kwargs = kwargs
        if isinstance(state.erase, Exception):
            raise state.erase
        return state.erase

    monkeypatch.setattr(se, "secure_erase_supported", supported)
    monkeypatch.setattr(se, "secure_erase", erase)
    monkeypatch.setattr(hw, "_set_execution_state",
                        lambda flags: state.power.append(flags))
    monkeypatch.setattr(hw, "audit_log", lambda msg: state.logs.append(msg))
    return state


# ── Readiness ────────────────────────────────────────────────────────


@pytest.mark.parametrize("status, reason", [
    (_status(frozen=True, method="frozen"), "frozen"),
    (_status(method="unsupported", supported=False,
             error="USB device with vendor id 'ASMT' is not an ATA drive"), "no_path"),
    (_status(method="unsupported", supported=False), "unsupported"),
    (_status(method="locked"), "locked"),
    (_status(method="has_password"), "has_password"),
    (_status(method="error", supported=False, error="IDENTIFY failed"), "error"),
])
def test_check_ready_names_why_the_drive_may_not_erase(status, reason):
    refusal = hw.check_ready(status)
    assert refusal is not None
    assert refusal.reason == reason
    assert refusal.detail


@pytest.mark.parametrize("method", ["ata-enhanced", "ata-secure-erase"])
def test_check_ready_accepts_a_ready_drive(method):
    assert hw.check_ready(_status(method=method)) is None


def test_erase_label_names_the_variant():
    assert hw.erase_label("ata-enhanced") == "ATA Secure Erase (enhanced)"
    assert hw.erase_label("ata-secure-erase") == "ATA Secure Erase"


# ── Refused before anything is written ───────────────────────────────


@pytest.mark.parametrize("status, reason", [
    (_status(frozen=True, method="frozen"), "frozen"),
    (_status(method="unsupported", supported=False, error="NVMe disk"), "no_path"),
    (_status(method="unsupported", supported=False), "unsupported"),
])
def test_not_ready_drive_gets_no_erase_and_no_write(drive, status, reason):
    drive.status = status
    zero = FakeZeroFill(drive.events)
    started, finished = [], []

    outcome = hw.run(HANDLE, SIZE, zero, on_erase_started=lambda *a: started.append(a),
                     on_erase_finished=finished.append)

    assert drive.events == ["probe"]
    assert outcome.report.status == "refused"
    assert outcome.report.reason == reason
    assert outcome.report.password_may_be_set is False
    assert outcome.wipe_result is None and outcome.erase is None
    assert started == [] and drive.power == []
    assert finished == [outcome.report]  # the caller always hears the outcome


# ── Erase, then zeros + verification ─────────────────────────────────


def test_ready_drive_erases_itself_then_gets_zeros_and_verification(drive):
    zero = FakeZeroFill(drive.events)
    seen = []

    def progress(*a):
        pass

    def verify_progress(*a):
        pass

    outcome = hw.run(
        HANDLE, SIZE, zero,
        progress_callback=progress, verify_progress_callback=verify_progress,
        on_erase_started=lambda method, minutes: seen.append(("started", method, minutes)),
        on_erase_finished=lambda report: seen.append(("finished", report.status)),
    )

    assert drive.events == ["probe", "erase", "overwrite"]
    assert drive.erase_kwargs["enhanced"] is True
    assert drive.erase_kwargs["password"] == hw.TEMP_PASSWORD
    assert seen == [("started", "ata-enhanced", 2), ("finished", "erased")]
    assert zero.kwargs["handle"] == HANDLE and zero.kwargs["drive_size"] == SIZE
    assert zero.kwargs["verify_mode"] == "full"
    assert zero.kwargs["progress_callback"] is progress
    assert zero.kwargs["verify_progress_callback"] is verify_progress

    report = outcome.report
    assert (report.status, report.method, report.estimated_minutes) == ("erased", "ata-enhanced", 2)
    assert report.duration_seconds == 42.0
    result = outcome.wipe_result
    assert result.method_name == "ATA Secure Erase (enhanced) + ZeroFill"
    assert result.success is True and result.passes == 1
    assert result.verify_result.method == "full"
    assert (result.bad_sector_count, result.bad_sector_offsets) == (2, [0, 512])
    assert outcome.erase.method == "ata-enhanced"


def test_drive_without_enhanced_erase_gets_the_normal_erase(drive):
    drive.status = _status(method="ata-secure-erase")
    drive.erase = _erased("ata-secure-erase")
    outcome = hw.run(HANDLE, SIZE, FakeZeroFill(drive.events))
    assert drive.erase_kwargs["enhanced"] is False
    assert outcome.wipe_result.method_name == "ATA Secure Erase + ZeroFill"


def test_failed_overwrite_after_the_erase_is_a_failed_wipe(drive):
    outcome = hw.run(HANDLE, SIZE, FakeZeroFill(drive.events, success=False))
    assert outcome.report.status == "erased"  # the drive did erase itself
    assert outcome.wipe_result.success is False
    assert outcome.wipe_result.error_message == "write failed"


def test_the_temporary_password_is_logged_before_the_erase(drive, monkeypatch):
    order = []
    drive.logs = order
    fake_erase = se.secure_erase

    def erase(handle, **kwargs):
        order.append("ERASE")
        return fake_erase(handle, **kwargs)

    monkeypatch.setattr(se, "secure_erase", erase)
    hw.run(HANDLE, SIZE, FakeZeroFill(drive.events))
    password_line = next(i for i, line in enumerate(order)
                         if isinstance(line, str) and hw.TEMP_PASSWORD in line)
    assert password_line < order.index("ERASE")


def test_the_pc_is_kept_awake_only_while_the_drive_erases(drive, monkeypatch):
    def erase(handle, **kwargs):
        drive.events.append(("erase", list(drive.power)))
        return _erased()

    monkeypatch.setattr(se, "secure_erase", erase)
    hw.run(HANDLE, SIZE, FakeZeroFill(drive.events))
    awake = hw.ES_CONTINUOUS | hw.ES_SYSTEM_REQUIRED
    assert ("erase", [awake]) in drive.events
    assert drive.power == [awake, hw.ES_CONTINUOUS]


# ── The erase itself fails ───────────────────────────────────────────


def test_failed_erase_stops_before_the_overwrite_and_warns_about_the_password(drive):
    drive.erase = SecureEraseResult(success=False, method="error", supported=True,
                                    frozen=False, duration_seconds=3.0,
                                    error="SECURITY ERASE UNIT failed", password_set=True)
    zero = FakeZeroFill(drive.events)
    outcome = hw.run(HANDLE, SIZE, zero)
    assert "overwrite" not in drive.events
    assert outcome.report.status == "failed"
    assert outcome.report.password_may_be_set is True
    assert "SECURITY ERASE UNIT failed" in outcome.report.detail
    assert outcome.wipe_result is None


def test_drive_that_refuses_at_erase_time_is_refused_without_password_risk(drive):
    drive.erase = SecureEraseResult(success=False, method="frozen", supported=True,
                                    frozen=True, duration_seconds=0.0, error=None)
    outcome = hw.run(HANDLE, SIZE, FakeZeroFill(drive.events))
    assert outcome.report.status == "refused"
    assert outcome.report.reason == "frozen"
    assert outcome.report.password_may_be_set is False
    assert "overwrite" not in drive.events


def test_exception_in_the_erase_is_a_failure_and_the_pc_may_sleep_again(drive):
    drive.erase = OSError("bridge gone")
    outcome = hw.run(HANDLE, SIZE, FakeZeroFill(drive.events))
    assert outcome.report.status == "failed"
    assert outcome.report.password_may_be_set is True
    assert "bridge gone" in outcome.report.detail
    assert "overwrite" not in drive.events
    assert drive.power[-1] == hw.ES_CONTINUOUS


# ── Review fixes ─────────────────────────────────────────────────────


@pytest.mark.parametrize("minutes, timeout", [
    (0, 7200), (2, 7200), (60, 7200), (61, 7320), (120, 14400),
])
def test_erase_timeout_is_two_hours_or_twice_the_drive_estimate(drive, minutes, timeout):
    drive.status = _status(estimated_minutes=minutes)
    hw.run(HANDLE, SIZE, FakeZeroFill(drive.events))
    assert drive.erase_kwargs["timeout_seconds"] == timeout


def test_the_overwrite_after_the_erase_is_always_fully_verified(drive):
    zero = FakeZeroFill(drive.events)
    hw.run(HANDLE, SIZE, zero)
    assert zero.kwargs["verify_mode"] == "full"
    with pytest.raises(TypeError):
        hw.run(HANDLE, SIZE, zero, verify_mode="sample")  # not the caller's choice


def test_a_locked_drive_is_refused_before_the_password_is_shown(drive):
    drive.status = _status(method="locked")
    started = []
    outcome = hw.run(HANDLE, SIZE, FakeZeroFill(drive.events),
                     on_erase_started=lambda *a: started.append(a))
    assert (outcome.report.status, outcome.report.reason) == ("refused", "locked")
    assert drive.events == ["probe"] and started == []
    assert not any(hw.TEMP_PASSWORD in line for line in drive.logs)


@pytest.mark.parametrize("still_set", [True, False])
def test_a_failed_erase_takes_the_password_state_from_the_drive(drive, still_set):
    drive.erase = SecureEraseResult(success=False, method="error", supported=True,
                                    frozen=False, duration_seconds=1.0,
                                    error="SECURITY ERASE UNIT failed", password_set=still_set)
    outcome = hw.run(HANDLE, SIZE, FakeZeroFill(drive.events))
    assert outcome.report.status == "failed"
    assert outcome.report.password_may_be_set is still_set


@pytest.mark.parametrize("status", [_status(), _status(method="frozen", frozen=True)])
def test_a_broken_audit_log_still_reports_the_outcome(drive, monkeypatch, status):
    def broken(_message):
        raise OSError("disk full")

    monkeypatch.setattr(hw, "audit_log", broken)
    drive.status = status
    finished = []
    outcome = hw.run(HANDLE, SIZE, FakeZeroFill(drive.events),
                     on_erase_finished=finished.append)
    assert finished == [outcome.report]


def test_the_outcome_is_reported_before_it_is_logged(drive, monkeypatch):
    order = []
    monkeypatch.setattr(hw, "audit_log", lambda msg: order.append("log"))
    hw.run(HANDLE, SIZE, FakeZeroFill(drive.events),
           on_erase_finished=lambda report: order.append("finished"))
    assert order == ["log", "finished", "log"]  # password line, outcome, outcome line


def test_fits_only_directly_attached_sata_ssds():
    from dataclasses import replace

    from wipe.device import DeviceInfo

    ssd = DeviceInfo(drive_letter="", device_id="\\\\.\\PhysicalDrive2", model="SSD",
                     serial_number="S1", capacity_bytes=10, filesystem="RAW",
                     connection_type="SATA", is_removable=False, is_system_drive=False)
    assert hw.fits(ssd, "ssd")
    assert hw.fits(replace(ssd, connection_type="ATA"), "ssd")
    for connection in ("USB", "NVMe", "RAID", "SCSI", ""):
        assert not hw.fits(replace(ssd, connection_type=connection), "ssd")
    for media in ("hdd", "flash", "unknown"):
        assert not hw.fits(ssd, media)
    assert not hw.fits(replace(ssd, is_system_drive=True), "ssd")


def test_a_drive_with_a_password_is_refused_before_ours_is_shown(drive):
    drive.status = _status(method="has_password")
    started = []
    outcome = hw.run(HANDLE, SIZE, FakeZeroFill(drive.events),
                     on_erase_started=lambda *a: started.append(a))
    assert (outcome.report.status, outcome.report.reason) == ("refused", "has_password")
    assert drive.events == ["probe"] and started == []
    assert not any(hw.TEMP_PASSWORD in line for line in drive.logs)
