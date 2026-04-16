"""Post-wipe verification via random sector sampling."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import random
from dataclasses import dataclass
from datetime import datetime

kernel32 = ctypes.windll.kernel32

LARGE_INTEGER = ctypes.c_int64
FILE_BEGIN = 0
SECTOR_SIZE = 512


@dataclass
class VerificationResult:
    passed: bool
    sectors_checked: int
    sectors_matched: int
    sample_hash: str
    timestamp: datetime


def _set_file_pointer(handle: int, position: int) -> bool:
    new_pos = LARGE_INTEGER(0)
    return bool(kernel32.SetFilePointerEx(
        ctypes.wintypes.HANDLE(handle),
        LARGE_INTEGER(position),
        ctypes.byref(new_pos),
        FILE_BEGIN,
    ))


def _read_sector(handle: int, offset: int) -> bytes | None:
    if not _set_file_pointer(handle, offset):
        return None
    buf = ctypes.create_string_buffer(SECTOR_SIZE)
    bytes_read = ctypes.wintypes.DWORD(0)
    success = kernel32.ReadFile(
        ctypes.wintypes.HANDLE(handle),
        buf,
        SECTOR_SIZE,
        ctypes.byref(bytes_read),
        None,
    )
    if not success or bytes_read.value != SECTOR_SIZE:
        return None
    return buf.raw


def verify_wipe(
    handle: int,
    drive_size: int,
    expected_pattern: bytes,
    sample_count: int = 100,
) -> VerificationResult:
    max_sector = (drive_size // SECTOR_SIZE) - 1
    if max_sector < 1:
        return VerificationResult(
            passed=False,
            sectors_checked=0,
            sectors_matched=0,
            sample_hash="",
            timestamp=datetime.now(),
        )

    sample_count = min(sample_count, max_sector + 1)
    offsets = sorted(random.sample(range(max_sector + 1), sample_count))

    is_random_check = len(expected_pattern) == 0
    if not is_random_check:
        # Build the expected sector from the pattern (tile to SECTOR_SIZE)
        full_pattern = (expected_pattern * ((SECTOR_SIZE // len(expected_pattern)) + 1))[:SECTOR_SIZE]
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
            # For random-data wipes, just verify sectors are not all zeros
            if data != b"\x00" * SECTOR_SIZE:
                sectors_matched += 1
        else:
            if data == full_pattern:
                sectors_matched += 1

    passed = sectors_checked > 0 and sectors_matched == sectors_checked

    return VerificationResult(
        passed=passed,
        sectors_checked=sectors_checked,
        sectors_matched=sectors_matched,
        sample_hash=hasher.hexdigest(),
        timestamp=datetime.now(),
    )
