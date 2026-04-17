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
        patch.object(cli, "is_admin", return_value=True),
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


# ─────────────────────────────────────────────────────────────────────
# Admin privilege check for cmd_wipe
# ─────────────────────────────────────────────────────────────────────

def test_cmd_wipe_requires_admin_when_not_elevated():
    """cmd_wipe must exit with SystemExit when is_admin() returns False."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--yes"])

    with patch.object(cli, "is_admin", return_value=False):
        with pytest.raises(SystemExit):
            cli.cmd_wipe(args)


# ─────────────────────────────────────────────────────────────────────
# Certificate passes reflect wipe_result.passes (actual), not method.passes
# ─────────────────────────────────────────────────────────────────────

def _fake_wipe_result_vsitr_with_blank():
    """A BSI-VSITR wipe result with an appended zero-blank pass (8 total)."""
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
        sample_hash="b" * 64,
        timestamp=now,
    )
    return SimpleNamespace(
        method_name="BSI-VSITR",
        passes=8,  # 7 VSITR passes + 1 appended zero-blank
        start_time=now,
        end_time=now,
        bytes_written=1024 * 1024,
        success=True,
        error_message=None,
        verify_result=verify,
        zero_blank_appended=True,
    )


def test_cert_uses_wipe_result_passes_not_method_passes():
    """CertificateData and CSV must use wipe_result.passes, not wipe_method.passes.

    For BSI-VSITR with an appended zero-blank pass, the method advertises 7
    passes but the actual run performs 8. The DIN 66399 certificate and CSV
    log must reflect the real count (8), not the declared method count (7).
    """
    parser = cli.build_parser()
    args = parser.parse_args(
        ["wipe", "--device", "E:", "--method", "bsi", "--operator", "Test",
         "--verify", "full", "--yes"]
    )

    fake_method = MagicMock()
    fake_method.name = "BSI-VSITR"
    fake_method.passes = 7  # the method's declared pass count
    fake_method.sicherheitsstufe = "3"
    fake_method.execute.return_value = _fake_wipe_result_vsitr_with_blank()

    captured_cert: dict = {}
    captured_csv: dict = {}

    def _capture_cert(cert_data, cert_path):
        captured_cert["data"] = cert_data
        return cert_path

    def _capture_csv(row):
        captured_csv["row"] = row

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "_resolve_wipe_method", return_value=fake_method),
        patch.object(cli, "dismount_volume"),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "lock_volume"),
        patch.object(cli, "unlock_volume"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "get_next_cert_number", return_value=42),
        patch.object(cli, "generate_certificate", side_effect=_capture_cert),
        patch.object(cli, "log_wipe_to_csv", side_effect=_capture_csv),
        patch.object(cli, "audit_log"),
    ):
        cli.cmd_wipe(args)

    # CertificateData should report the actual total (8), not method.passes (7)
    assert "data" in captured_cert, "generate_certificate was not called"
    assert captured_cert["data"].passes == 8, (
        f"Cert passes should be 8 (VSITR+blank), got {captured_cert['data'].passes}"
    )

    # CSV log should also use the actual total
    assert "row" in captured_csv, "log_wipe_to_csv was not called"
    assert captured_csv["row"]["passes"] == "8", (
        f"CSV passes should be '8', got {captured_csv['row']['passes']!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# --reformat flag parsing
# ─────────────────────────────────────────────────────────────────────

def test_reformat_flag_default_none():
    """When --reformat is omitted, args.reformat == 'none'."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS)
    assert args.reformat == "none"


def test_reformat_flag_accepts_exfat():
    """--reformat exfat is accepted and stored."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--reformat", "exfat"])
    assert args.reformat == "exfat"


def test_reformat_flag_rejects_invalid():
    """--reformat xfs is rejected by argparse with SystemExit."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(_REQUIRED_WIPE_ARGS + ["--reformat", "xfs"])


def test_reformat_label_default():
    """When --reformat-label is omitted, args.reformat_label == 'USB'."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS)
    assert args.reformat_label == "USB"


# ─────────────────────────────────────────────────────────────────────
# cmd_wipe invokes reformat_drive when --reformat is requested
# ─────────────────────────────────────────────────────────────────────

def _fake_format_result(success: bool = True, filesystem: str = "exfat",
                        label: str = "USB"):
    """Build a minimal FormatResult-like object."""
    return SimpleNamespace(
        success=success,
        method="diskpart" if success else "",
        filesystem=filesystem,
        label=label,
        partition_style="MBR",
        duration_seconds=1.5,
        error_message=None if success else "format failed",
    )


def test_cmd_wipe_calls_reformat_when_requested():
    """cmd_wipe must call reformat_drive with the chosen filesystem when --reformat is set."""
    parser = cli.build_parser()
    args = parser.parse_args(
        _REQUIRED_WIPE_ARGS + ["--reformat", "exfat", "--yes"]
    )

    fake_method = MagicMock()
    fake_method.name = "ZeroFill"
    fake_method.passes = 1
    fake_method.sicherheitsstufe = "1-2"
    fake_method.execute.return_value = _fake_wipe_result()

    fake_reformat = MagicMock(return_value=_fake_format_result(
        success=True, filesystem="exfat", label="USB"
    ))

    # Pre-register a stub wipe.format module so the inline import inside
    # cmd_wipe resolves to our mock without requiring agent A's module.
    import sys as _sys
    import types as _types
    fake_format_mod = _types.ModuleType("wipe.format")
    fake_format_mod.reformat_drive = fake_reformat
    _sys.modules["wipe.format"] = fake_format_mod

    try:
        with (
            patch.object(cli, "is_admin", return_value=True),
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
    finally:
        _sys.modules.pop("wipe.format", None)

    fake_reformat.assert_called_once()
    kwargs = fake_reformat.call_args.kwargs
    assert kwargs.get("filesystem") == "exfat"
    # device_id is r"\\.\PhysicalDrive99" → disk_number == 99
    assert kwargs.get("disk_number") == 99
    assert kwargs.get("label") == "USB"
    assert kwargs.get("partition_style") == "MBR"
