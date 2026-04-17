"""Tests for wipe.verify — sample_verify, full_verify and VerifyResult."""

import ctypes
import ctypes.wintypes
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# Ensure ctypes.windll exists on non-Windows platforms.
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()
ctypes.windll.kernel32 = MagicMock()

from wipe.verify import (
    sample_verify,
    full_verify,
    verify_wipe,
    VerifyResult,
    VerificationResult,
    SECTOR_SIZE,
)


BLOCK_SIZE = 1_048_576  # 1 MB default


# ─────────────────────────────────────────────────────────────────────
# sample_verify (formerly verify_wipe) tests
# ─────────────────────────────────────────────────────────────────────

@patch("wipe.verify._read_sector")
def test_sample_verify_all_zeros_passed(mock_read):
    mock_read.return_value = b"\x00" * SECTOR_SIZE
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 200,
        expected_pattern=b"\x00",
        sample_count=50,
    )
    assert result.success is True
    assert result.sectors_checked == 50
    assert result.sectors_matched == 50
    assert len(result.sample_hash) == 64  # SHA-256 hex


@patch("wipe.verify._read_sector")
def test_sample_verify_mismatch_fails(mock_read):
    # Disk has 0xFF but we expect 0x00
    mock_read.return_value = b"\xFF" * SECTOR_SIZE
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 200,
        expected_pattern=b"\x00",
        sample_count=50,
    )
    assert result.success is False
    assert result.sectors_matched == 0


def test_sample_verify_empty_drive():
    result = sample_verify(
        handle=1,
        drive_size=0,
        expected_pattern=b"\x00",
        sample_count=10,
    )
    assert result.success is False
    assert result.sectors_checked == 0


@patch("wipe.verify._read_sector")
def test_sample_verify_random_mode_non_zero(mock_read):
    # Non-zero data should count as "matched" in random mode
    mock_read.return_value = b"\xAB" * SECTOR_SIZE
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"",
        sample_count=20,
    )
    assert result.success is True
    assert result.sectors_matched == 20


@patch("wipe.verify._read_sector")
def test_sample_verify_random_mode_all_zero_fails(mock_read):
    # All-zero sectors should FAIL in random mode (expected random data)
    mock_read.return_value = b"\x00" * SECTOR_SIZE
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"",
        sample_count=10,
    )
    assert result.success is False
    assert result.sectors_matched == 0


@patch("wipe.verify._read_sector", return_value=None)
def test_sample_verify_read_failure(mock_read):
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"\x00",
        sample_count=10,
    )
    # No sectors could be read -> fails
    assert result.success is False
    assert result.sectors_checked == 0


# ─────────────────────────────────────────────────────────────────────
# VerifyResult dataclass
# ─────────────────────────────────────────────────────────────────────

def test_verify_result_fields():
    vr = VerifyResult(
        success=True,
        method="sample",
        bytes_verified=51200,
        expected_pattern="zeros",
        error_count=0,
        mismatch_offsets=[],
        duration_seconds=1.23,
        sectors_checked=100,
        sectors_matched=100,
        sample_hash="abc123",
        timestamp=datetime(2026, 1, 1),
    )
    assert vr.success is True
    assert vr.method == "sample"
    assert vr.sectors_checked == 100
    assert vr.sample_hash == "abc123"
    assert vr.expected_pattern == "zeros"


def test_verification_result_alias():
    """VerificationResult is kept as a deprecated alias for VerifyResult."""
    assert VerificationResult is VerifyResult


@patch("wipe.verify._read_sector")
def test_sample_verify_returns_new_verifyresult_shape(mock_read):
    """New test: assert returned VerifyResult has method='sample' and legacy fields."""
    mock_read.return_value = b"\x00" * SECTOR_SIZE
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 200,
        expected_pattern=b"\x00",
        sample_count=30,
    )
    assert isinstance(result, VerifyResult)
    assert result.method == "sample"
    assert result.expected_pattern == "zeros"
    assert result.bytes_verified == 30 * SECTOR_SIZE
    assert result.error_count == 0
    assert result.mismatch_offsets == []
    assert result.duration_seconds >= 0.0
    # legacy fields still populated
    assert result.sectors_checked == 30
    assert result.sectors_matched == 30
    assert len(result.sample_hash) == 64


@patch("wipe.verify._read_sector")
def test_sample_verify_pattern_label_custom(mock_read):
    mock_read.return_value = b"\xAA" * SECTOR_SIZE
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"\xAA",
        sample_count=10,
    )
    assert result.expected_pattern == "custom:0xAA"


