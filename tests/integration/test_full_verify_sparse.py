"""Integration tests for full_verify against a real Windows file handle.

These exercise `wipe.verify.full_verify` end-to-end by opening a 1 GiB sparse
file with CreateFileW and invoking the production SetFilePointerEx/ReadFile
code path. A sparse file is used so the test stays fast and uses ~0 bytes of
disk: Windows returns zero-filled reads for unallocated regions.

Primary target: Windows (CreateFileW + ReadFile via kernel32). On Linux the
full_verify implementation uses the same Win32 APIs, so these tests are
skipped there. If Linux CI support is ever desired, full_verify would need a
POSIX read/pread backend — flag at that point; out of scope here.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="full_verify integration test requires Windows CreateFileW/ReadFile",
)

from wipe.verify import full_verify  # noqa: E402  (import after skipif)


# ── Win32 constants ──────────────────────────────────────────────────────

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = -1

ONE_GIB = 1_073_741_824
RANDOM_WRITE_OFFSET = 0x10000000  # 256 MiB in — block-aligned for block_size=1 MiB
RANDOM_WRITE_SIZE = 4096

_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None


# ── Helpers ──────────────────────────────────────────────────────────────

def _open_file_raw(path: str, write: bool = False) -> int:
    """Open a file with CreateFileW and return the Win32 HANDLE as int."""
    access = GENERIC_READ | (GENERIC_WRITE if write else 0)
    handle = _kernel32.CreateFileW(
        path,
        access,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    # CreateFileW returns a HANDLE (pointer-sized). ctypes yields a Python
    # int; INVALID_HANDLE_VALUE is -1 after sign-extension but can appear as
    # 0xFFFFFFFFFFFFFFFF on 64-bit. Normalise by checking both forms.
    if handle in (INVALID_HANDLE_VALUE, 0xFFFFFFFFFFFFFFFF, 0, None):
        err = ctypes.get_last_error()
        raise OSError(f"CreateFileW failed for {path}: error {err}")
    return handle


def _close(handle: int) -> None:
    if handle and handle not in (INVALID_HANDLE_VALUE, 0xFFFFFFFFFFFFFFFF):
        _kernel32.CloseHandle(handle)


def _make_sparse_file(path: str, size: int) -> None:
    """Create a sparse file of `size` bytes at `path`.

    Strategy: create the file, mark it sparse via `fsutil sparse setflag`,
    then set the logical end-of-file via seek+write-of-one-zero-byte. This
    avoids allocating real clusters for the whole range — a non-sparse
    1 GiB file would take 10+ seconds to zero-fill on a slow disk.
    """
    # 1. Create an empty file.
    with open(path, "wb"):
        pass

    # 2. Mark sparse (best effort). fsutil may be unavailable in some
    #    environments; seek-extend still produces zero-on-read content on
    #    NTFS, so the test remains correct either way.
    try:
        subprocess.run(
            ["fsutil", "sparse", "setflag", path],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 3. Extend logical length by seeking past EOF and writing one byte at
    #    size-1. On NTFS with the sparse flag set this does not allocate
    #    clusters for the intervening zero range.
    with open(path, "r+b") as f:
        f.seek(size - 1)
        f.write(b"\x00")
        f.flush()

    # Sanity check: logical size is what we asked for.
    actual = os.path.getsize(path)
    assert actual == size, f"sparse file size mismatch: expected {size}, got {actual}"


# ── Tests ────────────────────────────────────────────────────────────────

def test_full_verify_success_on_sparse_zero_file(tmp_path):
    """A 1 GiB sparse file reads back as all zeros → full_verify passes."""
    sparse = tmp_path / "sparse_zero.bin"
    _make_sparse_file(str(sparse), ONE_GIB)

    handle = _open_file_raw(str(sparse), write=False)
    try:
        result = full_verify(
            handle=handle,
            drive_size=ONE_GIB,
            expected_pattern=b"\x00",
        )
    finally:
        _close(handle)

    assert result.success is True, (
        f"full_verify should succeed on all-zero sparse file; "
        f"got error_count={result.error_count}, "
        f"first mismatches={result.mismatch_offsets[:5]}"
    )
    assert result.method == "full"
    assert result.error_count == 0
    assert result.bytes_verified == ONE_GIB
    assert result.mismatch_offsets == []
    assert result.expected_pattern == "zeros"
    assert result.duration_seconds >= 0.0


def test_full_verify_failure_after_random_write(tmp_path):
    """Writing random bytes at a known offset must be detected as a mismatch."""
    sparse = tmp_path / "sparse_tainted.bin"
    _make_sparse_file(str(sparse), ONE_GIB)

    # Taint the file: write 4 KiB of random data at exactly the 256-MiB mark,
    # which is the start of a 1 MiB full_verify block (default block_size =
    # 1 MiB, and 0x10000000 % 1_048_576 == 0). Force the first byte to be
    # non-zero so the reported mismatch offset is deterministically the
    # block start; os.urandom has a ~1/256 chance of starting with 0x00.
    random_bytes = os.urandom(RANDOM_WRITE_SIZE)
    if random_bytes[0] == 0:
        random_bytes = b"\x5A" + random_bytes[1:]
    with open(sparse, "r+b") as f:
        f.seek(RANDOM_WRITE_OFFSET)
        f.write(random_bytes)
        f.flush()
        os.fsync(f.fileno())

    handle = _open_file_raw(str(sparse), write=False)
    try:
        result = full_verify(
            handle=handle,
            drive_size=ONE_GIB,
            expected_pattern=b"\x00",
        )
    finally:
        _close(handle)

    assert result.success is False
    assert result.method == "full"
    assert result.error_count >= 1
    assert result.bytes_verified == ONE_GIB
    # Block is 1 MiB and RANDOM_WRITE_OFFSET is block-aligned; the first
    # differing byte within that block is byte 0 (non-zero by construction),
    # so the reported mismatch offset equals RANDOM_WRITE_OFFSET exactly.
    assert RANDOM_WRITE_OFFSET in result.mismatch_offsets, (
        f"expected offset {hex(RANDOM_WRITE_OFFSET)} in mismatch_offsets, "
        f"got {[hex(o) for o in result.mismatch_offsets]}"
    )
