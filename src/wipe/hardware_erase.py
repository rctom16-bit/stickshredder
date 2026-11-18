"""Hardware erase (Beta): the drive erases itself, then one pass of zeros + verify.

The one procedure behind ``stickshredder wipe --method secure-erase`` and the
GUI's „Hardware-Löschung (Beta)“, so both take the same steps and write the
same certificate fields (spec G2):

  1. Ask the drive whether its own erase is ready
     (``secure_erase.secure_erase_supported`` — IDENTIFY only, never erases).
     Not ready (no drive-command path, no security feature set, frozen,
     locked, probe failed) → stop here. Nothing has been written.
  2. Write the temporary drive password into the audit log, keep the PC from
     going to sleep, and let the drive run its ATA Secure Erase (the enhanced
     variant when the drive offers it).
  3. One pass of zeros plus the chosen verification. That makes the result
     checkable byte by byte; the drive's own report alone never makes a wipe
     "ok" (``cert.payload.result_status``).

The caller still owns the safety gate (``prepare_target``), the open handle,
the volume locks and closing everything in ``finally``.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from core.log import audit_log
from wipe import secure_erase
from wipe.methods import WipeMethod, WipeResult

# The ATA user password set for the erase (the default of secure_erase()).
# Shown to the user and logged before it is set: should an erase stop
# half-way, the drive can be unlocked with it.
TEMP_PASSWORD = "StickShredder"

READY_METHODS = ("ata-enhanced", "ata-secure-erase")

# SetThreadExecutionState flags (winbase.h).
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@dataclass(frozen=True)
class Refusal:
    reason: str      # no_path | unsupported | frozen | locked | error
    detail: str      # English, for the audit log and the CLI


@dataclass(frozen=True)
class EraseReport:
    """What happened to the drive's own erase (shown in the GUI and the CLI)."""

    status: str                      # erased | refused | failed
    reason: str = ""                 # refused: see Refusal; failed: "error"
    method: str = ""                 # ata-enhanced | ata-secure-erase once it ran
    detail: str = ""                 # English detail
    password_may_be_set: bool = False
    duration_seconds: float = 0.0    # how long the drive took to erase itself
    estimated_minutes: int = 0       # the drive's own estimate (0 = none given)


@dataclass
class HardwareEraseOutcome:
    report: EraseReport
    erase: secure_erase.SecureEraseResult | None = None
    # Erase + zeros + verification as one wipe, once the erase succeeded.
    wipe_result: WipeResult | None = None


def check_ready(status: secure_erase.SecureEraseResult) -> Refusal | None:
    """Why the drive's own erase must not start, or ``None`` if it may."""
    if status.method in READY_METHODS and status.supported and not status.frozen:
        return None
    if status.frozen or status.method == "frozen":
        return Refusal("frozen", "the drive is SECURITY FROZEN (locked by the PC's firmware)")
    if status.method == "locked":
        return Refusal("locked", "the drive is locked with an ATA password")
    if status.method == "error":
        return Refusal("error", status.error or "the drive could not be asked")
    if status.error:  # wipe.transport found no drive-command path for this disk
        return Refusal("no_path", status.error)
    return Refusal("unsupported", "the drive does not offer the ATA security erase")


def erase_label(method: str) -> str:
    """Certificate name of the drive's erase variant."""
    return "ATA Secure Erase (enhanced)" if method == "ata-enhanced" else "ATA Secure Erase"


def _set_execution_state(flags: int) -> None:
    """Ask Windows not to sleep (best effort; never fails the erase)."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — not on Windows, or refused
        pass


def _combined(label: str, started: datetime, wipe) -> WipeResult:
    """The overwrite's result, named and timed as the whole procedure."""
    return WipeResult(
        method_name=label,
        passes=int(wipe.passes),
        start_time=started,
        end_time=wipe.end_time,
        bytes_written=int(getattr(wipe, "bytes_written", 0) or 0),
        success=bool(wipe.success),
        error_message=getattr(wipe, "error_message", None),
        verify_result=getattr(wipe, "verify_result", None),
        zero_blank_appended=bool(getattr(wipe, "zero_blank_appended", False)),
        bad_sector_count=int(getattr(wipe, "bad_sector_count", 0) or 0),
        bad_sector_bytes=int(getattr(wipe, "bad_sector_bytes", 0) or 0),
        bad_sector_offsets=list(getattr(wipe, "bad_sector_offsets", None) or []),
    )


def run(
    handle: int,
    drive_size: int,
    overwrite: WipeMethod,
    *,
    verify_mode: str = "full",
    progress_callback: Callable | None = None,
    verify_progress_callback: Callable | None = None,
    on_erase_started: Callable[[str, int], None] | None = None,
    on_erase_finished: Callable[[EraseReport], None] | None = None,
) -> HardwareEraseOutcome:
    """Run the hardware erase on an open, gated handle (see the module doc)."""
    started = datetime.now()
    status = secure_erase.secure_erase_supported(handle)
    refusal = check_ready(status)
    if refusal is not None:
        audit_log(f"Hardware erase refused before any write: {refusal.reason} ({refusal.detail})")
        report = EraseReport("refused", refusal.reason, detail=refusal.detail)
        if on_erase_finished is not None:
            on_erase_finished(report)
        return HardwareEraseOutcome(report)

    enhanced = status.method == "ata-enhanced"
    audit_log(
        f"Hardware erase: temporary ATA user password {TEMP_PASSWORD!r} — if the drive "
        f"stays locked after an interrupted erase, unlock it with this password"
    )
    if on_erase_started is not None:
        on_erase_started(status.method, int(status.estimated_minutes or 0))

    erase: secure_erase.SecureEraseResult | None = None
    _set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        erase = secure_erase.secure_erase(handle, enhanced=enhanced, password=TEMP_PASSWORD)
        error = erase.error
    except Exception as exc:  # noqa: BLE001 — secure_erase should never raise
        error = str(exc) or type(exc).__name__
    finally:
        _set_execution_state(ES_CONTINUOUS)

    if erase is not None and erase.success:
        report = EraseReport("erased", method=erase.method,
                             duration_seconds=erase.duration_seconds,
                             estimated_minutes=int(status.estimated_minutes or 0))
    elif erase is not None and erase.method in ("unsupported", "frozen", "locked"):
        # The drive refused at the last re-check: no erase command was sent.
        refused = check_ready(erase)
        report = EraseReport("refused", refused.reason, detail=refused.detail)
    else:
        report = EraseReport(
            "failed", "error", method=status.method,
            detail=error or "the drive did not confirm the erase",
            password_may_be_set=True,
            duration_seconds=erase.duration_seconds if erase is not None else 0.0,
        )
    audit_log(f"Hardware erase: {report.status} ({report.reason or report.method}) "
              f"{report.detail}".rstrip())
    if on_erase_finished is not None:
        on_erase_finished(report)
    if report.status != "erased":
        return HardwareEraseOutcome(report, erase)

    wipe = overwrite.execute(
        handle=handle,
        drive_size=drive_size,
        progress_callback=progress_callback,
        verify_mode=verify_mode,
        verify_progress_callback=verify_progress_callback,
    )
    label = f"{erase_label(erase.method)} + {overwrite.name}"
    return HardwareEraseOutcome(report, erase, _combined(label, started, wipe))
