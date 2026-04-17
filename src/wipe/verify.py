"""Post-wipe verification — sample (probabilistic) and full (exhaustive) modes."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from core.log import audit_log

# Load kernel32 with explicit last-error tracking and typed prototypes for the
# handful of Win32 APIs we call. This removes the implicit int-sized argument
# coercions that `ctypes.windll.kernel32` performs and guarantees that 64-bit
# file offsets are passed as genuine LARGE_INTEGERs on all Python versions.
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.SetFilePointerEx.argtypes = [
    wintypes.HANDLE, ctypes.c_int64, ctypes.POINTER(ctypes.c_int64), wintypes.DWORD,
]
kernel32.SetFilePointerEx.restype = wintypes.BOOL

kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
kernel32.ReadFile.restype = wintypes.BOOL

LARGE_INTEGER = ctypes.c_int64
FILE_BEGIN = 0
SECTOR_SIZE = 512

DEFAULT_BLOCK_SIZE = 1_048_576          # 1 MB
MAX_BLOCK_SIZE = 4 * 1024 * 1024        # 4 MB cap
PROGRESS_INTERVAL_BYTES = 50 * 1024 * 1024  # 50 MB between progress fires
MAX_MISMATCH_OFFSETS = 100


@dataclass
class VerifyResult:
    success: bool
    method: str  # "sample" | "full" | "none"
    bytes_verified: int
    expected_pattern: str  # human-readable: "zeros", "0xFF", "custom:0xAA", "non-zero (random)"
    error_count: int
    mismatch_offsets: list[int]  # byte offsets, first 100 only
    duration_seconds: float
    # Backward-compat fields (for sample mode):
    sectors_checked: int = 0
    sectors_matched: int = 0
    sample_hash: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


# Deprecated alias for backward compat — existing callers keep working.
VerificationResult = VerifyResult


# Progress callback: (fraction, bytes_verified, total_bytes, speed_mbps)
VerifyProgressCallback = Callable[[float, int, int, float], None]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _pattern_label(expected_pattern: bytes) -> str:
    """Human-readable label for the expected pattern."""
    if len(expected_pattern) == 0:
        return "non-zero (random)"
    if expected_pattern == b"\x00":
        return "zeros"
    if expected_pattern == b"\xFF":
        return "0xFF"
    # Single-byte custom pattern → hex string; multi-byte → show first byte.
    return f"custom:0x{expected_pattern[0]:02X}"


def _tile_pattern(pattern: bytes, size: int) -> bytes:
    """Tile `pattern` to exactly `size` bytes."""
    if len(pattern) == 0:
        return b""
    repeats = (size // len(pattern)) + 1
    return (pattern * repeats)[:size]


def _set_file_pointer(handle: int, position: int) -> bool:
    """Seek to absolute 64-bit byte offset on the volume handle."""
    new_pos = LARGE_INTEGER(0)
    return bool(kernel32.SetFilePointerEx(
        wintypes.HANDLE(handle),
        LARGE_INTEGER(position),
        ctypes.byref(new_pos),
        FILE_BEGIN,
    ))


def _read_sector(handle: int, offset: int) -> bytes | None:
    """Read exactly one 512-byte sector at `offset`. Returns None on failure."""
    if not _set_file_pointer(handle, offset):
        return None
    buf = ctypes.create_string_buffer(SECTOR_SIZE)
    bytes_read = wintypes.DWORD(0)
    success = kernel32.ReadFile(
        wintypes.HANDLE(handle),
        buf,
        SECTOR_SIZE,
        ctypes.byref(bytes_read),
        None,
    )
    if not success or bytes_read.value != SECTOR_SIZE:
        return None
    return buf.raw


def _read_block(handle: int, size: int) -> bytes | None:
    """Read `size` bytes at the current file pointer.

    Caller is responsible for seeking first via _set_file_pointer.
    Returns None on failure.
    """
    buf = ctypes.create_string_buffer(size)
    bytes_read = wintypes.DWORD(0)
    success = kernel32.ReadFile(
        wintypes.HANDLE(handle),
        buf,
        size,
        ctypes.byref(bytes_read),
        None,
    )
    if not success or bytes_read.value != size:
        return None
    return buf.raw


# ─────────────────────────────────────────────────────────────────────
# sample_verify
# ─────────────────────────────────────────────────────────────────────

def sample_verify(
    handle: int,
    drive_size: int,
    expected_pattern: bytes,
    sample_count: int = 100,
) -> VerifyResult:
    """Random-sector sampling verification (fast, probabilistic)."""
    start_time = time.monotonic()
    pattern_label = _pattern_label(expected_pattern)

    max_sector = (drive_size // SECTOR_SIZE) - 1
    if max_sector < 1:
        return VerifyResult(
            success=False,
            method="sample",
            bytes_verified=0,
            expected_pattern=pattern_label,
            error_count=0,
            mismatch_offsets=[],
            duration_seconds=time.monotonic() - start_time,
            sectors_checked=0,
            sectors_matched=0,
            sample_hash="",
            timestamp=datetime.now(),
        )

    sample_count = min(sample_count, max_sector + 1)
    offsets = sorted(random.sample(range(max_sector + 1), sample_count))

    is_random_check = len(expected_pattern) == 0
    if not is_random_check:
        full_pattern = _tile_pattern(expected_pattern, SECTOR_SIZE)
    else:
        full_pattern = b""

    sectors_checked = 0
    sectors_matched = 0
    hasher = hashlib.sha256()

    for sector_index in offsets:
        byte_offset = sector_index * SECTOR_SIZE
        data = _read_sector(handle, byte_offset)
        if data is None:
            continue

        sectors_checked += 1
        hasher.update(data)

        if is_random_check:
            if data != b"\x00" * SECTOR_SIZE:
                sectors_matched += 1
        else:
            if data == full_pattern:
                sectors_matched += 1

    success = sectors_checked > 0 and sectors_matched == sectors_checked
    error_count = sectors_checked - sectors_matched
    bytes_verified = sectors_checked * SECTOR_SIZE

    return VerifyResult(
        success=success,
        method="sample",
        bytes_verified=bytes_verified,
        expected_pattern=pattern_label,
        error_count=error_count,
        mismatch_offsets=[],
        duration_seconds=time.monotonic() - start_time,
        sectors_checked=sectors_checked,
        sectors_matched=sectors_matched,
        sample_hash=hasher.hexdigest(),
        timestamp=datetime.now(),
    )


# ─────────────────────────────────────────────────────────────────────
# full_verify
# ─────────────────────────────────────────────────────────────────────

def full_verify(
    handle: int,
    drive_size: int,
    expected_pattern: bytes,
    block_size: int = DEFAULT_BLOCK_SIZE,
    progress_callback: VerifyProgressCallback | None = None,
) -> VerifyResult:
    """Read every sector and compare against the expected pattern."""
    start_time = time.monotonic()
    pattern_label = _pattern_label(expected_pattern)

    # Clamp block_size to 4 MB cap.
    if block_size > MAX_BLOCK_SIZE:
        audit_log(
            f"full_verify: block_size {block_size} exceeds cap "
            f"{MAX_BLOCK_SIZE}; clamping to {MAX_BLOCK_SIZE}"
        )
        block_size = MAX_BLOCK_SIZE
    if block_size <= 0:
        block_size = DEFAULT_BLOCK_SIZE

    if drive_size <= 0:
        return VerifyResult(
            success=False,
            method="full",
            bytes_verified=0,
            expected_pattern=pattern_label,
            error_count=0,
            mismatch_offsets=[],
            duration_seconds=time.monotonic() - start_time,
            timestamp=datetime.now(),
        )

    is_random_check = len(expected_pattern) == 0
    # Pre-allocate reference blocks once so the hot loop only has to compare
    # against existing bytes objects instead of materialising a fresh
    # `b"\x00" * block_size` (or a freshly tiled pattern) on every iteration.
    # The tail block — which may be shorter than `block_size` — is rebuilt
    # on demand, but that happens at most once per full_verify call.
    zero_block = b"\x00" * block_size
    expected_full_block: bytes | None = None
    if not is_random_check:
        expected_full_block = _tile_pattern(expected_pattern, block_size)

    # Seek to 0 once before we start. Windows auto-advances the file pointer
    # after each successful ReadFile, so we do NOT re-seek per block. We only
    # re-seek on error-recovery paths (after a failed SetFilePointerEx or
    # ReadFile) in order to resume at the next block boundary.
    if not _set_file_pointer(handle, 0):
        audit_log("full_verify: failed initial SetFilePointerEx to 0")
        return VerifyResult(
            success=False,
            method="full",
            bytes_verified=0,
            expected_pattern=pattern_label,
            error_count=1,
            mismatch_offsets=[0],
            duration_seconds=time.monotonic() - start_time,
            timestamp=datetime.now(),
        )

    bytes_verified = 0
    error_count = 0
    mismatch_offsets: list[int] = []
    last_progress_bytes = 0
    needs_seek = False  # set on error-recovery; forces a re-seek next iter.

    block_start = 0
    while block_start < drive_size:
        remaining = drive_size - block_start
        this_block_size = min(block_size, remaining)

        # Re-seek only on recovery paths. The file pointer has already been
        # advanced by the previous successful ReadFile call on the happy path.
        if needs_seek:
            if not _set_file_pointer(handle, block_start):
                audit_log(
                    f"full_verify: SetFilePointerEx failed at offset {block_start}"
                )
                error_count += 1
                if len(mismatch_offsets) < MAX_MISMATCH_OFFSETS:
                    mismatch_offsets.append(block_start)
                block_start += this_block_size
                bytes_verified += this_block_size
                # leave needs_seek=True so the next iteration re-seeks again.
                continue
            needs_seek = False

        data = _read_block(handle, this_block_size)
        if data is None:
            audit_log(
                f"full_verify: ReadFile failed at offset {block_start} "
                f"(size {this_block_size})"
            )
            error_count += 1
            if len(mismatch_offsets) < MAX_MISMATCH_OFFSETS:
                mismatch_offsets.append(block_start)
            block_start += this_block_size
            bytes_verified += this_block_size
            # A failed ReadFile leaves the file pointer at an indeterminate
            # position — force a re-seek to block_start before the next read.
            needs_seek = True
            continue

        # Compare against the pre-built reference block on the hot path; only
        # build a bespoke tail block when the final read is shorter.
        if is_random_check:
            # "non-zero (random)" — block must not be all zeros.
            if this_block_size == block_size:
                expected_block = zero_block
            else:
                expected_block = b"\x00" * this_block_size
            if data == expected_block:
                error_count += 1
                if len(mismatch_offsets) < MAX_MISMATCH_OFFSETS:
                    mismatch_offsets.append(block_start)
        else:
            if this_block_size == block_size and expected_full_block is not None:
                expected_block = expected_full_block
            else:
                expected_block = _tile_pattern(expected_pattern, this_block_size)

            if data != expected_block:
                error_count += 1
                if len(mismatch_offsets) < MAX_MISMATCH_OFFSETS:
                    diff_offset = 0
                    for i in range(this_block_size):
                        if data[i] != expected_block[i]:
                            diff_offset = i
                            break
                    mismatch_offsets.append(block_start + diff_offset)

        block_start += this_block_size
        bytes_verified += this_block_size

        # Fire progress callback every ~50 MB.
        if progress_callback is not None:
            if bytes_verified - last_progress_bytes >= PROGRESS_INTERVAL_BYTES:
                elapsed = time.monotonic() - start_time
                speed_mbps = (
                    (bytes_verified / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
                )
                fraction = bytes_verified / drive_size if drive_size > 0 else 1.0
                progress_callback(fraction, bytes_verified, drive_size, speed_mbps)
                last_progress_bytes = bytes_verified

    # Final 100% callback. Only fire if the in-loop tick hasn't already landed
    # at drive_size (which happens whenever drive_size is an exact multiple of
    # PROGRESS_INTERVAL_BYTES). Comparing against `drive_size` instead of the
    # previous loop-local `bytes_verified != last_progress_bytes` check stops
    # the GUI bar from freezing at 98% on clean-multiple drive sizes.
    if progress_callback is not None and drive_size > 0:
        if last_progress_bytes < drive_size:
            elapsed = time.monotonic() - start_time
            speed_mbps = (
                (bytes_verified / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
            )
            fraction = bytes_verified / drive_size if drive_size > 0 else 1.0
            progress_callback(fraction, bytes_verified, drive_size, speed_mbps)

    success = error_count == 0
    duration_seconds = time.monotonic() - start_time
    # Surface an accurate sector count so full-mode certificates no longer
    # report "Sectors Checked: 0" on e.g. a 32 GB scan.
    sectors_checked_count = bytes_verified // SECTOR_SIZE

    return VerifyResult(
        success=success,
        method="full",
        bytes_verified=bytes_verified,
        expected_pattern=pattern_label,
        error_count=error_count,
        mismatch_offsets=mismatch_offsets,
        duration_seconds=duration_seconds,
        sectors_checked=sectors_checked_count,
        timestamp=datetime.now(),
    )


# ─────────────────────────────────────────────────────────────────────
# Deprecated alias
# ─────────────────────────────────────────────────────────────────────

def verify_wipe(
    handle: int,
    drive_size: int,
    expected_pattern: bytes,
    sample_count: int = 100,
) -> VerifyResult:
    """Deprecated. Use sample_verify(). Kept for backward compatibility."""
    return sample_verify(handle, drive_size, expected_pattern, sample_count)
