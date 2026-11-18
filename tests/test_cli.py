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


def test_verify_flag_defaults_to_full():
    """When --verify is omitted, default is 'full' (spec G6)."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS)
    assert args.verify == "full"


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
        bad_sector_count=0,
        bad_sector_bytes=0,
        bad_sector_offsets=[],
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
        patch.object(cli, "prepare_target"),
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
# v1.2: HPA/DCO detection + experimental ATA Secure Erase
# ─────────────────────────────────────────────────────────────────────

_SECURE_ERASE_ARGS = [
    "wipe", "--device", "E:", "--method", "secure-erase",
    "--operator", "Test Operator",
]


def _no_hidden_areas():
    return SimpleNamespace(
        supported=False, hpa_present=False, dco_present=False,
        hpa_hidden_sectors=0, dco_hidden_sectors=0,
    )


def test_secure_erase_requires_experimental_flag():
    """--method secure-erase without --experimental must abort (SystemExit)."""
    parser = cli.build_parser()
    args = parser.parse_args(_SECURE_ERASE_ARGS + ["--yes"])
    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "audit_log"),
    ):
        with pytest.raises(SystemExit):
            cli.cmd_wipe(args)


def test_secure_erase_runs_and_marks_certificate():
    """With --experimental, cmd_wipe calls secure_erase() and flags the cert."""
    parser = cli.build_parser()
    args = parser.parse_args(_SECURE_ERASE_ARGS + ["--experimental", "--yes"])

    captured = {}

    def _capture_cert(cert_data, path, **_signing):
        captured["cert"] = cert_data
        return "C:/tmp/fake.pdf"

    se_result = SimpleNamespace(
        success=True, method="ata-secure-erase", supported=True,
        frozen=False, duration_seconds=1.0, error=None,
    )

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "prepare_target") as mock_gate,
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "lock_volume"),
        patch.object(cli, "unlock_volume"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "detect_hidden_areas", return_value=_no_hidden_areas()),
        patch.object(cli, "secure_erase", return_value=se_result) as mock_se,
        patch.object(cli, "get_next_cert_number", return_value=1),
        patch.object(cli, "generate_certificate", side_effect=_capture_cert),
        patch.object(cli, "log_wipe_to_csv"),
        patch.object(cli, "audit_log"),
    ):
        cli.cmd_wipe(args)

    mock_gate.assert_called_once()
    mock_se.assert_called_once()
    cert = captured["cert"]
    assert cert.secure_erase_used is True
    assert cert.secure_erase_method == "ata-secure-erase"
    assert cert.wipe_method.startswith("ATA Secure Erase")


def test_hpa_detection_flows_to_certificate():
    """A detected HPA must land on the certificate (hidden_area fields)."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--verify", "none", "--yes"])

    captured = {}

    def _capture_cert(cert_data, path, **_signing):
        captured["cert"] = cert_data
        return "C:/tmp/fake.pdf"

    fake_method = MagicMock()
    fake_method.name = "ZeroFill"
    fake_method.passes = 1
    fake_method.sicherheitsstufe = "1-2"
    fake_method.execute.return_value = _fake_wipe_result()

    hidden = SimpleNamespace(
        supported=True, hpa_present=True, hpa_hidden_sectors=2048 * 1024,
        dco_present=False, dco_hidden_sectors=0,
    )

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "_resolve_wipe_method", return_value=fake_method),
        patch.object(cli, "prepare_target"),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "lock_volume"),
        patch.object(cli, "unlock_volume"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "detect_hidden_areas", return_value=hidden),
        patch.object(cli, "get_next_cert_number", return_value=1),
        patch.object(cli, "generate_certificate", side_effect=_capture_cert),
        patch.object(cli, "log_wipe_to_csv"),
        patch.object(cli, "audit_log"),
    ):
        # A hidden area that was not removed limits the result: exit code 3.
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_wipe(args)
    assert exc_info.value.code == 3

    cert = captured["cert"]
    assert cert.hidden_area_probed is True
    assert cert.hpa_present is True
    assert cert.hpa_hidden_bytes == 2048 * 1024 * 512


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
        bad_sector_count=0,
        bad_sector_bytes=0,
        bad_sector_offsets=[],
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

    def _capture_cert(cert_data, cert_path, **_signing):
        captured_cert["data"] = cert_data
        return cert_path

    def _capture_csv(row):
        captured_csv["row"] = row

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "_resolve_wipe_method", return_value=fake_method),
        patch.object(cli, "prepare_target"),
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
            patch.object(cli, "prepare_target"),
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


# ─────────────────────────────────────────────────────────────────────
# 1.1.1: the safety gate runs before the disk is opened
# ─────────────────────────────────────────────────────────────────────

