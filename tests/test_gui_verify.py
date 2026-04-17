"""GUI tests for the v1.1 Full-Verification feature.

These tests cover:
    * Default state of the "Full verification" checkbox in MainWindow
    * The checkbox propagates to WipeWorker.verify_mode
    * WipeWorker emits phase_changed signals in the expected sequence when
      WipeMethod.execute() is mocked

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
    """A virtual demo device so MainWindow has something selectable."""
    from wipe.demo import create_demo_device

    return create_demo_device()


@pytest.fixture()
def main_window(qtbot, app_config):
    """Build a MainWindow without running the deferred device scan."""
    from gui.main_window import MainWindow

    window = MainWindow(app_config)
    qtbot.addWidget(window)
    return window


# -- Phase-label helper tests (pure, no Qt event loop needed) -----------------


def test_phase_label_helper_returns_dual_language_strings():
    """Plain unit test for the static phase_text helper."""
    from gui.main_window import MainWindow

    assert "Bereit" in MainWindow._phase_text("idle")
    assert "Ready" in MainWindow._phase_text("idle")
    assert "Wiping" in MainWindow._phase_text("wiping")
    assert "\u00dcberschreiben" in MainWindow._phase_text("wiping")
    assert "Verifying" in MainWindow._phase_text("verifying")
    assert "Verifikation" in MainWindow._phase_text("verifying")
    assert "Complete" in MainWindow._phase_text("done")
    assert "Fertig" in MainWindow._phase_text("done")


def test_phase_style_helper_returns_different_colors_per_phase():
    """Each phase should have a visually distinct stylesheet."""
    from gui.main_window import MainWindow

    styles = {p: MainWindow._phase_style(p) for p in ("idle", "wiping", "verifying", "done")}
    # Sanity: all four are non-empty and distinct.
    assert all(styles.values())
    assert len(set(styles.values())) == 4


# -- MainWindow checkbox + wiring --------------------------------------------


def test_checkbox_default_unchecked(main_window):
    """The Full-Verification checkbox must exist and start off."""
    assert hasattr(main_window, "full_verify_cb")
    assert main_window.full_verify_cb.isChecked() is False


def test_checkbox_has_bilingual_label_and_tooltip(main_window):
    """Label and tooltip must cover both DE and EN."""
    cb = main_window.full_verify_cb
    text = cb.text()
    assert "Vollst\u00e4ndige Verifikation" in text
    assert "Full verification" in text

    tooltip = cb.toolTip()
    assert "jeden Sektor" in tooltip
    assert "every sector" in tooltip


def test_verify_progress_bar_hidden_until_verify_phase(main_window):
    """The verify progress bar must start hidden."""
    assert not main_window.verify_progress_bar.isVisible()


def test_set_phase_updates_badge_text(main_window):
    """_set_phase should mutate the phase badge label text."""
    main_window._set_phase("wiping")
    assert "Wiping" in main_window.phase_label.text()
    main_window._set_phase("verifying")
    assert "Verifying" in main_window.phase_label.text()
    main_window._set_phase("done")
    assert "Complete" in main_window.phase_label.text()


def test_time_estimate_doubles_with_full_verify(main_window, demo_device):
    """Ticking the full-verify box should lengthen the ETA string."""
    from PySide6.QtWidgets import QCheckBox

    # Inject a device and a matching checked checkbox so _selected_devices()
    # returns it.
    main_window.devices = [demo_device]
    main_window._device_checkboxes = []
    cb = QCheckBox()
    cb.setChecked(True)
    main_window._device_checkboxes.append(cb)

    main_window.full_verify_cb.setChecked(False)
    main_window._update_time_estimate()
    text_sample = main_window.estimate_label.text()

    main_window.full_verify_cb.setChecked(True)
    main_window._update_time_estimate()
    text_full = main_window.estimate_label.text()

    assert "full verify" in text_full
    assert "full verify" not in text_sample


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


def test_worker_falls_back_to_sample_on_bad_verify_mode(app_config):
    """An invalid verify_mode string must fall back to 'sample'."""
    from gui.wipe_worker import WipeWorker
    from wipe.methods import ZeroFill

    worker = WipeWorker(
        devices=[],
        wipe_method=ZeroFill(),
        config=app_config,
        verify_mode="bogus",
    )
    assert worker.verify_mode == "sample"


def test_checkbox_changes_verify_mode(qtbot, main_window, demo_device, monkeypatch):
    """When the checkbox is checked, _start_wipe must build a worker with
    verify_mode="full". Unchecked should yield "sample"."""
    from gui import main_window as mw_module

    captured: dict = {}

    class FakeWorker:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            # Fake signals that accept .connect without doing anything.
            for name in (
                "progress_updated",
                "verify_progress",
                "phase_changed",
                "device_completed",
                "all_completed",
                "error",
                "status_message",
                "stall_detected",
            ):
                setattr(self, name, MagicMock())

        def start(self):
            pass

        def isRunning(self):  # noqa: N802 — Qt API name
            return False

        def cancel(self):
            pass

        def wait(self, timeout=0):
            return True

    monkeypatch.setattr(mw_module, "WipeWorker", FakeWorker)

    # Case 1: unchecked -> sample
    main_window.full_verify_cb.setChecked(False)
    main_window._start_wipe([demo_device])
    assert captured["verify_mode"] == "sample"

    captured.clear()

    # Case 2: checked -> full
    main_window.full_verify_cb.setChecked(True)
    main_window._start_wipe([demo_device])
    assert captured["verify_mode"] == "full"


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


# -- v1.1: progress regression tests -----------------------------------------


def test_progress_bar_updates_when_on_progress_fires(qtbot, main_window):
    """Regression: a direct call to _on_progress with valid byte counts must
    move the device progress bar off zero and replace 'Calculating...' with
    a real ETA. Catches a v1.1 regression where progress stayed stuck at 0%."""
    # Half-way through a single-pass wipe of a 100 MB chunk.
    main_window._on_progress(0, 1, 1, 50_000_000, 100_000_000, 100.0)
    assert main_window.device_progress.value() == 50
    assert "calculating" not in main_window.eta_label.text().lower()


def test_progress_bar_handles_signal_int_overflow(qtbot, main_window):
    """PySide6 Signal(int, ...) marshals through C int (signed 32-bit), so
    drives larger than 2 GiB arrive here as negative two's-complement values.
    _on_progress must re-interpret negatives as their unsigned 32-bit form
    so the percentage math stays correct on real USB sticks."""
    total_bytes_real = 3 * 1024 * 1024 * 1024  # 3 GiB
    bytes_written_real = total_bytes_real // 2

    # Replicate PySide6's int-overflow wrap so the test reflects what the
    # slot actually receives when the signal carries a >2 GiB value.
    def wrap_int32(v: int) -> int:
        v &= 0xFFFFFFFF
        return v - (1 << 32) if v & 0x80000000 else v

    main_window._on_progress(
        0, 1, 1,
        wrap_int32(bytes_written_real),
        wrap_int32(total_bytes_real),
        100.0,
    )
    assert main_window.device_progress.value() == 50
    assert "calculating" not in main_window.eta_label.text().lower()


# -- v1.1: reformat UI tests --------------------------------------------------


def test_reformat_checkbox_default_unchecked(main_window):
    """The Reformat checkbox must exist and start off."""
    assert hasattr(main_window, "reformat_cb")
    assert main_window.reformat_cb.isChecked() is False


def test_reformat_controls_disabled_by_default(main_window):
    """Filesystem combo and label edit must be disabled when checkbox is off."""
    assert main_window.reformat_fs_combo.isEnabled() is False
    assert main_window.reformat_label_edit.isEnabled() is False


def test_reformat_controls_enable_when_checkbox_toggled(qtbot, main_window):
    """Toggling the reformat checkbox must enable/disable the dependent
    filesystem combo and label edit together."""
    main_window.reformat_cb.setChecked(True)
    assert main_window.reformat_fs_combo.isEnabled() is True
    assert main_window.reformat_label_edit.isEnabled() is True

    main_window.reformat_cb.setChecked(False)
    assert main_window.reformat_fs_combo.isEnabled() is False
    assert main_window.reformat_label_edit.isEnabled() is False


def test_reformat_filesystem_options_and_default(main_window):
    """The filesystem dropdown must offer exFAT, FAT32, NTFS with userData
    keys 'exfat'/'fat32'/'ntfs', defaulting to exFAT."""
    combo = main_window.reformat_fs_combo
    assert combo.count() == 3
    keys = [combo.itemData(i) for i in range(combo.count())]
    assert keys == ["exfat", "fat32", "ntfs"]
    # Default selection is exFAT.
    assert combo.currentData() == "exfat"


def test_reformat_label_default_and_max_length(main_window):
    """Volume label defaults to 'USB' and is capped at 32 characters."""
    edit = main_window.reformat_label_edit
    assert edit.text() == "USB"
    assert edit.placeholderText() == "Volume label"
    assert edit.maxLength() == 32


def test_reformat_checkbox_has_bilingual_label_and_tooltip(main_window):
    """Reformat checkbox label and tooltip must cover both DE and EN."""
    cb = main_window.reformat_cb
    text = cb.text()
    assert "Nach L\u00f6schung formatieren" in text
    assert "Reformat after wipe" in text

    tooltip = cb.toolTip()
    assert "fresh partition" in tooltip
    assert "Windows Explorer" in tooltip
