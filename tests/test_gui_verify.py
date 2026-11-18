"""Worker tests for verification, reformatting, the stall watchdog and the
volume locks (gui.wipe_worker.WipeWorker).

These tests cover:
    * WipeWorker keeps the verify mode and reformat choice it is given
    * WipeWorker emits phase_changed signals in the expected sequence when
      WipeMethod.execute() is mocked
    * the stall watchdog and the target re-checks before writing/reformatting

The window-level checks of the old main window moved to the page tests
(test_gui_delete_page.py, test_gui_wipe_defaults.py).

They use pytest-qt, which is already a dev dependency of the project. If the
Qt environment cannot start (no QApplication platform available), the tests
are skipped rather than hanging the suite.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# Force offscreen rendering so the tests don't pop windows on a dev machine
# and can run in CI sandboxes. Must be set before QApplication is created.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication  # noqa: F401

    QT_AVAILABLE = True
except Exception:  # noqa: BLE001
    QT_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not QT_AVAILABLE,
    reason="PySide6 / Qt platform not available in this environment",
)


# -- Fixtures -----------------------------------------------------------------


@pytest.fixture()
def app_config(tmp_path):
    """Return an AppConfig pointed at a tmp cert/log directory."""
    from core.config import AppConfig

    cfg = AppConfig()
    cfg.cert_output_dir = str(tmp_path / "certs")
    return cfg


@pytest.fixture()
def demo_device():
    """A virtual demo device (a temp file, never a real disk)."""
    from wipe.demo import create_demo_device

    return create_demo_device()


# -- Worker-level tests -------------------------------------------------------


def test_worker_accepts_verify_mode_kwarg(app_config):
    """Constructing a WipeWorker with verify_mode must persist the value."""
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill

    worker_full = WipeWorker(
        devices=[],
        wipe_method=ZeroFill(),
        config=app_config,
        verify_mode="full",
    )
    assert worker_full.verify_mode == "full"

    worker_sample = WipeWorker(
        devices=[],
        wipe_method=ZeroFill(),
        config=app_config,
        verify_mode="sample",
    )
    assert worker_sample.verify_mode == "sample"


def test_worker_falls_back_to_full_on_bad_verify_mode(app_config):
    """An invalid verify_mode string must fall back to 'full' (the default)."""
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill

    worker = WipeWorker(
        devices=[],
        wipe_method=ZeroFill(),
        config=app_config,
        verify_mode="bogus",
    )
    assert worker.verify_mode == "full"



def test_stall_watchdog_fires_when_no_progress(qtbot, app_config, demo_device):
    """If _note_progress is never called within STALL_THRESHOLD_SECONDS, the
    watchdog thread emits stall_detected with a non-zero second count and a
    human hint."""
    from wipe.methods import ZeroFill
    from gui.wipe_worker import WipeWorker

    worker = WipeWorker(
        devices=[demo_device],
        wipe_method=ZeroFill(),
        config=app_config,
        verify_mode="none",
    )
    # Shrink the threshold to ~1s so the test finishes fast.
    worker.STALL_THRESHOLD_SECONDS = 1
    # Prime the timer as if a wipe just started, then don't fire _note_progress.
    worker._note_progress()
    received: list[tuple[int, int, str]] = []
    worker.stall_detected.connect(
        lambda idx, sec, hint: received.append((idx, sec, hint))
    )
    worker._start_stall_watchdog(device_index=42)
    # The watchdog polls every 5s, so we need to wait a bit longer than that.
    # To avoid slowing down the suite we shortcut with a direct call instead:
    # manually advance _last_progress_time into the past so the next watchdog
    # tick trips the threshold immediately.
    import time as _time
    worker._last_progress_time = _time.monotonic() - 10  # 10s "ago"
    # Wait up to 8 seconds for the watchdog to emit.
    def _fired():
        return len(received) > 0
    qtbot.waitUntil(_fired, timeout=8000)
    assert len(received) >= 1
    idx, secs, hint = received[0]
    assert idx == 42
    assert secs >= 1
    assert "unplug" in hint.lower() or "replug" in hint.lower()
    # Cleanup: stop the watchdog thread.
    worker._last_progress_time = None


def test_stall_watchdog_does_not_fire_when_progress_is_active(qtbot, app_config, demo_device):
    """If _note_progress is called regularly, stall_detected must not fire."""
    from wipe.methods import ZeroFill
    from gui.wipe_worker import WipeWorker
    import time as _time

    worker = WipeWorker(
        devices=[demo_device],
        wipe_method=ZeroFill(),
        config=app_config,
        verify_mode="none",
    )
    worker.STALL_THRESHOLD_SECONDS = 2
    worker._note_progress()
    received: list[tuple[int, int, str]] = []
    worker.stall_detected.connect(
        lambda idx, sec, hint: received.append((idx, sec, hint))
    )
    worker._start_stall_watchdog(device_index=5)
    # Call _note_progress several times over ~3 seconds (busier than the
    # 5-second poll interval). We accept one-off stall signals as false
    # positives if the test runner is heavily loaded — tolerance of 0.
    for _ in range(6):
        worker._note_progress()
        _time.sleep(0.5)
    # Cleanup.
    worker._last_progress_time = None
    assert received == [], f"Unexpected stall signal(s): {received}"


def test_worker_emits_phase_changed_signals(qtbot, app_config, demo_device):
    """Running a worker against a demo device with verify_mode=sample must
    emit phase_changed(wiping), phase_changed(verifying), phase_changed(done)
    in that order for that device index."""
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill, WipeResult
    from wipe.verify import VerifyResult

    method = ZeroFill()

    # Patch demo helpers so no real I/O happens.
    with patch("gui.wipe_worker.create_demo_file", return_value="fake.bin"), \
         patch("gui.wipe_worker.wipe_demo_file") as mock_wipe, \
         patch("gui.wipe_worker.verify_demo_file") as mock_verify, \
         patch("gui.wipe_worker.generate_certificate", return_value="fake_cert.pdf"), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("os.remove"):

        mock_wipe.return_value = WipeResult(
            method_name="ZeroFill",
            passes=1,
            start_time=datetime.now(),
            end_time=datetime.now(),
            bytes_written=1024,
            success=True,
            error_message=None,
        )
        mock_verify.return_value = VerifyResult(
            success=True,
            method="sample",
            bytes_verified=1024,
            expected_pattern="zeros",
            error_count=0,
            mismatch_offsets=[],
            duration_seconds=0.01,
            sectors_checked=2,
            sectors_matched=2,
            sample_hash="cafebabe",
            timestamp=datetime.now(),
        )

        worker = WipeWorker(
            devices=[demo_device],
            wipe_method=method,
            config=app_config,
            verify_mode="sample",
        )

        phases: list[tuple[int, str]] = []
        worker.phase_changed.connect(lambda idx, p: phases.append((idx, p)))

        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

        phase_names = [p for _, p in phases]
        assert phase_names[0] == "wiping"
        assert "verifying" in phase_names
        assert phase_names[-1] == "done"


def test_worker_verify_mode_none_skips_verifying_phase(qtbot, app_config, demo_device):
    """When verify_mode='none' the worker should go straight wiping -> done."""
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill, WipeResult

    method = ZeroFill()

    with patch("gui.wipe_worker.create_demo_file", return_value="fake.bin"), \
         patch("gui.wipe_worker.wipe_demo_file") as mock_wipe, \
         patch("gui.wipe_worker.verify_demo_file") as mock_verify, \
         patch("gui.wipe_worker.generate_certificate", return_value="fake_cert.pdf"), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("os.remove"):

        mock_wipe.return_value = WipeResult(
            method_name="ZeroFill",
            passes=1,
            start_time=datetime.now(),
            end_time=datetime.now(),
            bytes_written=1024,
            success=True,
            error_message=None,
        )

        worker = WipeWorker(
            devices=[demo_device],
            wipe_method=method,
            config=app_config,
            verify_mode="none",
        )

        phases: list[tuple[int, str]] = []
        worker.phase_changed.connect(lambda idx, p: phases.append((idx, p)))

        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

        phase_names = [p for _, p in phases]
        assert phase_names == ["wiping", "done"]
        # And verify_demo_file must not have been called.
        mock_verify.assert_not_called()


# -- v1.1: reformat-phase tests ----------------------------------------------


def test_worker_accepts_reformat_kwarg(app_config):
    """Constructing a WipeWorker with reformat must persist the value."""
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill

    worker = WipeWorker(
        devices=[],
        wipe_method=ZeroFill(),
        config=app_config,
        reformat="exfat",
    )
    assert worker.reformat == "exfat"
    # Default label/partition come through unchanged.
    assert worker.reformat_label == "USB"
    assert worker.reformat_partition == "MBR"

    worker2 = WipeWorker(
        devices=[],
        wipe_method=ZeroFill(),
        config=app_config,
        reformat="fat32",
        reformat_label="ARCHIVE",
        reformat_partition="GPT",
    )
    assert worker2.reformat == "fat32"
    assert worker2.reformat_label == "ARCHIVE"
    assert worker2.reformat_partition == "GPT"


def test_worker_rejects_invalid_reformat(app_config):
    """An invalid reformat string must fall back to 'none' (with a warning log)."""
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill

    with patch("gui.wipe_worker.audit_log") as mock_log:
        worker = WipeWorker(
            devices=[],
            wipe_method=ZeroFill(),
            config=app_config,
            reformat="xfs",  # not a Windows filesystem we support
        )

    assert worker.reformat == "none"
    # An audit_log call should mention the rejected value and the fallback.
    log_messages = " ".join(
        str(call.args[0]) for call in mock_log.call_args_list if call.args
    )
    assert "xfs" in log_messages
    assert "none" in log_messages


def test_worker_emits_reformatting_phase_when_requested(
    qtbot, app_config, demo_device
):
    """When reformat != 'none', the worker must emit a 'reformatting' phase
    after wiping (and before 'done')."""
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill, WipeResult
    from wipe.format import FormatResult

    method = ZeroFill()

    fake_format = FormatResult(
        success=True,
        method="demo",
        filesystem="exfat",
        label="USB",
        partition_style="MBR",
        duration_seconds=0.01,
        error_message=None,
    )

    fake_wipe_result = WipeResult(
        method_name="ZeroFill",
        passes=1,
        start_time=datetime.now(),
        end_time=datetime.now(),
        bytes_written=1024,
        success=True,
        error_message=None,
    )
    # Agent C attaches the format result to the WipeResult before returning.
    fake_wipe_result.format_result = fake_format

    with patch("gui.wipe_worker.create_demo_file", return_value="fake.bin"), \
         patch("gui.wipe_worker.wipe_demo_file", return_value=fake_wipe_result), \
         patch("gui.wipe_worker.verify_demo_file"), \
         patch("gui.wipe_worker.generate_certificate", return_value="fake_cert.pdf"), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("os.remove"):

        worker = WipeWorker(
            devices=[demo_device],
            wipe_method=method,
            config=app_config,
            verify_mode="none",
            reformat="exfat",
        )

        phases: list[tuple[int, str]] = []
        worker.phase_changed.connect(lambda idx, p: phases.append((idx, p)))

        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

        phase_names = [p for _, p in phases]
        assert "reformatting" in phase_names
        # Reformat must come after the initial 'wiping' phase and before 'done'.
        wiping_idx = phase_names.index("wiping")
        reformatting_idx = phase_names.index("reformatting")
        done_idx = phase_names.index("done")
        assert wiping_idx < reformatting_idx < done_idx


# -- 1.1.1: target re-checks ----------------------------------------------------


def test_worker_refuses_real_device_when_target_unsafe(qtbot, app_config):
    """1.1.1: a real (non-demo) device that fails prepare_target is reported
    as failed and the physical disk is never opened."""
    from gui.wipe_worker import WipeWorker
    from wipe.device import DeviceInfo, UnsafeTargetError
    from wipe.methods import ZeroFill

    device = DeviceInfo(
        drive_letter="E:", device_id=r"\\.\PhysicalDrive7", model="Stick",
        serial_number="SN7", capacity_bytes=1024**3, filesystem="FAT32",
        connection_type="USB", is_removable=True, is_system_drive=False,
        disk_letters=["E:"],
    )
    opener = MagicMock(return_value=1)
    with patch("gui.wipe_worker.prepare_target",
               side_effect=UnsafeTargetError("swapped")), \
         patch("gui.wipe_worker.open_physical_drive", opener), \
         patch("gui.wipe_worker.close_drive"), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.audit_log"):
        worker = WipeWorker(devices=[device], wipe_method=ZeroFill(),
                            config=app_config, verify_mode="none")
        results: list[tuple[int, bool]] = []
        worker.device_completed.connect(lambda i, ok, _p: results.append((i, ok)))
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

    assert results == [(0, False)]
    opener.assert_not_called()


def test_worker_rechecks_target_before_reformat(qtbot, app_config):
    """Reformat rewrites the partition table of a real disk, so the worker runs
    prepare_target again right before it. A refusal skips the reformat; the
    wipe itself stays successful and its certificate is still issued."""
    from types import SimpleNamespace
    from gui.wipe_worker import WipeWorker
    from wipe.device import DeviceInfo, UnsafeTargetError
    from wipe.methods import WipeResult

    device = DeviceInfo(
        drive_letter="E:", device_id=r"\\.\PhysicalDrive7", model="Stick",
        serial_number="SN7", capacity_bytes=1024**2, filesystem="FAT32",
        connection_type="USB", is_removable=True, is_system_drive=False,
        disk_letters=["E:"],
    )
    now = datetime.now()
    method = MagicMock()
    method.name = "ZeroFill"
    method.passes = 1
    method.sicherheitsstufe = "H-2"
    method.execute.return_value = WipeResult(
        method_name="ZeroFill", passes=1, start_time=now, end_time=now,
        bytes_written=1024**2, success=True, error_message=None,
    )
    no_hidden = SimpleNamespace(supported=False, hpa_present=False, dco_present=False,
                                hpa_hidden_sectors=0, dco_hidden_sectors=0)
    gate = MagicMock(side_effect=[[], UnsafeTargetError("swapped")])
    reformat = MagicMock()
    with patch("gui.wipe_worker.prepare_target", gate), \
         patch("gui.wipe_worker.open_physical_drive", return_value=1), \
         patch("gui.wipe_worker.get_drive_size", return_value=1024**2), \
         patch("gui.wipe_worker.detect_hidden_areas", return_value=no_hidden), \
         patch("gui.wipe_worker.close_drive"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("gui.wipe_worker.generate_certificate", return_value="x.pdf") as cert, \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.audit_log"), \
         patch("wipe.format.reformat_drive", reformat):
        worker = WipeWorker(devices=[device], wipe_method=method, config=app_config,
                            verify_mode="none", reformat="exfat")
        results: list[tuple[int, bool]] = []
        worker.device_completed.connect(lambda i, ok, _p: results.append((i, ok)))
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

    assert gate.call_count == 2
    reformat.assert_not_called()
    cert.assert_called_once()
    assert results == [(0, True)]


def _gui_lock_test_device():
    from wipe.device import DeviceInfo
    return DeviceInfo(
        drive_letter="E:", device_id=r"\\.\PhysicalDrive7", model="Stick",
        serial_number="SN7", capacity_bytes=1024**2, filesystem="FAT32",
        connection_type="USB", is_removable=True, is_system_drive=False,
        disk_letters=["E:"],
    )


def _gui_lock_test_method(execute):
    method = MagicMock()
    method.name = "ZeroFill"
    method.passes = 1
    method.sicherheitsstufe = "H-2"
    method.execute.side_effect = execute
    return method


def _ok_wipe_result():
    from wipe.methods import WipeResult
    now = datetime.now()
    return WipeResult(
        method_name="ZeroFill", passes=1, start_time=now, end_time=now,
        bytes_written=1024**2, success=True, error_message=None,
    )


def test_worker_holds_volume_locks_for_the_whole_wipe(qtbot, app_config):
    """1.1.2: the volume locks from prepare_target stay held while the wipe
    and verification run, and are released exactly once afterwards."""
    from types import SimpleNamespace
    from gui.wipe_worker import WipeWorker

    released: list[int] = []
    held_during_execute: list[bool] = []

    def fake_execute(*_a, **_k):
        held_during_execute.append(released == [])
        return _ok_wipe_result()

    no_hidden = SimpleNamespace(supported=False, hpa_present=False, dco_present=False,
                                hpa_hidden_sectors=0, dco_hidden_sectors=0)
    with patch("gui.wipe_worker.prepare_target", return_value=[7, 8]),          patch("gui.wipe_worker.release_volumes", side_effect=released.extend),          patch("gui.wipe_worker.open_physical_drive", return_value=1),          patch("gui.wipe_worker.get_drive_size", return_value=1024**2),          patch("gui.wipe_worker.detect_hidden_areas", return_value=no_hidden),          patch("gui.wipe_worker.close_drive"),                    patch("gui.wipe_worker.generate_certificate", return_value="c.pdf"),          patch("gui.wipe_worker.log_wipe_to_csv"),          patch("gui.wipe_worker.get_next_cert_number", return_value=1),          patch("gui.wipe_worker.audit_log"):
        worker = WipeWorker(devices=[_gui_lock_test_device()],
                            wipe_method=_gui_lock_test_method(fake_execute),
                            config=app_config, verify_mode="none")
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

    assert held_during_execute == [True]
    assert released == [7, 8]


def test_worker_releases_the_locks_when_closing_the_drive_fails(qtbot, app_config):
    """Review: an exception from close_drive in the cleanup (e.g. its audit
    log) must not skip release_volumes or end the worker without a result."""
    from types import SimpleNamespace
    from gui.wipe_worker import WipeWorker

    released: list[int] = []
    errors: list[tuple[int, str]] = []

    def failing_execute(*_a, **_k):
        raise RuntimeError("write failed (error 1167)")

    no_hidden = SimpleNamespace(supported=False, hpa_present=False, dco_present=False,
                                hpa_hidden_sectors=0, dco_hidden_sectors=0)
    with patch("gui.wipe_worker.prepare_target", return_value=[7, 8]), \
         patch("gui.wipe_worker.release_volumes", side_effect=released.extend), \
         patch("gui.wipe_worker.open_physical_drive", return_value=1), \
         patch("gui.wipe_worker.get_drive_size", return_value=1024**2), \
         patch("gui.wipe_worker.detect_hidden_areas", return_value=no_hidden), \
         patch("gui.wipe_worker.close_drive", side_effect=OSError("log file locked")), \
         patch("gui.wipe_worker.generate_certificate", return_value="c.pdf"), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("gui.wipe_worker.audit_log"):
        worker = WipeWorker(devices=[_gui_lock_test_device()],
                            wipe_method=_gui_lock_test_method(failing_execute),
                            config=app_config, verify_mode="none")
        worker.error.connect(lambda i, msg: errors.append((i, msg)))
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

    assert released == [7, 8]
    assert errors and "1167" in errors[0][1]


def test_worker_releases_the_gate_locks_before_reformatting(qtbot, app_config):
    """The safety re-check before reformat locks the volumes again; Clear-Disk
    needs the disk free, so those locks are released before the reformat."""
    from types import SimpleNamespace
    from gui.wipe_worker import WipeWorker

    released: list[int] = []
    seen_at_reformat: list[list[int]] = []

    def fake_reformat(**_kw):
        seen_at_reformat.append(list(released))
        return SimpleNamespace(success=True, filesystem="exfat", label="USB",
                               error_message=None)

    no_hidden = SimpleNamespace(supported=False, hpa_present=False, dco_present=False,
                                hpa_hidden_sectors=0, dco_hidden_sectors=0)
    gate = MagicMock(side_effect=[[7], [9]])
    with patch("gui.wipe_worker.prepare_target", gate),          patch("gui.wipe_worker.release_volumes", side_effect=released.extend),          patch("gui.wipe_worker.open_physical_drive", return_value=1),          patch("gui.wipe_worker.get_drive_size", return_value=1024**2),          patch("gui.wipe_worker.detect_hidden_areas", return_value=no_hidden),          patch("gui.wipe_worker.close_drive"),                    patch("gui.wipe_worker.generate_certificate", return_value="c.pdf"),          patch("gui.wipe_worker.log_wipe_to_csv"),          patch("gui.wipe_worker.get_next_cert_number", return_value=1),          patch("gui.wipe_worker.audit_log"),          patch("wipe.format.reformat_drive", side_effect=fake_reformat):
        worker = WipeWorker(devices=[_gui_lock_test_device()],
                            wipe_method=_gui_lock_test_method(lambda *a, **k: _ok_wipe_result()),
                            config=app_config, verify_mode="none", reformat="exfat")
        results: list[tuple[int, bool]] = []
        worker.device_completed.connect(lambda i, ok, _p: results.append((i, ok)))
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

    assert gate.call_count == 2
    assert seen_at_reformat == [[7, 9]]
    assert released == [7, 9]
    assert results == [(0, True)]


# -- Partition table removed right before the first write ---------------------


def _run_real_device_worker(qtbot, app_config, events, *, clear_ok=True, cancel=False):
    """Run the worker on a fake real disk; every step is recorded in ``events``."""
    from types import SimpleNamespace
    from gui.wipe_worker import WipeWorker

    def gate(device):
        events.append("gate")
        return [7, 8]

    def opener(device_id):
        events.append("open")
        return 1

    def clear(handle):
        events.append(("clear", handle))
        return clear_ok

    def fake_execute(handle, *_a, **_k):
        events.append(("write", handle))
        return _ok_wipe_result()

    no_hidden = SimpleNamespace(supported=False, hpa_present=False, dco_present=False,
                                hpa_hidden_sectors=0, dco_hidden_sectors=0)
    worker_box: list = []

    def probe(handle):
        if cancel:  # the user cancels while the disk is being looked at
            worker_box[0].cancel()
        return no_hidden

    results: list[tuple[int, bool]] = []
    with patch("gui.wipe_worker.prepare_target", side_effect=gate), \
         patch("gui.wipe_worker.release_volumes",
               side_effect=lambda locks: events.append(("release", list(locks)))), \
         patch("gui.wipe_worker.open_physical_drive", side_effect=opener), \
         patch("gui.wipe_worker.clear_partition_table", side_effect=clear), \
         patch("gui.wipe_worker.get_drive_size", return_value=1024**2), \
         patch("gui.wipe_worker.detect_hidden_areas", side_effect=probe), \
         patch("gui.wipe_worker.close_drive"), \
         patch("gui.wipe_worker.generate_certificate", return_value="c.pdf"), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("gui.wipe_worker.audit_log"):
        worker = WipeWorker(devices=[_gui_lock_test_device()],
                            wipe_method=_gui_lock_test_method(fake_execute),
                            config=app_config, verify_mode="none")
        worker_box.append(worker)
        worker.device_completed.connect(lambda i, ok, _p: results.append((i, ok)))
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()
        worker.wait(2000)
    # The cleanup also hands over the (by then empty) rest of the locks.
    events[:] = [e for e in events if e != ("release", [])]
    return results


def test_worker_removes_the_partition_table_after_the_gate_and_before_writing(
        qtbot, app_config):
    """A Rufus disk's read-only UEFI_NTFS partition blocks raw writes even
    with every volume locked; the layout goes first, while the locks hold."""
    events: list = []
    results = _run_real_device_worker(qtbot, app_config, events)

    assert events == ["gate", "open", ("clear", 1), ("write", 1), ("release", [7, 8])]
    assert results == [(0, True)]


def test_worker_wipes_on_when_the_partition_table_cannot_be_removed(qtbot, app_config):
    events: list = []
    results = _run_real_device_worker(qtbot, app_config, events, clear_ok=False)

    assert events == ["gate", "open", ("clear", 1), ("write", 1), ("release", [7, 8])]
    assert results == [(0, True)]


def test_worker_cancelled_before_writing_keeps_the_partition_table(qtbot, app_config):
    events: list = []
    results = _run_real_device_worker(qtbot, app_config, events, cancel=True)

    assert not any(isinstance(e, tuple) and e[0] in ("clear", "write") for e in events)
    assert results == [(0, False)]


def test_demo_wipe_never_removes_a_partition_table(qtbot, app_config, demo_device):
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill

    clear = MagicMock(return_value=True)
    with patch("gui.wipe_worker.clear_partition_table", clear), \
         patch("gui.wipe_worker.create_demo_file", return_value="fake.bin"), \
         patch("gui.wipe_worker.wipe_demo_file", return_value=_ok_wipe_result()), \
         patch("gui.wipe_worker.generate_certificate", return_value="c.pdf"), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("os.remove"):
        worker = WipeWorker(devices=[demo_device], wipe_method=ZeroFill(),
                            config=app_config, verify_mode="none")
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()
        worker.wait(2000)

    clear.assert_not_called()


@pytest.mark.parametrize("letter, disk_letters, named", [
    ("", [], "\\\\.\\PhysicalDrive7"),
    ("E:", ["E:"], "E:"),
])
def test_lock_skipped_log_names_the_disk_without_a_letter(qtbot, app_config,
                                                          letter, disk_letters, named):
    """18.11.2026: "Physical-drive lock skipped for  (...)" had an empty name
    for a disk without a letter; it names the letter or the device id."""
    from dataclasses import replace
    from types import SimpleNamespace
    from gui.wipe_worker import WipeWorker

    device = replace(_gui_lock_test_device(), drive_letter=letter, disk_letters=disk_letters)
    no_hidden = SimpleNamespace(supported=False, hpa_present=False, dco_present=False,
                                hpa_hidden_sectors=0, dco_hidden_sectors=0)
    log = MagicMock()
    with patch("gui.wipe_worker.prepare_target", return_value=[]), \
         patch("gui.wipe_worker.release_volumes"), \
         patch("gui.wipe_worker.open_physical_drive", return_value=1), \
         patch("gui.wipe_worker.get_drive_size", return_value=1024**2), \
         patch("gui.wipe_worker.detect_hidden_areas", return_value=no_hidden), \
         patch("gui.wipe_worker.close_drive"), \
         patch("gui.wipe_worker.generate_certificate", return_value="c.pdf"), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("gui.wipe_worker.audit_log", log):
        worker = WipeWorker(devices=[device],
                            wipe_method=_gui_lock_test_method(lambda *a, **k: _ok_wipe_result()),
                            config=app_config, verify_mode="none")
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()

    lines = [c.args[0] for c in log.call_args_list if "lock skipped" in c.args[0]]
    assert lines == [f"Physical-drive lock skipped for {named} (dismount already exclusive)"]