@patch("wipe.verify._read_sector")
def test_sample_verify_pattern_label_ff(mock_read):
    mock_read.return_value = b"\xFF" * SECTOR_SIZE
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"\xFF",
        sample_count=10,
    )
    assert result.expected_pattern == "0xFF"


@patch("wipe.verify._read_sector")
def test_sample_verify_pattern_label_random(mock_read):
    mock_read.return_value = b"\xAB" * SECTOR_SIZE
    result = sample_verify(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"",
        sample_count=10,
    )
    assert result.expected_pattern == "non-zero (random)"


@patch("wipe.verify._read_sector")
def test_verify_wipe_alias_still_works(mock_read):
    """Deprecated verify_wipe alias should still work and return VerifyResult."""
    mock_read.return_value = b"\x00" * SECTOR_SIZE
    result = verify_wipe(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"\x00",
        sample_count=20,
    )
    assert isinstance(result, VerifyResult)
    assert result.method == "sample"
    assert result.success is True


# ─────────────────────────────────────────────────────────────────────
# full_verify tests
# ─────────────────────────────────────────────────────────────────────

def _install_full_verify_mocks(
    kernel32_mock,
    read_side_effect,
    set_pointer_success: bool = True,
):
    """Wire a mock kernel32 so full_verify can iterate read calls.

    read_side_effect is a callable (idx, size) -> (success: bool, data: bytes).
    It is invoked for each ReadFile call.
    """
    kernel32_mock.reset_mock()

    def _set_ptr(*args, **kwargs):
        return 1 if set_pointer_success else 0

    kernel32_mock.SetFilePointerEx.side_effect = _set_ptr

    call_state = {"index": 0}

    def _read_file(handle, buf, size, bytes_read_ref, overlapped):
        idx = call_state["index"]
        call_state["index"] += 1
        success, data = read_side_effect(idx, size)
        if success:
            data_to_write = data[:size]
            ctypes.memmove(buf, data_to_write, len(data_to_write))
            bytes_read_ref._obj.value = len(data_to_write)
            return 1
        else:
            bytes_read_ref._obj.value = 0
            return 0

    kernel32_mock.ReadFile.side_effect = _read_file


@patch("wipe.verify.kernel32")
def test_full_verify_success_all_zeros(mock_kernel32):
    drive_size = BLOCK_SIZE * 3

    def reader(idx, size):
        return True, b"\x00" * size

    _install_full_verify_mocks(mock_kernel32, reader)

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\x00",
    )
    assert result.success is True
    assert result.method == "full"
    assert result.error_count == 0
    assert result.bytes_verified == drive_size
    assert result.expected_pattern == "zeros"
    assert result.mismatch_offsets == []


@patch("wipe.verify.kernel32")
def test_full_verify_success_all_ff(mock_kernel32):
    drive_size = BLOCK_SIZE * 2

    def reader(idx, size):
        return True, b"\xFF" * size

    _install_full_verify_mocks(mock_kernel32, reader)

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\xFF",
    )
    assert result.success is True
    assert result.error_count == 0
    assert result.bytes_verified == drive_size
    assert result.expected_pattern == "0xFF"


@patch("wipe.verify.kernel32")
def test_full_verify_detects_single_mismatch_at_known_offset(mock_kernel32):
    """drive_size 3 blocks; block 2 (index 1) has a wrong byte at offset 5."""
    drive_size = BLOCK_SIZE * 3

    def reader(idx, size):
        if idx == 1:
            buf = bytearray(b"\x00" * size)
            buf[5] = 0xFF
            return True, bytes(buf)
        return True, b"\x00" * size

    _install_full_verify_mocks(mock_kernel32, reader)

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\x00",
    )
    assert result.success is False
    assert result.error_count == 1
    assert result.mismatch_offsets == [BLOCK_SIZE + 5]


@patch("wipe.verify.kernel32")
def test_full_verify_collects_up_to_100_mismatches(mock_kernel32):
    drive_size = BLOCK_SIZE * 200

    def reader(idx, size):
        buf = bytearray(b"\x00" * size)
        buf[0] = 0xFF
        return True, bytes(buf)

    _install_full_verify_mocks(mock_kernel32, reader)

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\x00",
    )
    assert result.success is False
    assert result.error_count == 200
    assert len(result.mismatch_offsets) == 100


