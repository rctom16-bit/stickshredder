"""Secure wipe methods for raw disk handles."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from core.log import audit_log

kernel32 = ctypes.windll.kernel32

LARGE_INTEGER = ctypes.c_int64
FILE_BEGIN = 0


@dataclass
class WipeResult:
    method_name: str
    passes: int
    start_time: datetime
    end_time: datetime
    bytes_written: int
    success: bool
    error_message: str | None


ProgressCallback = Callable[[int, int, int, int, float], None]


def _set_file_pointer(handle: int, position: int) -> bool:
    new_pos = LARGE_INTEGER(0)
    return bool(kernel32.SetFilePointerEx(
        ctypes.wintypes.HANDLE(handle),
        LARGE_INTEGER(position),
        ctypes.byref(new_pos),
        FILE_BEGIN,
    ))


def _write_block(handle: int, data: bytes) -> int:
    written = ctypes.wintypes.DWORD(0)
    success = kernel32.WriteFile(
        ctypes.wintypes.HANDLE(handle),
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

    @abstractmethod
    def get_pattern(self, pass_number: int, block_size: int) -> bytes:
        ...

    def execute(
        self,
        handle: int,
        drive_size: int,
        block_size: int = 1_048_576,
        progress_callback: ProgressCallback | None = None,
    ) -> WipeResult:
        start_time = datetime.now()
        total_written = 0
        error_message: str | None = None
        success = True

        audit_log(f"Wipe started: method={self.name}, passes={self.passes}, "
                  f"drive_size={drive_size}, block_size={block_size}")

        try:
            for pass_num in range(1, self.passes + 1):
                audit_log(f"Pass {pass_num}/{self.passes} started ({self.name})")
                pass_start = time.monotonic()

                if not _set_file_pointer(handle, 0):
                    raise OSError("SetFilePointerEx failed: could not seek to start")

                bytes_this_pass = 0
                remaining = drive_size

                while remaining > 0:
                    chunk = min(block_size, remaining)
                    pattern = self.get_pattern(pass_num, chunk)
                    written = _write_block(handle, pattern)
                    bytes_this_pass += written
                    total_written += written
                    remaining -= written

                    if progress_callback is not None:
                        elapsed = time.monotonic() - pass_start
                        speed = (bytes_this_pass / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0
                        progress_callback(pass_num, self.passes, bytes_this_pass, drive_size, speed)

                audit_log(f"Pass {pass_num}/{self.passes} completed ({self.name}), "
                          f"bytes_written={bytes_this_pass}")

            kernel32.FlushFileBuffers(ctypes.wintypes.HANDLE(handle))

        except OSError as exc:
            success = False
            error_message = str(exc)
            audit_log(f"Wipe error: method={self.name}, pass={pass_num}, error={exc}")

        end_time = datetime.now()
        audit_log(f"Wipe finished: method={self.name}, success={success}, "
                  f"total_bytes_written={total_written}")

        return WipeResult(
            method_name=self.name,
            passes=self.passes,
            start_time=start_time,
            end_time=end_time,
            bytes_written=total_written,
            success=success,
            error_message=error_message,
        )


class ZeroFill(WipeMethod):
    name = "ZeroFill"
    passes = 1
    sicherheitsstufe = "1-2"
    description_de = "Einfaches Überschreiben mit Nullen (DIN 66399 Sicherheitsstufe 1-2)"
    description_en = "Single pass zero fill (DIN 66399 security level 1-2)"

    def get_pattern(self, pass_number: int, block_size: int) -> bytes:
        return b"\x00" * block_size


class RandomThreePass(WipeMethod):
    name = "RandomThreePass"
    passes = 3
    sicherheitsstufe = "3"
    description_de = "Drei Durchgänge mit kryptographisch zufälligen Daten (Sicherheitsstufe 3)"
    description_en = "Three passes of cryptographically random data (security level 3)"

    def get_pattern(self, pass_number: int, block_size: int) -> bytes:
        return os.urandom(block_size)


class BsiVsitr(WipeMethod):
    name = "BSI-VSITR"
    passes = 7
    sicherheitsstufe = "4+"
    description_de = "BSI VSITR 7-Durchgänge-Verfahren (Sicherheitsstufe 4+)"
    description_en = "BSI VSITR 7-pass method (security level 4+)"

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
