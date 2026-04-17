"""Tests for wipe.format.reformat_drive.

All tests mock subprocess.run so no real PowerShell is invoked. We also force
sys.platform == "win32" by default (via fixture) so the platform guard does
not short-circuit on non-Windows CI runners.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from wipe import format as fmt
from wipe.format import FormatResult, reformat_drive


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _force_win32(monkeypatch):
    """Default every test to behave as if running on Windows.

    Individual tests that need a different platform (e.g. the non-Windows
    test) override this by monkeypatching `sys.platform` themselves.
    """
    monkeypatch.setattr(fmt.sys, "platform", "win32")
    # Ensure the PowerShell-availability check passes by default. Tests that
    # care about the negative path can override this.
    monkeypatch.setattr(fmt, "_powershell_available", lambda: True)


def _ok_proc(stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a successful CompletedProcess-like mock."""
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = 0
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _fail_proc(stderr: str, stdout: str = "", returncode: int = 1) -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ─────────────────────────────────────────────────────────────────────
# Happy paths
# ─────────────────────────────────────────────────────────────────────


def test_reformat_success_exfat():
    with patch.object(fmt.subprocess, "run", return_value=_ok_proc()) as run:
        result = reformat_drive(disk_number=1, filesystem="exfat", label="USB")

    assert isinstance(result, FormatResult)
    assert result.success is True
    assert result.method == "powershell"
    assert result.filesystem == "exFAT"
    assert result.label == "USB"
    assert result.partition_style == "MBR"
    assert result.error_message is None
    assert result.duration_seconds >= 0

    # Sanity-check that the command we sent contains the expected cmdlets.
    args, _kwargs = run.call_args
    cmd = args[0]
    assert cmd[0] == "powershell"
    script = cmd[-1]
    assert "Clear-Disk -Number 1" in script
    assert "Initialize-Disk -Number 1 -PartitionStyle MBR" in script
    assert "New-Partition -DiskNumber 1 -UseMaximumSize -AssignDriveLetter" in script
    assert "Format-Volume" in script
    assert "exFAT" in script
    assert '"USB"' in script


def test_reformat_success_fat32():
    with patch.object(fmt.subprocess, "run", return_value=_ok_proc()):
        result = reformat_drive(disk_number=2, filesystem="fat32", label="DATA")

    assert result.success is True
    assert result.filesystem == "FAT32"
    assert result.label == "DATA"


# ─────────────────────────────────────────────────────────────────────
# Label handling
# ─────────────────────────────────────────────────────────────────────


def test_reformat_fat32_label_truncation():
    with patch.object(fmt.subprocess, "run", return_value=_ok_proc()):
        result = reformat_drive(
            disk_number=1, filesystem="fat32", label="VeryLongLabelName"
        )

    assert result.success is True
    assert result.label == "VeryLongLab"  # 11 chars
    assert len(result.label) == 11


def test_reformat_sanitizes_label():
    with patch.object(fmt.subprocess, "run", return_value=_ok_proc()) as run:
        result = reformat_drive(
            disk_number=1, filesystem="exfat", label="My`USB;"
        )

    assert result.success is True
    assert result.label == "MyUSB"
    # And the dangerous characters never made it into the PowerShell script's
    # label argument. The cmdlet separator ';' is fine elsewhere in the
    # script, but the label literal we quoted must be the sanitised form.
    script = run.call_args[0][0][-1]
    assert '"MyUSB"' in script
    assert "`" not in script


# ─────────────────────────────────────────────────────────────────────
# Failure paths
# ─────────────────────────────────────────────────────────────────────


def test_reformat_failure_captures_stderr():
    with patch.object(
        fmt.subprocess,
        "run",
        return_value=_fail_proc(stderr="Access denied"),
    ):
        result = reformat_drive(disk_number=1, filesystem="exfat")

    assert result.success is False
    assert result.method == "powershell"
    assert result.error_message is not None
    assert "Access denied" in result.error_message


def test_reformat_invalid_filesystem():
    # subprocess.run must NOT be called when validation fails up front.
    with patch.object(fmt.subprocess, "run") as run:
        result = reformat_drive(disk_number=1, filesystem="xfs")

    assert result.success is False
    assert result.error_message is not None
    # Error mentions the supported options.
    msg = result.error_message
    assert "FAT32" in msg
    assert "exFAT" in msg
    assert "NTFS" in msg
    run.assert_not_called()


def test_reformat_non_windows(monkeypatch):
    monkeypatch.setattr(fmt.sys, "platform", "linux")
    with patch.object(fmt.subprocess, "run") as run:
        result = reformat_drive(disk_number=1, filesystem="exfat")

    assert result.success is False
    assert result.method == "none"
    assert result.error_message == "Reformat requires Windows"
    run.assert_not_called()


def test_reformat_timeout():
    timeout_exc = subprocess.TimeoutExpired(cmd="powershell", timeout=300)
    with patch.object(fmt.subprocess, "run", side_effect=timeout_exc):
        result = reformat_drive(disk_number=1, filesystem="exfat")

    assert result.success is False
    assert result.method == "powershell"
    assert result.error_message is not None
    assert "timed out" in result.error_message.lower()


# ─────────────────────────────────────────────────────────────────────
# Progress callback
# ─────────────────────────────────────────────────────────────────────


def test_reformat_progress_callback():
    messages: list[str] = []

    def cb(msg: str) -> None:
        messages.append(msg)

    with patch.object(fmt.subprocess, "run", return_value=_ok_proc()):
        result = reformat_drive(
            disk_number=1,
            filesystem="exfat",
            progress_callback=cb,
        )

    assert result.success is True
    # Each step fires its own message — exact wording matches the spec so
    # downstream UI can match-and-localise if it wants to.
    assert "Clearing disk..." in messages
    assert "Initializing..." in messages
    assert "Creating partition..." in messages
    assert "Formatting..." in messages
    assert "Done." in messages
    # And the order is the order the steps execute in.
    order = [
        "Clearing disk...",
        "Initializing...",
        "Creating partition...",
        "Formatting...",
        "Done.",
    ]
    indices = [messages.index(m) for m in order]
    assert indices == sorted(indices)
