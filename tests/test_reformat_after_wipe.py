"""Reformat after a wipe (2.0 polish), GUI and CLI.

PowerShell's Clear-Disk needs the disk free: the physical drive handle is
closed and every volume lock released before the reformat starts. A
reformat that is skipped, fails or even raises never changes the wipe
result — the certificate is still issued, just without a new file system.
All drive access is faked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import cli
from tests.test_cli import _fake_device, _fake_wipe_result

pytest.importorskip("PySide6")


def _ok_format(**_kw):
    return SimpleNamespace(success=True, filesystem="exFAT", label="USB",
                           partition_style="MBR", duration_seconds=1.0,
                           error_message=None)


def _no_hidden():
    return SimpleNamespace(supported=False, hpa_present=False, dco_present=False,
                           hpa_hidden_sectors=0, dco_hidden_sectors=0)


# ── CLI ────────────────────────────────────────────────────────────────

def _run_cli_wipe(events: list, reformat, gate=None):
    args = cli.build_parser().parse_args([
        "wipe", "--device", "E:", "--method", "zero", "--operator", "Test",
        "--verify", "none", "--reformat", "exfat", "--yes",
    ])
    method = MagicMock()
    method.name = "ZeroFill"
    method.passes = 1
    method.sicherheitsstufe = "1-2"
    method.execute.return_value = _fake_wipe_result()
    gate = gate or MagicMock(side_effect=[[1, 2], [5]])
    cert = MagicMock(return_value="C:/tmp/fake.pdf")
    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "_resolve_wipe_method", return_value=method),
        patch.object(cli, "prepare_target", gate),
        patch.object(cli, "release_volumes",
                     side_effect=lambda hs: events.append(("release", list(hs)))),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive", side_effect=lambda h: events.append(("close", h))),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "detect_hidden_areas", return_value=_no_hidden()),
        patch.object(cli, "get_next_cert_number", return_value=1),
        patch.object(cli, "generate_certificate", cert),
        patch.object(cli, "log_wipe_to_csv"),
        patch.object(cli, "audit_log"),
        patch("wipe.format.reformat_drive", side_effect=reformat),
    ):
        cli.cmd_wipe(args)
    return cert


def test_cli_reformat_starts_after_the_drive_is_closed_and_unlocked():
    events: list = []

    def reformat(**kw):
        events.append(("reformat", kw["disk_number"]))
        return _ok_format()

    cert = _run_cli_wipe(events, reformat)
    at = events.index(("reformat", 99))
    assert ("close", 12345) in events[:at]
    assert ("release", [1, 2]) in events[:at]
    assert ("release", [5]) in events[:at]
    assert cert.call_args.args[0].reformat_performed is True


def test_cli_reformat_that_raises_keeps_the_wipe_and_the_certificate():
    def reformat(**_kw):
        raise RuntimeError("PowerShell vanished")

    cert = _run_cli_wipe([], reformat)
    cert.assert_called_once()
    data = cert.call_args.args[0]
    assert data.wipe_success is True
    assert data.reformat_performed is False


def test_cli_reformat_gate_error_only_skips_the_reformat():
    reformat = MagicMock(side_effect=AssertionError("must not run"))
    gate = MagicMock(side_effect=[[1], ValueError("odd WMI answer")])
    cert = _run_cli_wipe([], reformat, gate)
    reformat.assert_not_called()
    assert cert.call_args.args[0].reformat_performed is False


# ── GUI worker ─────────────────────────────────────────────────────────

@pytest.fixture()
def app_config(tmp_path):
    from core.config import AppConfig

    cfg = AppConfig()
    cfg.cert_output_dir = str(tmp_path / "certs")
    return cfg


def _run_worker(qtbot, app_config, events: list, reformat, gate=None, **worker_kw):
    from gui.wipe_worker import WipeWorker
    from tests.test_gui_verify import (
        _gui_lock_test_device, _gui_lock_test_method, _ok_wipe_result,
    )

    gate = gate or MagicMock(side_effect=[[7], [9]])
    cert = MagicMock(return_value="c.pdf")
    results: list[tuple[int, bool]] = []
    with patch("gui.wipe_worker.prepare_target", gate), \
         patch("gui.wipe_worker.release_volumes",
               side_effect=lambda hs: events.append(("release", list(hs)))), \
         patch("gui.wipe_worker.open_physical_drive", return_value=1), \
         patch("gui.wipe_worker.close_drive",
               side_effect=lambda h: events.append(("close", h))), \
         patch("gui.wipe_worker.get_drive_size", return_value=1024**2), \
         patch("gui.wipe_worker.detect_hidden_areas", return_value=_no_hidden()), \
         patch("gui.wipe_worker.generate_certificate", cert), \
         patch("gui.wipe_worker.log_wipe_to_csv"), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("gui.wipe_worker.audit_log"), \
         patch("wipe.format.reformat_drive", side_effect=reformat):
        worker = WipeWorker(devices=[_gui_lock_test_device()],
                            wipe_method=_gui_lock_test_method(
                                lambda *a, **k: _ok_wipe_result()),
                            config=app_config, verify_mode="none", reformat="exfat",
                            **worker_kw)
        worker.device_completed.connect(lambda i, ok, _p: results.append((i, ok)))
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()
    return cert, results


def test_worker_closes_the_drive_before_reformatting(qtbot, app_config):
    events: list = []

    def reformat(**kw):
        events.append(("reformat", kw["disk_number"]))
        return _ok_format()

    cert, results = _run_worker(qtbot, app_config, events, reformat)
    at = events.index(("reformat", 7))
    assert ("close", 1) in events[:at]
    assert ("release", [7]) in events[:at]
    assert ("release", [9]) in events[:at]
    # Closed exactly once (not again in the clean-up).
    assert events.count(("close", 1)) == 1
    assert results == [(0, True)]
    assert cert.call_args.args[0].reformat_performed is True


def test_worker_reformat_that_raises_keeps_the_wipe_and_the_certificate(qtbot, app_config):
    def reformat(**_kw):
        raise RuntimeError("PowerShell vanished")

    cert, results = _run_worker(qtbot, app_config, [], reformat)
    assert results == [(0, True)]
    cert.assert_called_once()
    assert cert.call_args.args[0].reformat_performed is False


def test_worker_reformat_gate_error_only_skips_the_reformat(qtbot, app_config):
    reformat = MagicMock(side_effect=AssertionError("must not run"))
    gate = MagicMock(side_effect=[[7], ValueError("odd WMI answer")])
    cert, results = _run_worker(qtbot, app_config, [], reformat, gate)
    reformat.assert_not_called()
    assert results == [(0, True)]
    assert cert.call_args.args[0].reformat_performed is False


def test_worker_hands_the_media_type_to_the_certificate(qtbot, app_config):
    """Protection class 3 on an SSD: the certificate needs the media type."""
    from tests.test_gui_verify import _gui_lock_test_device

    cert, results = _run_worker(
        qtbot, app_config, [], lambda **kw: _ok_format(), schutzklasse=3,
        media_types={_gui_lock_test_device().device_id: "ssd"})
    data = cert.call_args.args[0]
    assert (data.media_type, data.schutzklasse) == ("ssd", 3)
    assert results == [(0, True)]
