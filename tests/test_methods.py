"""Tests for wipe.methods — pattern generators and WipeMethod.execute."""

import ctypes
import ctypes.wintypes
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# We must mock ctypes.windll before importing wipe.methods because the module
# does `kernel32 = ctypes.windll.kernel32` at import time.  On non-Windows
# platforms ctypes.windll does not exist at all.
_fake_kernel32 = MagicMock()
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()
ctypes.windll.kernel32 = _fake_kernel32

from wipe.methods import (
    ZeroFill,
    RandomThreePass,
    BsiVsitr,
    CustomWipe,
    WipeResult,
    WipeMethod,
    _write_block,
    _set_file_pointer,
)


def _make_fake_verify_result(success: bool = True, method: str = "sample"):
    """Build a minimal stand-in for a verify.VerifyResult.

    The real dataclass lives in wipe.verify (being implemented in parallel).
    Tests only depend on a small subset of its attributes, so a SimpleNamespace
    avoids coupling to the other module.
    """
    from types import SimpleNamespace
    return SimpleNamespace(
        success=success,
        method=method,
        bytes_verified=0,
        expected_pattern=r"\x00",
        error_count=0,
        mismatch_offsets=[],
        duration_seconds=0.0,
        sectors_checked=0,
        sectors_matched=0,
        sample_hash="",
        timestamp=datetime.now(),
    )


# ── ZeroFill ──────────────────────────────────────────────────────────

def test_zerofill_pattern_is_all_zeros():
    zf = ZeroFill()
    pat = zf.get_pattern(1, 4096)
    assert pat == b"\x00" * 4096


def test_zerofill_metadata():
    zf = ZeroFill()
    assert zf.name == "ZeroFill"
    assert zf.passes == 1
    assert zf.sicherheitsstufe == "1-2"


def test_zerofill_pattern_various_sizes():
    zf = ZeroFill()
    for size in (1, 512, 1048576):
        pat = zf.get_pattern(1, size)
        assert len(pat) == size
        assert set(pat) == {0}


# ── RandomThreePass ───────────────────────────────────────────────────

def test_random_three_pass_correct_length():
    rtp = RandomThreePass()
    pat = rtp.get_pattern(1, 4096)
    assert len(pat) == 4096


def test_random_three_pass_is_random():
    rtp = RandomThreePass()
    p1 = rtp.get_pattern(1, 4096)
    p2 = rtp.get_pattern(1, 4096)
    # Astronomically unlikely to be equal
    assert p1 != p2


def test_random_three_pass_metadata():
    rtp = RandomThreePass()
    assert rtp.passes == 3
    assert rtp.sicherheitsstufe == "3"


# ── BsiVsitr ──────────────────────────────────────────────────────────

def test_bsi_vsitr_pass_1_zeros():
    bsi = BsiVsitr()
    assert bsi.get_pattern(1, 512) == b"\x00" * 512


def test_bsi_vsitr_pass_2_ones():
    bsi = BsiVsitr()
    assert bsi.get_pattern(2, 512) == b"\xFF" * 512


def test_bsi_vsitr_alternation():
    bsi = BsiVsitr()
    expected = [b"\x00", b"\xFF", b"\x00", b"\xFF", b"\x00", b"\xFF"]
    for pass_num in range(1, 7):
        pat = bsi.get_pattern(pass_num, 16)
        assert pat == expected[pass_num - 1] * 16


def test_bsi_vsitr_pass_7_random():
    bsi = BsiVsitr()
    p1 = bsi.get_pattern(7, 1024)
    p2 = bsi.get_pattern(7, 1024)
    assert len(p1) == 1024
    assert p1 != p2  # random


def test_bsi_vsitr_metadata():
    bsi = BsiVsitr()
    assert bsi.name == "BSI-VSITR"
    assert bsi.passes == 7
    assert bsi.sicherheitsstufe == "4+"


# ── CustomWipe ────────────────────────────────────────────────────────

def test_custom_wipe_zero_pattern():
    cw = CustomWipe(passes=2, pattern="zero")
    assert cw.get_pattern(1, 256) == b"\x00" * 256


def test_custom_wipe_ones_pattern():
    cw = CustomWipe(passes=1, pattern="ones")
    assert cw.get_pattern(1, 256) == b"\xFF" * 256


def test_custom_wipe_random_pattern():
    cw = CustomWipe(passes=1, pattern="random")
    p1 = cw.get_pattern(1, 256)
    p2 = cw.get_pattern(1, 256)
    assert len(p1) == 256
    assert p1 != p2


def test_custom_wipe_custom_byte():
    cw = CustomWipe(passes=1, pattern="custom", custom_byte=0xAB)
    assert cw.get_pattern(1, 128) == b"\xAB" * 128


def test_custom_wipe_invalid_pattern():
    cw = CustomWipe(passes=1, pattern="bogus")
    with pytest.raises(ValueError, match="Unknown pattern"):
        cw.get_pattern(1, 64)