@patch("wipe.verify.kernel32")
def test_full_verify_progress_callback_fractions(mock_kernel32):
    """Callbacks: monotonic, first after ≥50 MB, last ≈1.0, all in [0.0, 1.0]."""
    drive_size = BLOCK_SIZE * 200  # 200 MB

    def reader(idx, size):
        return True, b"\x00" * size

    _install_full_verify_mocks(mock_kernel32, reader)

    calls = []

    def progress_cb(fraction, bytes_verified, total_bytes, speed_mbps):
        calls.append((fraction, bytes_verified, total_bytes, speed_mbps))

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\x00",
        progress_callback=progress_cb,
    )
    assert result.success is True
    assert len(calls) >= 1
    fractions = [c[0] for c in calls]
    for i in range(1, len(fractions)):
        assert fractions[i] >= fractions[i - 1]
    for f in fractions:
        assert 0.0 <= f <= 1.0
    assert calls[0][1] >= 50 * 1024 * 1024
    assert fractions[-1] == pytest.approx(1.0, abs=1e-6)
    for c in calls:
        assert c[2] == drive_size


@patch("wipe.verify.kernel32")
def test_full_verify_large_drive_5gb(mock_kernel32):
    """5 GB drive: every byte is verified and error-recovery re-seeks above 2**32.

    full_verify now only calls SetFilePointerEx once up front on the happy path
    (Windows auto-advances the file pointer after each ReadFile), so we can no
    longer assert "some seek > 2**32" on a clean run. Instead we:

      1. Force a ReadFile failure on a block past the 2**32 boundary. That
         triggers the error-recovery path, which re-seeks to block_start — so
         we still exercise int64 offset handling with a real >2**32 argument.
      2. Assert the run still covers all 5 GB otherwise.
    """
    drive_size = 5 * 1024**3  # 5 GB
    # First block past 2**32: block index ceil(2**32 / 1 MiB) = 4096.
    fail_idx = 4096

    def reader(idx, size):
        if idx == fail_idx:
            return False, b""
        return True, b"\x00" * size

    _install_full_verify_mocks(mock_kernel32, reader)

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\x00",
    )
    # One forced ReadFile failure → one error, every other block verified.
    assert result.bytes_verified == drive_size
    assert result.error_count == 1
    assert result.success is False

    # The ctypes prototype we installed in verify.py guarantees the offset
    # argument is c_int64 on every call; assert that contract holds and that
    # error recovery re-seeks past 2**32.
    saw_above_32bit = False
    for call in mock_kernel32.SetFilePointerEx.call_args_list:
        args = call.args
        assert len(args) >= 2
        pos_arg = args[1]
        assert isinstance(pos_arg, ctypes.c_int64)
        if pos_arg.value > 2**32:
            saw_above_32bit = True
    assert saw_above_32bit, "expected error-recovery seek above 2**32"


@patch("wipe.verify.kernel32")
def test_full_verify_non_multiple_block_size(mock_kernel32):
    """drive_size = block_size * 2 + 123 — last read exactly 123 bytes."""
    drive_size = BLOCK_SIZE * 2 + 123

    def reader(idx, size):
        return True, b"\x00" * size

    _install_full_verify_mocks(mock_kernel32, reader)

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\x00",
    )
    assert result.success is True
    assert result.bytes_verified == drive_size

    sizes_read = [c.args[2] for c in mock_kernel32.ReadFile.call_args_list]
    assert 123 in sizes_read


@patch("wipe.verify.audit_log")
@patch("wipe.verify.kernel32")
def test_full_verify_read_error_continues(mock_kernel32, mock_audit):
    """Middle block read fails; verify error_count increments, rest verified."""
    drive_size = BLOCK_SIZE * 3

    def reader(idx, size):
        if idx == 1:
            return False, b""
        return True, b"\x00" * size

    _install_full_verify_mocks(mock_kernel32, reader)

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\x00",
    )
    assert result.success is False
    assert result.error_count == 1
    assert BLOCK_SIZE in result.mismatch_offsets
    assert mock_kernel32.ReadFile.call_count == 3


@patch("wipe.verify.audit_log")
@patch("wipe.verify.kernel32")
def test_full_verify_caps_block_size_at_4mb(mock_kernel32, mock_audit):
    """Caller passes block_size=16 MB; actual reads should be ≤ 4 MB."""
    requested_block = 16 * 1024 * 1024
    cap = 4 * 1024 * 1024
    drive_size = cap * 3

    def reader(idx, size):
        return True, b"\x00" * size

    _install_full_verify_mocks(mock_kernel32, reader)

    result = full_verify(
        handle=1,
        drive_size=drive_size,
        expected_pattern=b"\x00",
        block_size=requested_block,
    )
    assert result.success is True
    sizes_read = [c.args[2] for c in mock_kernel32.ReadFile.call_args_list]
    for s in sizes_read:
        assert s <= cap
