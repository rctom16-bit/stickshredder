"""Tests for cli.py — argument parsing and verify-mode integration."""

from __future__ import annotations

import ctypes
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Mock Windows-only ctypes surfaces before importing cli, which (via
# wipe.methods / wipe.verify) references ctypes.windll at import time.
_fake_kernel32 = MagicMock()
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()  # type: ignore[attr-defined]
ctypes.windll.kernel32 = _fake_kernel32

import cli  # noqa: E402  — must come after the ctypes shim


# ─────────────────────────────────────────────────────────────────────
# --verify flag parsing
# ─────────────────────────────────────────────────────────────────────

_REQUIRED_WIPE_ARGS = [
    "wipe",
    "--device", "E:",
    "--method", "zero",
    "--operator", "Test Operator",
]


def test_verify_flag_defaults_to_sample():
    """When --verify is omitted, default is 'sample'."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS)
    assert args.verify == "sample"


def test_verify_flag_accepts_full():
    """--verify full is accepted and stored."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--verify", "full"])
    assert args.verify == "full"


def test_verify_flag_accepts_none():
    """--verify none is accepted and stored."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--verify", "none"])
    assert args.verify == "none"


def test_verify_flag_rejects_invalid():
    """--verify bogus is rejected by argparse with SystemExit."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(_REQUIRED_WIPE_ARGS + ["--verify", "bogus"])


# ─────────────────────────────────────────────────────────────────────
# cmd_wipe plumbs verify_mode into WipeMethod.execute()
# ─────────────────────────────────────────────────────────────────────

def _fake_device():
    """Build a minimal DeviceInfo-like object safe for cmd_wipe to consume."""
    return SimpleNamespace(
        drive_letter="E:",
        device_id=r"\\.\PhysicalDrive99",
        model="Fake USB Stick",
        serial_number="FAKE-SERIAL-0001",
        capacity_bytes=1024 * 1024,
        filesystem="FAT32",
        connection_type="USB",
        is_removable=True,
        is_system_drive=False,
        is_internal=False,
        has_bitlocker=False,
        has_active_processes=False,
        partition_count=1,
        safe_to_wipe=True,
        friendly_name="Fake USB Stick (E:)",
    )


def _fake_wipe_result():
    """A successful WipeResult with a sample VerifyResult attached."""
    now = datetime.now()
    verify = SimpleNamespace(
        success=True,
        method="full",
        bytes_verified=1024 * 1024,
        expected_pattern="zeros",
        error_count=0,
        mismatch_offsets=[],
        duration_seconds=0.5,
        sectors_checked=2048,
        sectors_matched=2048,
        sample_hash="a" * 64,
        timestamp=now,
    )
    return SimpleNamespace(
        method_name="ZeroFill",
        passes=1,
        start_time=now,
        end_time=now,
        bytes_written=1024 * 1024,
        success=True,
        error_message=None,
        verify_result=verify,
        zero_blank_appended=False,
    )


def test_cmd_wipe_passes_verify_mode_to_execute():
    """cmd_wipe must forward args.verify to wipe_method.execute(verify_mode=...)."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--verify", "full", "--yes"])

    fake_method = MagicMock()
    fake_method.name = "ZeroFill"
    fake_method.passes = 1
    fake_method.sicherheitsstufe = "1-2"
    fake_method.execute.return_value = _fake_wipe_result()

    with (
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "_resolve_wipe_method", return_value=fake_method),
        patch.object(cli, "dismount_volume"),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "lock_volume"),
        patch.object(cli, "unlock_volume"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "get_next_cert_number", return_value=1),
        patch.object(cli, "generate_certificate", return_value="C:/tmp/fake.pdf"),
        patch.object(cli, "log_wipe_to_csv"),
        patch.object(cli, "audit_log"),
    ):
        cli.cmd_wipe(args)

    fake_method.execute.assert_called_once()
    kwargs = fake_method.execute.call_args.kwargs
    assert kwargs.get("verify_mode") == "full"
    # Full mode must also pass a verify_progress_callback so the CLI can render
    # a verification progress bar.
    assert kwargs.get("verify_progress_callback") is not None