def test_custom_wipe_name_includes_pattern():
    cw = CustomWipe(passes=3, pattern="ones")
    assert "3x" in cw.name
    assert "ones" in cw.name


# ── WipeMethod.execute ────────────────────────────────────────────────

@patch("wipe.methods.audit_log")
def test_execute_success(mock_audit):
    """Mock kernel32 calls so execute runs without hardware."""
    zf = ZeroFill()
    drive_size = 4096
    block_size = 1024

    # SetFilePointerEx returns True
    _fake_kernel32.SetFilePointerEx.return_value = True

    # WriteFile: sets written.value via side_effect on byref
    def fake_write(handle, data, length, p_written, overlap):
        # p_written is a ctypes pointer; we can't easily set it,
        # so we make WriteFile return True and patch _write_block instead.
        return True

    progress_calls = []

    def progress_cb(pass_num, total_passes, bytes_done, total, speed):
        progress_calls.append((pass_num, bytes_done))

    with patch("wipe.methods._write_block", return_value=block_size) as mock_wb:
        with patch("wipe.methods._set_file_pointer", return_value=True):
            result = zf.execute(
                handle=999,
                drive_size=drive_size,
                block_size=block_size,
                progress_callback=progress_cb,
            )

    assert result.success is True
    assert result.method_name == "ZeroFill"
    assert result.passes == 1
    assert result.bytes_written == drive_size
    assert result.error_message is None
    # Progress is batched to every 50 MB with a guaranteed fire at end-of-pass,
    # so for a 4 KB drive we expect exactly one callback: the end-of-pass one.
    assert len(progress_calls) == 1
    assert progress_calls[0] == (1, drive_size)
    # WriteFile should have been called 4 times (4096 / 1024)
    assert mock_wb.call_count == drive_size // block_size


@patch("wipe.methods.audit_log")
def test_execute_write_error(mock_audit):
    """Verify graceful handling when _write_block raises OSError."""
    zf = ZeroFill()

    with patch("wipe.methods._write_block", side_effect=OSError("disk error")):
        with patch("wipe.methods._set_file_pointer", return_value=True):
            result = zf.execute(handle=999, drive_size=4096, block_size=1024)

    assert result.success is False
    assert "disk error" in result.error_message


@patch("wipe.methods.audit_log")
def test_execute_seek_error(mock_audit):
    """Verify graceful handling when _set_file_pointer fails."""
    zf = ZeroFill()

    with patch("wipe.methods._set_file_pointer", return_value=False):
        result = zf.execute(handle=999, drive_size=4096, block_size=1024)

    assert result.success is False
    assert result.error_message is not None


# ── verify_mode integration (v1.1) ────────────────────────────────────

