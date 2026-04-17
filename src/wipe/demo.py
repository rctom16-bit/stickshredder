"""Demo mode: simulate wiping a virtual disk file without touching real hardware."""

from __future__ import annotations

import hashlib
import os
import random
import tempfile
import time
from datetime import datetime

from core.log import audit_log
from wipe.device import DeviceInfo
from wipe.methods import WipeMethod, WipeResult, ProgressCallback
from wipe.verify import VerifyResult, VerifyProgressCallback

SECTOR_SIZE = 512
DEFAULT_DEMO_SIZE = 10 * 1024 * 1024  # 10 MB


def _random_hex(n: int = 4) -> str:
    return os.urandom(n // 2 + 1).hex()[:n].upper()


def _pattern_label(expected: bytes) -> str:
    """Return a human-readable label for a tile pattern."""
    if not expected:
        return "non-zero (random)"
    if expected == b"\x00":
        return "zeros"
    if expected == b"\xFF":
        return "0xFF"
    return f"custom:0x{expected[0]:02X}"


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
    """Create a temporary file filled with random data to simulate a USB drive."""
    if not path:
        fd, path = tempfile.mkstemp(prefix="stickshredder_demo_", suffix=".bin")
        os.close(fd)

    audit_log(f"Demo: creating virtual disk file ({size_bytes} bytes) at {path}")

    block_size = 1_048_576
    remaining = size_bytes
    with open(path, "wb") as f:
        while remaining > 0:
            chunk = min(block_size, remaining)
            f.write(os.urandom(chunk))
            remaining -= chunk

    audit_log(f"Demo: virtual disk file created: {path}")
    return path


def _write_zero_pass(f, file_size: int, block_size: int,
                     progress_callback: ProgressCallback | None,
                     pass_num: int, total_passes: int) -> int:
    """Write zeros across the whole file. Returns bytes written."""
    f.seek(0)
    pass_start = time.monotonic()
    bytes_done = 0
    remaining = file_size
    zero_block = b"\x00" * block_size
    while remaining > 0:
        chunk = min(block_size, remaining)
        data = zero_block if chunk == block_size else b"\x00" * chunk
        f.write(data)
        bytes_done += chunk
        remaining -= chunk
        if progress_callback is not None:
            elapsed = time.monotonic() - pass_start
            speed = (bytes_done / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
            progress_callback(pass_num, total_passes, bytes_done, file_size, speed)
    return bytes_done


def wipe_demo_file(
    path: str,
    method: WipeMethod,
    progress_callback: ProgressCallback | None = None,
    verify_mode: str = "none",
    verify_progress_callback: VerifyProgressCallback | None = None,
) -> WipeResult:
    """Simulate a full wipe (+ optional verify) on a local file.

    Mirrors WipeMethod.execute() but uses Python file I/O instead of raw ctypes.
    When verify_mode != "none" and the method's final pass is random, appends
    a zero-blanking pass so verification has a deterministic target.
    """
    start_time = datetime.now()
    total_written = 0
    error_message: str | None = None
    success = True
    block_size = 1_048_576
    zero_blank_appended = False
    verify_result: VerifyResult | None = None

    file_size = os.path.getsize(path)

    final_pass_is_random = getattr(method, "final_pass_is_random", False)
    append_zero_blank = verify_mode != "none" and final_pass_is_random
    total_passes = method.passes + (1 if append_zero_blank else 0)

    audit_log(
        f"Demo wipe started: method={method.name}, passes={total_passes} "
        f"({method.passes} core + {1 if append_zero_blank else 0} blank), "
        f"file_size={file_size}, block_size={block_size}, verify_mode={verify_mode}"
    )

    try:
        with open(path, "r+b") as f:
            for pass_num in range(1, method.passes + 1):
                audit_log(f"Demo pass {pass_num}/{total_passes} started ({method.name})")
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
                        progress_callback(pass_num, total_passes, bytes_this_pass, file_size, speed)

                f.flush()
                os.fsync(f.fileno())
                audit_log(
                    f"Demo pass {pass_num}/{total_passes} completed ({method.name}), "
                    f"bytes_written={bytes_this_pass}"
                )

            if append_zero_blank:
                audit_log(
                    f"Zero-blanking pass appended for verification (method supports {verify_mode} verify)"
                )
                blank_pass = method.passes + 1
                audit_log(f"Demo pass {blank_pass}/{total_passes} started (zero-blank)")
                bytes_this_pass = _write_zero_pass(
                    f, file_size, block_size, progress_callback,
                    blank_pass, total_passes,
                )
                total_written += bytes_this_pass
                f.flush()
                os.fsync(f.fileno())
                zero_blank_appended = True
                audit_log(
                    f"Demo pass {blank_pass}/{total_passes} completed (zero-blank), "
                    f"bytes_written={bytes_this_pass}"
                )

    except OSError as exc:
        success = False
        error_message = str(exc)
        audit_log(f"Demo wipe error: method={method.name}, error={exc}")

    if success and verify_mode != "none":
        expected = _expected_final_pattern(method, append_zero_blank)
        try:
            if verify_mode == "sample":
                verify_result = _demo_sample_verify(path, expected)
            elif verify_mode == "full":
                verify_result = _demo_full_verify(
                    path, expected, block_size=block_size,
                    progress_callback=verify_progress_callback,
                )
        except Exception as exc:  # noqa: BLE001
            audit_log(f"Demo verification crashed: {exc}")
            verify_result = None

    end_time = datetime.now()
    audit_log(
        f"Demo wipe finished: method={method.name}, success={success}, "
        f"total_bytes_written={total_written}, verify_success="
        f"{verify_result.success if verify_result else None}"
    )

    return WipeResult(
        method_name=method.name,
        passes=total_passes,
        start_time=start_time,
        end_time=end_time,
        bytes_written=total_written,
        success=success,
        error_message=error_message,
        verify_result=verify_result,
        zero_blank_appended=zero_blank_appended,
    )


def _expected_final_pattern(method: WipeMethod, zero_blanked: bool) -> bytes:
    """Return the single-byte pattern expected on disk after the last write."""
    if zero_blanked:
        return b"\x00"
    if getattr(method, "final_pass_is_random", False):
        # No zero-blank was appended (verify=none) — treat as random check
        return b""
    last = method.get_pattern(method.passes, 4)
    return bytes([last[0]])


def _demo_sample_verify(path: str, expected_pattern: bytes,
                        sample_count: int = 100) -> VerifyResult:
    """Random-sector sample verify on a local file."""
    start = time.monotonic()
    file_size = os.path.getsize(path)
    max_sector = (file_size // SECTOR_SIZE) - 1
    pattern_label = _pattern_label(expected_pattern)

    if max_sector < 1:
        return VerifyResult(
            success=False, method="sample", bytes_verified=0,
            expected_pattern=pattern_label, error_count=0,
            mismatch_offsets=[], duration_seconds=time.monotonic() - start,
            sectors_checked=0, sectors_matched=0, sample_hash="",
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
            f.seek(sector_index * SECTOR_SIZE)
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
    duration = time.monotonic() - start

    audit_log(
        f"Demo sample verification: passed={passed}, "
        f"checked={sectors_checked}, matched={sectors_matched}, "
        f"pattern={pattern_label}"
    )

    return VerifyResult(
        success=passed, method="sample",
        bytes_verified=sectors_checked * SECTOR_SIZE,
        expected_pattern=pattern_label,
        error_count=sectors_checked - sectors_matched,
        mismatch_offsets=[], duration_seconds=duration,
        sectors_checked=sectors_checked,
        sectors_matched=sectors_matched,
        sample_hash=hasher.hexdigest(),
    )


def _demo_full_verify(path: str, expected_pattern: bytes,
                      block_size: int = 1_048_576,
                      progress_callback: VerifyProgressCallback | None = None
                      ) -> VerifyResult:
    """Block-by-block verify: read the entire file and compare every byte."""
    start = time.monotonic()
    file_size = os.path.getsize(path)
    pattern_label = _pattern_label(expected_pattern)
    is_random_check = len(expected_pattern) == 0

    mismatch_offsets: list[int] = []
    error_count = 0
    bytes_verified = 0
    last_progress_bytes = 0
    progress_interval = 50 * 1024 * 1024  # 50 MB

    def _build_expected_block(size: int) -> bytes:
        if is_random_check:
            return b""
        return (expected_pattern * ((size // len(expected_pattern)) + 1))[:size]

    # Pre-allocate the full-size zero block for is_random_check comparison.
    zero_full = b"\x00" * block_size if is_random_check else b""
    # Pre-build the full-size expected block once for fixed-pattern verify.
    _expected_full = _build_expected_block(block_size) if not is_random_check else b""

    with open(path, "rb") as f:
        offset = 0
        while offset < file_size:
            chunk = min(block_size, file_size - offset)
            f.seek(offset)
            data = f.read(chunk)
            if len(data) != chunk:
                error_count += 1
                if len(mismatch_offsets) < 100:
                    mismatch_offsets.append(offset)
                audit_log(f"Demo full verify: short read at offset {offset}")
                offset += chunk
                bytes_verified += chunk
                continue

            if is_random_check:
                zero_block = zero_full if chunk == block_size else zero_full[:chunk]
                if data == zero_block:
                    error_count += 1
                    if len(mismatch_offsets) < 100:
                        mismatch_offsets.append(offset)
            else:
                expected_block = _expected_full if chunk == block_size else _build_expected_block(chunk)
                if data != expected_block:
                    error_count += 1
                    for i, (got, exp) in enumerate(zip(data, expected_block)):
                        if got != exp:
                            if len(mismatch_offsets) < 100:
                                mismatch_offsets.append(offset + i)
                            break

            offset += chunk
            bytes_verified += chunk

            if progress_callback is not None:
                if bytes_verified - last_progress_bytes >= progress_interval:
                    elapsed = time.monotonic() - start
                    speed = (bytes_verified / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
                    fraction = bytes_verified / file_size if file_size else 1.0
                    progress_callback(fraction, bytes_verified, file_size, speed)
                    last_progress_bytes = bytes_verified

    if progress_callback is not None and file_size > 0 and last_progress_bytes < file_size:
        elapsed = time.monotonic() - start
        speed = (bytes_verified / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
        progress_callback(1.0, bytes_verified, file_size, speed)

    duration = time.monotonic() - start
    success = error_count == 0
    sectors_checked = bytes_verified // SECTOR_SIZE

    audit_log(
        f"Demo full verification: success={success}, "
        f"bytes_verified={bytes_verified}, error_count={error_count}, "
        f"pattern={pattern_label}, duration={duration:.2f}s"
    )

    return VerifyResult(
        success=success, method="full", bytes_verified=bytes_verified,
        expected_pattern=pattern_label, error_count=error_count,
        mismatch_offsets=mismatch_offsets, duration_seconds=duration,
        sectors_checked=sectors_checked,
    )


def verify_demo_file(
    path: str,
    expected_pattern: bytes,
    sample_count: int = 100,
) -> VerifyResult:
    """Legacy wrapper — always sample-verifies. Prefer wipe_demo_file(verify_mode=...) instead."""
    return _demo_sample_verify(path, expected_pattern, sample_count)
