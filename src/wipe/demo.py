"""Demo mode: simulate wiping a virtual disk file without touching real hardware."""

from __future__ import annotations

import hashlib
import os
import random
import tempfile
import time
from datetime import datetime
from pathlib import Path

from core.log import audit_log
from wipe.device import DeviceInfo
from wipe.methods import WipeMethod, WipeResult, ProgressCallback
from wipe.verify import VerificationResult

SECTOR_SIZE = 512
DEFAULT_DEMO_SIZE = 10 * 1024 * 1024  # 10 MB


def _random_hex(n: int = 4) -> str:
    return os.urandom(n // 2 + 1).hex()[:n].upper()


def create_demo_device() -> DeviceInfo:
    """Return a DeviceInfo with fake but realistic-looking data."""
    serial = f"DEMO-2026-{_random_hex(4)}"
    return DeviceInfo(
        drive_letter="DEMO:",
        device_id=r"\\.\DemoDevice0",
        model="StickShredder Demo Drive",
        serial_number=serial,
        capacity_bytes=DEFAULT_DEMO_SIZE,
        filesystem="FAT32",
        connection_type="Virtual",
        is_removable=True,
        is_system_drive=False,
        is_internal=False,
        has_bitlocker=False,
        has_active_processes=False,
        partition_count=1,
        friendly_name="Demo Drive (DEMO:)",
    )


def create_demo_file(size_bytes: int = DEFAULT_DEMO_SIZE, path: str = "") -> str:
    """Create a temporary file filled with random data to simulate a USB drive.

    Returns the path to the created file.
    """
    if not path:
        fd, path = tempfile.mkstemp(prefix="stickshredder_demo_", suffix=".bin")
        os.close(fd)

    audit_log(f"Demo: creating virtual disk file ({size_bytes} bytes) at {path}")

    block_size = 1_048_576  # 1 MB
    remaining = size_bytes
    with open(path, "wb") as f:
        while remaining > 0:
            chunk = min(block_size, remaining)
            f.write(os.urandom(chunk))
            remaining -= chunk

    audit_log(f"Demo: virtual disk file created: {path}")
    return path


def wipe_demo_file(
    path: str,
    method: WipeMethod,
    progress_callback: ProgressCallback | None = None,
) -> WipeResult:
    """Open the demo file with normal Python file I/O and write wipe patterns.

    This mirrors the real WipeMethod.execute() flow but uses standard file
    operations instead of raw disk ctypes calls.
    """
    start_time = datetime.now()
    total_written = 0
    error_message: str | None = None
    success = True
    block_size = 1_048_576  # 1 MB

    file_size = os.path.getsize(path)
    audit_log(f"Demo wipe started: method={method.name}, passes={method.passes}, "
              f"file_size={file_size}, block_size={block_size}")

    try:
        with open(path, "r+b") as f:
            for pass_num in range(1, method.passes + 1):
                audit_log(f"Demo pass {pass_num}/{method.passes} started ({method.name})")
                pass_start = time.monotonic()

                f.seek(0)
                bytes_this_pass = 0
                remaining = file_size

                while remaining > 0:
                    chunk = min(block_size, remaining)
                    pattern = method.get_pattern(pass_num, chunk)
                    f.write(pattern)
                    written = len(pattern)
                    bytes_this_pass += written
                    total_written += written
                    remaining -= written

                    if progress_callback is not None:
                        elapsed = time.monotonic() - pass_start
                        speed = (bytes_this_pass / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
                        progress_callback(pass_num, method.passes, bytes_this_pass, file_size, speed)

                f.flush()
                os.fsync(f.fileno())

                audit_log(f"Demo pass {pass_num}/{method.passes} completed ({method.name}), "
                          f"bytes_written={bytes_this_pass}")

    except OSError as exc:
        success = False
        error_message = str(exc)
        audit_log(f"Demo wipe error: method={method.name}, error={exc}")

    end_time = datetime.now()
    audit_log(f"Demo wipe finished: method={method.name}, success={success}, "
              f"total_bytes_written={total_written}")

    return WipeResult(
        method_name=method.name,
        passes=method.passes,
        start_time=start_time,
        end_time=end_time,
        bytes_written=total_written,
        success=success,
        error_message=error_message,
    )


def verify_demo_file(
    path: str,
    expected_pattern: bytes,
    sample_count: int = 100,
) -> VerificationResult:
    """Read random positions in the demo file and check the wipe pattern.

    For random-data wipes (expected_pattern is empty), verify sectors are
    not all zeros. For deterministic patterns, verify exact match.
    """
    file_size = os.path.getsize(path)
    max_sector = (file_size // SECTOR_SIZE) - 1

    if max_sector < 1:
        return VerificationResult(
            success=False,
            method="sample",
            bytes_verified=file_size,
            expected_pattern="zeros",
            error_count=0,
            mismatch_offsets=[],
            duration_seconds=0.1,
            sectors_checked=0,
            sectors_matched=0,
            sample_hash="",
            timestamp=datetime.now(),
        )

    sample_count = min(sample_count, max_sector + 1)
    offsets = sorted(random.sample(range(max_sector + 1), sample_count))

    is_random_check = len(expected_pattern) == 0
    if not is_random_check:
        full_pattern = (expected_pattern * ((SECTOR_SIZE // len(expected_pattern)) + 1))[:SECTOR_SIZE]
    else:
        full_pattern = b""

    sectors_checked = 0
    sectors_matched = 0
    hasher = hashlib.sha256()

    with open(path, "rb") as f:
        for sector_index in offsets:
            byte_offset = sector_index * SECTOR_SIZE
            f.seek(byte_offset)
            data = f.read(SECTOR_SIZE)
            if len(data) != SECTOR_SIZE:
                continue

            sectors_checked += 1
            hasher.update(data)

            if is_random_check:
                if data != b"\x00" * SECTOR_SIZE:
                    sectors_matched += 1
            else:
                if data == full_pattern:
                    sectors_matched += 1

    passed = sectors_checked > 0 and sectors_matched == sectors_checked

    audit_log(f"Demo verification: passed={passed}, "
              f"checked={sectors_checked}, matched={sectors_matched}")

    return VerificationResult(
        success=passed,
        method="sample",
        bytes_verified=file_size,
        expected_pattern="zeros",
        error_count=sectors_checked - sectors_matched,
        mismatch_offsets=[],
        duration_seconds=0.1,
        sectors_checked=sectors_checked,
        sectors_matched=sectors_matched,
        sample_hash=hasher.hexdigest(),
        timestamp=datetime.now(),
    )
