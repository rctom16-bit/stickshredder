"""Secure wipe methods for raw disk handles."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from core.log import audit_log
from wipe.verify import (
    VerifyProgressCallback,
    full_verify,
    sample_verify,
)

if TYPE_CHECKING:
    # Imported for type hints only so the runtime import surface is minimal.
    from wipe.verify import VerifyResult
    from wipe.format import FormatResult

# Load kernel32 with last-error tracking enabled so `ctypes.get_last_error()`
# reliably reports the Win32 error code after a failing API call.
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LARGE_INTEGER = ctypes.c_int64
FILE_BEGIN = 0

# Fire progress callbacks at most every 50 MB of written data so we do not
# drown Qt's signal bus on a 1 MB block size (which would otherwise yield
# 64k callbacks per gigabyte).
PROGRESS_INTERVAL = 50 * 1024 * 1024


# ─────────────────────────────────────────────────────────────────────
# ctypes prototypes
# ─────────────────────────────────────────────────────────────────────

kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
kernel32.WriteFile.restype = wintypes.BOOL

kernel32.SetFilePointerEx.argtypes = [
    wintypes.HANDLE, ctypes.c_int64, ctypes.POINTER(ctypes.c_int64), wintypes.DWORD,
]
kernel32.SetFilePointerEx.restype = wintypes.BOOL

kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
kernel32.FlushFileBuffers.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


@dataclass
class WipeResult:
    method_name: str
    passes: int  # total passes ACTUALLY run, including zero-blanking if appended
    start_time: datetime
    end_time: datetime
    bytes_written: int
    success: bool
    error_message: str | None
    verify_result: "VerifyResult | None" = None  # populated when verify_mode != "none"
    zero_blank_appended: bool = False  # true if a zero-blanking pass was added for verifiability
    format_result: "FormatResult | None" = None  # populated when reformat requested


ProgressCallback = Callable[[int, int, int, int, float], None]


def _set_file_pointer(handle: int, position: int) -> bool:
    new_pos = LARGE_INTEGER(0)
    return bool(kernel32.SetFilePointerEx(
        wintypes.HANDLE(handle),
        LARGE_INTEGER(position),
        ctypes.byref(new_pos),
        FILE_BEGIN,
    ))


def _write_block(handle: int, data: bytes) -> int:
    written = wintypes.DWORD(0)
    success = kernel32.WriteFile(
        wintypes.HANDLE(handle),
        data,
        len(data),
        ctypes.byref(written),
        None,
    )
    if not success:
        err = ctypes.get_last_error() or kernel32.GetLastError()
        raise OSError(f"WriteFile failed with error code {err}")
    return written.value


class WipeMethod(ABC):
    name: str
    passes: int
    sicherheitsstufe: str
    description_de: str
    description_en: str
    # Whether this method's final pass writes non-deterministic (random) data.
    # When True and verify_mode != "none", a zero-blanking pass is appended so
    # the post-wipe verifier has a deterministic expected pattern.
    final_pass_is_random: bool = False

    @abstractmethod
    def get_pattern(self, pass_number: int, block_size: int) -> bytes:
        ...

    def _expected_final_pattern(self) -> bytes:
        """Return the single-byte pattern expected on disk after the last pass.

        If the method's final pass is random, returns b"\\x00" (assuming a
        zero-blanking pass was appended). Otherwise inspects the last pass's
        pattern and returns its first byte as a single-byte expected value.
        """
        if self.final_pass_is_random:
            return b"\x00"
        last = self.get_pattern(self.passes, 4)
        return bytes([last[0]])

    def _run_single_pass(
        self,
        handle: int,
        drive_size: int,
        block_size: int,
        pass_num: int,
        total_passes: int,
        pattern_factory: Callable[[int, int], bytes],
        progress_callback: ProgressCallback | None,
    ) -> int:
        """Run a single overwrite pass over the whole drive. Returns bytes written."""
        if not _set_file_pointer(handle, 0):
            raise OSError("SetFilePointerEx failed: could not seek to start")

        bytes_this_pass = 0
        remaining = drive_size
        pass_start = time.monotonic()
        last_progress_bytes = 0

        while remaining > 0:
            chunk = min(block_size, remaining)
            pattern = pattern_factory(pass_num, chunk)
            written = _write_block(handle, pattern)
            bytes_this_pass += written
            remaining -= written

            if progress_callback is not None:
                # Fire every PROGRESS_INTERVAL bytes, and always at end-of-pass.
                if (bytes_this_pass - last_progress_bytes >= PROGRESS_INTERVAL
                        or bytes_this_pass == drive_size):
                    elapsed = time.monotonic() - pass_start
                    speed = (
                        (bytes_this_pass / (1024 * 1024)) / elapsed
                        if elapsed > 0 else 0.0
                    )
                    progress_callback(
                        pass_num, total_passes, bytes_this_pass, drive_size, speed,
                    )
                    last_progress_bytes = bytes_this_pass

        return bytes_this_pass

    def execute(
        self,
        handle: int,
        drive_size: int,
        block_size: int = 1_048_576,
        progress_callback: ProgressCallback | None = None,
        verify_mode: str = "none",  # "none" | "sample" | "full"
        verify_progress_callback: VerifyProgressCallback | None = None,
    ) -> WipeResult:
        start_time = datetime.now()
        total_written = 0
        error_message: str | None = None
        success = True
        pass_num = 0  # track for error reporting

        # Decide up front whether a zero-blanking pass needs to be appended so
        # post-wipe verification has a deterministic expected pattern.
        append_zero_blank = verify_mode != "none" and self.final_pass_is_random
        total_passes = self.passes + (1 if append_zero_blank else 0)

        audit_log(f"Wipe started: method={self.name}, passes={self.passes}, "
                  f"drive_size={drive_size}, block_size={block_size}, "
                  f"verify_mode={verify_mode}")

        if append_zero_blank:
            audit_log(
                f"Zero-blanking pass appended for verification "
                f"(method supports {verify_mode} verify)"
            )

        try:
            # Normal overwrite passes 1..N
            for pass_num in range(1, self.passes + 1):
                audit_log(f"Pass {pass_num}/{total_passes} started ({self.name})")
                bytes_this_pass = self._run_single_pass(
                    handle=handle,
                    drive_size=drive_size,
                    block_size=block_size,
                    pass_num=pass_num,
                    total_passes=total_passes,
                    pattern_factory=self.get_pattern,
                    progress_callback=progress_callback,
                )
                total_written += bytes_this_pass
                audit_log(f"Pass {pass_num}/{total_passes} completed ({self.name}), "
                          f"bytes_written={bytes_this_pass}")

            # Optional zero-blanking pass (always the LAST pass when appended)
            if append_zero_blank:
                pass_num = self.passes + 1
                audit_log(
                    f"Pass {pass_num}/{total_passes} started "
                    f"({self.name}, zero-blanking)"
                )
                try:
                    bytes_this_pass = self._run_single_pass(
                        handle=handle,
                        drive_size=drive_size,
                        block_size=block_size,
                        pass_num=pass_num,
                        total_passes=total_passes,
                        pattern_factory=lambda _pn, size: b"\x00" * size,
                        progress_callback=progress_callback,
                    )
                except OSError as exc:
                    # Let cancel bubble up untouched; anything else is a real
                    # zero-blank failure that should fail the wipe.
                    if isinstance(exc, InterruptedError):
                        raise
                    success = False
                    error_message = str(exc)
                    audit_log(
                        f"Wipe error (zero-blank pass): method={self.name}, "
                        f"pass={pass_num}, error={exc}"
                    )
                else:
                    total_written += bytes_this_pass
                    audit_log(
                        f"Pass {pass_num}/{total_passes} completed "
                        f"({self.name}, zero-blanking), bytes_written={bytes_this_pass}"
                    )

            if not kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
                err = ctypes.get_last_error()
                audit_log(f"FlushFileBuffers failed: error {err}")
                # Don't raise — flush failure at end of wipe is informational,
                # but we log it so certificate reviewers can see it.

        except OSError as exc:
            # InterruptedError subclasses OSError on Python 3.3+; when the
            # progress callback raises one for cancellation we must propagate
            # it so the GUI worker can treat it as cancel, not wipe failure.
            if isinstance(exc, InterruptedError):
                raise
            success = False
            error_message = str(exc)
            audit_log(f"Wipe error: method={self.name}, pass={pass_num}, error={exc}")

        end_time = datetime.now()
        audit_log(f"Wipe finished: method={self.name}, success={success}, "
                  f"total_bytes_written={total_written}")

        result = WipeResult(
            method_name=self.name,
            passes=total_passes,
            start_time=start_time,
            end_time=end_time,
            bytes_written=total_written,
            success=success,
            error_message=error_message,
            verify_result=None,
            zero_blank_appended=append_zero_blank,
        )

        # Post-wipe verification — only if wipe succeeded and verify was requested.
        if verify_mode != "none" and success:
            expected = self._expected_final_pattern()
            audit_log(
                f"Verification started: mode={verify_mode}, "
                f"expected_pattern=0x{expected.hex()}"
            )
            try:
                if verify_mode == "sample":
                    result.verify_result = sample_verify(
                        handle, drive_size, expected
                    )
                elif verify_mode == "full":
                    result.verify_result = full_verify(
                        handle,
                        drive_size,
                        expected,
                        progress_callback=verify_progress_callback,
                    )
                else:
                    audit_log(f"Unknown verify_mode: {verify_mode!r} — skipping verification")
            except Exception as exc:  # noqa: BLE001 — verification must never crash the wipe
                audit_log(f"Verification raised an exception: {exc}")
                result.verify_result = None
            else:
                vr = result.verify_result
                if vr is not None:
                    vr_ok = getattr(vr, "success", None)
                    if vr_ok is False:
                        audit_log(
                            f"Verification FAILED: method={self.name}, "
                            f"errors={getattr(vr, 'error_count', '?')}"
                        )
                    else:
                        audit_log(f"Verification passed: method={self.name}")

        return result


class ZeroFill(WipeMethod):
    name = "ZeroFill"
    passes = 1
    sicherheitsstufe = "1-2"
    description_de = "Einfaches Überschreiben mit Nullen (DIN 66399 Sicherheitsstufe 1-2)"
    description_en = "Single pass zero fill (DIN 66399 security level 1-2)"
    final_pass_is_random = False

    def get_pattern(self, pass_number: int, block_size: int) -> bytes:
        return b"\x00" * block_size


class RandomThreePass(WipeMethod):
    name = "RandomThreePass"
    passes = 3
    sicherheitsstufe = "3"
    description_de = "Drei Durchgänge mit kryptographisch zufälligen Daten (Sicherheitsstufe 3)"
    description_en = "Three passes of cryptographically random data (security level 3)"
    final_pass_is_random = True

    def get_pattern(self, pass_number: int, block_size: int) -> bytes:
        return os.urandom(block_size)


class BsiVsitr(WipeMethod):
    name = "BSI-VSITR"
    passes = 7
    sicherheitsstufe = "4+"
    description_de = "BSI VSITR 7-Durchgänge-Verfahren (Sicherheitsstufe 4+)"
    description_en = "BSI VSITR 7-pass method (security level 4+)"
    final_pass_is_random = True  # pass 7 is random

    _PASS_BYTES = {
        1: b"\x00",
        2: b"\xFF",
        3: b"\x00",
        4: b"\xFF",
        5: b"\x00",
        6: b"\xFF",
    }

    def get_pattern(self, pass_number: int, block_size: int) -> bytes:
        if pass_number == 7:
            return os.urandom(block_size)
        fill = self._PASS_BYTES[pass_number]
        return fill * block_size


class CustomWipe(WipeMethod):
    sicherheitsstufe = "custom"
    description_de = "Benutzerdefinierte Löschmethode"
    description_en = "User-defined wipe method"

    def __init__(
        self,
        passes: int,
        pattern: str = "zero",
        custom_byte: int | None = None,
    ) -> None:
        self.passes = passes
        self._pattern = pattern
        self._custom_byte = custom_byte
        self.name = f"Custom({passes}x,{pattern})"
        # Random-pattern customs need zero-blanking for verifiability.
        self.final_pass_is_random = (pattern == "random")

    def get_pattern(self, pass_number: int, block_size: int) -> bytes:
        match self._pattern:
            case "zero":
                return b"\x00" * block_size
            case "ones":
                return b"\xFF" * block_size
            case "random":
                return os.urandom(block_size)
            case "custom" if self._custom_byte is not None:
                return bytes([self._custom_byte]) * block_size
            case _:
                raise ValueError(f"Unknown pattern: {self._pattern}")