def test_cmd_wipe_aborts_before_opening_disk_when_target_unsafe():
    """If prepare_target refuses, the CLI exits and never opens the disk."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--yes"])
    opener = MagicMock(return_value=12345)

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(
            cli, "prepare_target",
            side_effect=cli.UnsafeTargetError("is no longer the selected device"),
        ),
        patch.object(cli, "open_physical_drive", opener),
        patch.object(cli, "close_drive"),
        patch.object(cli, "audit_log"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_wipe(args)

    assert exc_info.value.code != 0
    opener.assert_not_called()


def test_secure_erase_calls_prepare_target_first():
    """--method secure-erase passes the same gate: when prepare_target
    refuses, the disk is never opened and no ATA command is sent."""
    parser = cli.build_parser()
    args = parser.parse_args(_SECURE_ERASE_ARGS + ["--experimental", "--yes"])
    opener = MagicMock(return_value=12345)

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(
            cli, "prepare_target",
            side_effect=cli.UnsafeTargetError("is the system disk"),
        ),
        patch.object(cli, "open_physical_drive", opener),
        patch.object(cli, "close_drive"),
        patch.object(cli, "secure_erase") as mock_se,
        patch.object(cli, "audit_log"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_wipe(args)

    assert exc_info.value.code != 0
    opener.assert_not_called()
    mock_se.assert_not_called()


def test_reformat_rechecks_target_and_is_skipped_when_refused():
    """Reformat writes a new partition table, so prepare_target runs again
    right before it. A refusal skips the reformat; the wipe's certificate
    is still issued."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--reformat", "exfat", "--yes"])

    fake_method = MagicMock()
    fake_method.name = "ZeroFill"
    fake_method.passes = 1
    fake_method.sicherheitsstufe = "1-2"
    fake_method.execute.return_value = _fake_wipe_result()
    fake_reformat = MagicMock(return_value=_fake_format_result())

    import sys as _sys
    import types as _types
    fake_format_mod = _types.ModuleType("wipe.format")
    fake_format_mod.reformat_drive = fake_reformat
    _sys.modules["wipe.format"] = fake_format_mod

    gate = MagicMock(side_effect=[[], cli.UnsafeTargetError("stick swapped")])
    try:
        with (
            patch.object(cli, "is_admin", return_value=True),
            patch.object(cli, "list_devices", return_value=[_fake_device()]),
            patch.object(cli, "_resolve_wipe_method", return_value=fake_method),
            patch.object(cli, "prepare_target", gate),
            patch.object(cli, "open_physical_drive", return_value=12345),
            patch.object(cli, "close_drive"),
            patch.object(cli, "lock_volume"),
            patch.object(cli, "unlock_volume"),
            patch.object(cli, "get_drive_size", return_value=1024 * 1024),
            patch.object(cli, "detect_hidden_areas", return_value=_no_hidden_areas()),
            patch.object(cli, "get_next_cert_number", return_value=1),
            patch.object(cli, "generate_certificate",
                         return_value="C:/tmp/fake.pdf") as mock_cert,
            patch.object(cli, "log_wipe_to_csv"),
            patch.object(cli, "audit_log"),
        ):
            cli.cmd_wipe(args)
    finally:
        _sys.modules.pop("wipe.format", None)

    assert gate.call_count == 2
    fake_reformat.assert_not_called()
    mock_cert.assert_called_once()


def test_cmd_wipe_confirmation_names_every_letter_of_the_disk(capsys, monkeypatch):
    """1.1.1: 'wipe --device E:' on a disk with E: and G: must warn about both."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS)
    dev = _fake_device()
    dev.disk_letters = ["E:", "G:"]
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[dev]),
        patch.object(cli, "audit_log"),
    ):
        with pytest.raises(SystemExit):
            cli.cmd_wipe(args)

    assert "E:, G:" in capsys.readouterr().out


def test_cmd_wipe_holds_volume_locks_until_the_wipe_is_done():
    """1.1.2: CLI keeps the locks from prepare_target during execute()."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--yes"])
    released: list[int] = []
    held: list[bool] = []

    fake_method = MagicMock()
    fake_method.name = "ZeroFill"
    fake_method.passes = 1
    fake_method.sicherheitsstufe = "1-2"

    def fake_execute(**_k):
        held.append(released == [])
        return _fake_wipe_result()

    fake_method.execute.side_effect = fake_execute
    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "_resolve_wipe_method", return_value=fake_method),
        patch.object(cli, "prepare_target", return_value=[5]),
        patch.object(cli, "release_volumes", side_effect=released.extend),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "unlock_volume"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "detect_hidden_areas", return_value=_no_hidden_areas()),
        patch.object(cli, "get_next_cert_number", return_value=1),
        patch.object(cli, "generate_certificate", return_value="C:/tmp/fake.pdf"),
        patch.object(cli, "log_wipe_to_csv"),
        patch.object(cli, "audit_log"),
    ):
        cli.cmd_wipe(args)

    assert held == [True]
    assert released == [5]


