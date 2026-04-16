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
    _write_block,
    _set_file_pointer,
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
    assert len(progress_calls) == drive_size // block_size
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
