"""Tests for wipe.verify — verify_wipe and VerificationResult."""

import ctypes
import ctypes.wintypes
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# Ensure ctypes.windll exists on non-Windows platforms.
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()
ctypes.windll.kernel32 = MagicMock()

from wipe.verify import verify_wipe, VerificationResult, SECTOR_SIZE


def _make_read_sector(sector_data: bytes):
    """Return a replacement for _read_sector that always returns *sector_data*."""
    def _read_sector(handle, offset):
        return sector_data
    return _read_sector


# ── verify_wipe: matching pattern ─────────────────────────────────────

@patch("wipe.verify._read_sector")
def test_verify_wipe_all_zeros_passed(mock_read):
    mock_read.return_value = b"\x00" * SECTOR_SIZE
    result = verify_wipe(
        handle=1,
        drive_size=SECTOR_SIZE * 200,
        expected_pattern=b"\x00",
        sample_count=50,
    )
    assert result.passed is True
    assert result.sectors_checked == 50
    assert result.sectors_matched == 50
    assert len(result.sample_hash) == 64  # SHA-256 hex


# ── verify_wipe: mismatched pattern ──────────────────────────────────

@patch("wipe.verify._read_sector")
def test_verify_wipe_mismatch_fails(mock_read):
    # Disk has 0xFF but we expect 0x00
    mock_read.return_value = b"\xFF" * SECTOR_SIZE
    result = verify_wipe(
        handle=1,
        drive_size=SECTOR_SIZE * 200,
        expected_pattern=b"\x00",
        sample_count=50,
    )
    assert result.passed is False
    assert result.sectors_matched == 0


# ── verify_wipe: zero-size drive ─────────────────────────────────────

def test_verify_wipe_empty_drive():
    result = verify_wipe(
        handle=1,
        drive_size=0,
        expected_pattern=b"\x00",
        sample_count=10,
    )
    assert result.passed is False
    assert result.sectors_checked == 0


# ── verify_wipe: random check mode ──────────────────────────────────

@patch("wipe.verify._read_sector")
def test_verify_wipe_random_mode_non_zero(mock_read):
    # Non-zero data should count as "matched" in random mode
    mock_read.return_value = b"\xAB" * SECTOR_SIZE
    result = verify_wipe(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"",
        sample_count=20,
    )
    assert result.passed is True
    assert result.sectors_matched == 20


@patch("wipe.verify._read_sector")
def test_verify_wipe_random_mode_all_zero_fails(mock_read):
    # All-zero sectors should FAIL in random mode (expected random data)
    mock_read.return_value = b"\x00" * SECTOR_SIZE
    result = verify_wipe(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"",
        sample_count=10,
    )
    assert result.passed is False
    assert result.sectors_matched == 0


# ── verify_wipe: read failure ────────────────────────────────────────

@patch("wipe.verify._read_sector", return_value=None)
def test_verify_wipe_read_failure(mock_read):
    result = verify_wipe(
        handle=1,
        drive_size=SECTOR_SIZE * 100,
        expected_pattern=b"\x00",
        sample_count=10,
    )
    # No sectors could be read -> fails
    assert result.passed is False
    assert result.sectors_checked == 0


# ── VerificationResult dataclass ─────────────────────────────────────

def test_verification_result_fields():
    vr = VerificationResult(
        passed=True,
        sectors_checked=100,
        sectors_matched=100,
        sample_hash="abc123",
        timestamp=datetime(2026, 1, 1),
    )
    assert vr.passed is True
    assert vr.sectors_checked == 100
    assert vr.sample_hash == "abc123"