def test_secure_erase_holds_volume_locks_during_the_erase():
    """1.1.2 on v2: the drive's own erase also runs with the volumes locked,
    and the locks are released exactly once afterwards."""
    parser = cli.build_parser()
    args = parser.parse_args(_SECURE_ERASE_ARGS + ["--experimental", "--yes"])
    released: list[int] = []
    held: list[bool] = []
    se_result = SimpleNamespace(
        success=True, method="ata-secure-erase", supported=True,
        frozen=False, duration_seconds=1.0, error=None,
    )

    def fake_secure_erase(*_a, **_k):
        held.append(released == [])
        return se_result

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "prepare_target", return_value=[5, 6]),
        patch.object(cli, "release_volumes", side_effect=released.extend),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "unlock_volume"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "detect_hidden_areas", return_value=_no_hidden_areas()),
        patch.object(cli, "secure_erase", side_effect=fake_secure_erase),
        patch.object(cli, "get_next_cert_number", return_value=1),
        patch.object(cli, "generate_certificate", return_value="C:/tmp/fake.pdf"),
        patch.object(cli, "log_wipe_to_csv"),
        patch.object(cli, "audit_log"),
    ):
        cli.cmd_wipe(args)

    assert held == [True]
    assert released == [5, 6]


def test_secure_erase_releases_volume_locks_when_the_erase_raises():
    """An unexpected error during the erase still releases the locks."""
    parser = cli.build_parser()
    args = parser.parse_args(_SECURE_ERASE_ARGS + ["--experimental", "--yes"])
    released: list[int] = []

    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "prepare_target", return_value=[5]),
        patch.object(cli, "release_volumes", side_effect=released.extend),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "detect_hidden_areas", return_value=_no_hidden_areas()),
        patch.object(cli, "secure_erase", side_effect=OSError("bridge gone")),
        patch.object(cli, "audit_log"),
    ):
        with pytest.raises(SystemExit):
            cli.cmd_wipe(args)

    assert released == [5]


def test_reformat_releases_the_gate_locks_before_reformatting():
    """The safety re-check before reformat dismounts and locks the volumes
    again. Clear-Disk needs the disk free, so those locks are released
    before the reformat runs; the identity/system check itself stays."""
    parser = cli.build_parser()
    args = parser.parse_args(_REQUIRED_WIPE_ARGS + ["--reformat", "exfat", "--yes"])
    released: list[int] = []
    seen_at_reformat: list[list[int]] = []

    fake_method = MagicMock()
    fake_method.name = "ZeroFill"
    fake_method.passes = 1
    fake_method.sicherheitsstufe = "1-2"
    fake_method.execute.return_value = _fake_wipe_result()

    def fake_reformat(**_kw):
        seen_at_reformat.append(list(released))
        return _fake_format_result()

    import sys as _sys
    import types as _types
    fake_format_mod = _types.ModuleType("wipe.format")
    fake_format_mod.reformat_drive = MagicMock(side_effect=fake_reformat)
    _sys.modules["wipe.format"] = fake_format_mod

    gate = MagicMock(side_effect=[[5], [6]])
    try:
        with (
            patch.object(cli, "is_admin", return_value=True),
            patch.object(cli, "list_devices", return_value=[_fake_device()]),
            patch.object(cli, "_resolve_wipe_method", return_value=fake_method),
            patch.object(cli, "prepare_target", gate),
            patch.object(cli, "release_volumes", side_effect=released.extend),
            patch.object(cli, "open_physical_drive", return_value=12345),
            patch.object(cli, "close_drive"),
            patch.object(cli, "unlock_volume"),
            patch.object(cli, "get_drive_size", return_value=1024 * 1024),
            patch.object(cli, "detect_hidden_areas", return_value=_no_hidden_areas()),
            patch.object(cli, "get_next_cert_number", return_value=1),
            patch.object(cli, "generate_certificate", return_value="C:/tmp/fake.pdf"),
            patch.object(cli, "log_wipe_to_csv"),
            patch.object(cli, "audit_log"),
        ):
            cli.cmd_wipe(args)
    finally:
        _sys.modules.pop("wipe.format", None)

    assert gate.call_count == 2
    assert seen_at_reformat == [[5, 6]]
    assert released == [5, 6]