@patch("wipe.methods.audit_log")
def test_execute_verify_mode_none_no_extra_pass(mock_audit):
    """verify_mode='none' should keep legacy behaviour: no extra pass, no verify call."""
    rtp = RandomThreePass()  # random method — would otherwise trigger zero-blanking
    drive_size = 2048
    block_size = 1024

    with patch("wipe.methods._write_block", return_value=block_size) as mock_wb, \
         patch("wipe.methods._set_file_pointer", return_value=True), \
         patch("wipe.methods.sample_verify") as mock_sample, \
         patch("wipe.methods.full_verify") as mock_full:
        result = rtp.execute(
            handle=999,
            drive_size=drive_size,
            block_size=block_size,
            verify_mode="none",
        )

    assert result.success is True
    assert result.zero_blank_appended is False
    assert result.verify_result is None
    assert result.passes == 3  # unchanged — 3 random passes, no zero blank
    # Exactly 3 passes * 2 blocks each = 6 write calls.
    assert mock_wb.call_count == 3 * (drive_size // block_size)
    mock_sample.assert_not_called()
    mock_full.assert_not_called()


@patch("wipe.methods.audit_log")
def test_execute_random_method_appends_zero_pass_when_verify_on(mock_audit):
    """RandomThreePass + verify='sample' appends a zero-blanking pass as the 4th pass."""
    rtp = RandomThreePass()
    drive_size = 2048
    block_size = 1024

    captured_writes: list[bytes] = []

    def record_write(handle, data):
        captured_writes.append(bytes(data))
        return len(data)

    with patch("wipe.methods._write_block", side_effect=record_write), \
         patch("wipe.methods._set_file_pointer", return_value=True), \
         patch("wipe.methods.sample_verify",
               return_value=_make_fake_verify_result(success=True)):
        result = rtp.execute(
            handle=999,
            drive_size=drive_size,
            block_size=block_size,
            verify_mode="sample",
        )

    assert result.success is True
    assert result.zero_blank_appended is True
    assert result.passes == 4  # 3 random + 1 zero blank
    # Last 2 writes (drive_size // block_size == 2 blocks) belong to zero-blank.
    blocks_per_pass = drive_size // block_size
    last_pass_writes = captured_writes[-blocks_per_pass:]
    assert all(chunk == b"\x00" * block_size for chunk in last_pass_writes), (
        "Final pass must write all-zeros for zero-blanking"
    )


@patch("wipe.methods.audit_log")
def test_execute_zero_fill_no_zero_pass_appended(mock_audit):
    """ZeroFill (final_pass_is_random=False) must NOT get an extra zero-blank pass."""
    zf = ZeroFill()
    drive_size = 2048
    block_size = 1024

    with patch("wipe.methods._write_block", return_value=block_size), \
         patch("wipe.methods._set_file_pointer", return_value=True), \
         patch("wipe.methods.sample_verify",
               return_value=_make_fake_verify_result(success=True)):
        result = zf.execute(
            handle=999,
            drive_size=drive_size,
            block_size=block_size,
            verify_mode="sample",
        )

    assert result.success is True
    assert result.zero_blank_appended is False
    assert result.passes == 1
    assert result.verify_result is not None


@patch("wipe.methods.audit_log")
def test_execute_calls_sample_verify_when_mode_sample(mock_audit):
    """verify_mode='sample' invokes wipe.methods.sample_verify with correct args."""
    zf = ZeroFill()
    drive_size = 2048
    block_size = 1024
    fake_result = _make_fake_verify_result(success=True, method="sample")

    with patch("wipe.methods._write_block", return_value=block_size), \
         patch("wipe.methods._set_file_pointer", return_value=True), \
         patch("wipe.methods.sample_verify", return_value=fake_result) as mock_sample, \
         patch("wipe.methods.full_verify") as mock_full:
        result = zf.execute(
            handle=777,
            drive_size=drive_size,
            block_size=block_size,
            verify_mode="sample",
        )

    mock_sample.assert_called_once()
    mock_full.assert_not_called()
    # Positional args: (handle, drive_size, expected_pattern)
    args, _kwargs = mock_sample.call_args
    assert args[0] == 777
    assert args[1] == drive_size
    assert args[2] == b"\x00"  # ZeroFill writes zeros, so we expect zeros
    assert result.verify_result is fake_result


@patch("wipe.methods.audit_log")
def test_execute_calls_full_verify_when_mode_full(mock_audit):
    """verify_mode='full' invokes full_verify and wires through the progress callback."""
    zf = ZeroFill()
    drive_size = 2048
    block_size = 1024
    fake_result = _make_fake_verify_result(success=True, method="full")
    vp_cb = MagicMock(name="verify_progress_cb")

    with patch("wipe.methods._write_block", return_value=block_size), \
         patch("wipe.methods._set_file_pointer", return_value=True), \
         patch("wipe.methods.full_verify", return_value=fake_result) as mock_full, \
         patch("wipe.methods.sample_verify") as mock_sample:
        result = zf.execute(
            handle=555,
            drive_size=drive_size,
            block_size=block_size,
            verify_mode="full",
            verify_progress_callback=vp_cb,
        )

    mock_full.assert_called_once()
    mock_sample.assert_not_called()
    args, kwargs = mock_full.call_args
    assert args[0] == 555
    assert args[1] == drive_size
    assert args[2] == b"\x00"
    assert kwargs.get("progress_callback") is vp_cb
    assert result.verify_result is fake_result


@patch("wipe.methods.audit_log")
def test_execute_skips_verify_on_wipe_failure(mock_audit):
    """If the wipe itself fails, verification must not be attempted."""
    zf = ZeroFill()

    with patch("wipe.methods._write_block", side_effect=OSError("disk error")), \
         patch("wipe.methods._set_file_pointer", return_value=True), \
         patch("wipe.methods.sample_verify") as mock_sample, \
         patch("wipe.methods.full_verify") as mock_full:
        result = zf.execute(
            handle=999,
            drive_size=4096,
            block_size=1024,
            verify_mode="sample",
        )

    assert result.success is False
    assert result.verify_result is None
    mock_sample.assert_not_called()
    mock_full.assert_not_called()


# ── _expected_final_pattern helper ────────────────────────────────────

def test_expected_final_pattern_random_returns_zero():
    """RandomThreePass is random-final, so expected pattern is zero (post-blank)."""
    rtp = RandomThreePass()
    assert rtp._expected_final_pattern() == b"\x00"


def test_expected_final_pattern_zerofill_returns_zero():
    """ZeroFill's last pass writes zeros, so expected pattern is zero."""
    zf = ZeroFill()
    assert zf._expected_final_pattern() == b"\x00"


def test_expected_final_pattern_bsi_returns_zero():
    """BsiVsitr has a random 7th pass → zero-blank appended → expect zeros."""
    bsi = BsiVsitr()
    assert bsi._expected_final_pattern() == b"\x00"


# ── CustomWipe.final_pass_is_random ───────────────────────────────────

def test_custom_method_random_marks_final_pass_random():
    cw = CustomWipe(passes=2, pattern="random")
    assert cw.final_pass_is_random is True


def test_custom_method_zero_marks_final_pass_not_random():
    cw = CustomWipe(passes=2, pattern="zero")
    assert cw.final_pass_is_random is False
