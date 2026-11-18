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
     variant when the drive offers it). The command may take up to two hours,
     or twice the drive's own estimate if that is longer.
  3. Remove the partition table (``device.clear_partition_table``), then one
     pass of zeros plus a full verification — always, whatever the
     caller's usual verification: that makes the result checkable byte by
     byte, and the drive's own report alone never makes a wipe "ok".

The caller still owns the safety gate (``prepare_target``), the open handle,
the volume locks and closing everything in ``finally``. ``fits`` is the rule
for which disks get the offer at all (WMI facts only, no drive command).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from core.log import audit_log
from wipe import secure_erase
from wipe.device import clear_partition_table
from wipe.methods import WipeMethod, WipeResult

# The ATA user password set for the erase. Shown to the user and logged
# before it is set: should an erase stop half-way, the drive can be unlocked
# with it (``secure_erase.unlock_drive``).
TEMP_PASSWORD = secure_erase.DEFAULT_PASSWORD

READY_METHODS = ("ata-enhanced", "ata-secure-erase")

# Connections whose disks may reach the drive's own erase: wipe.secure_erase
# only works on the "ata" path (SATA on Microsoft's storahci driver). USB
# bridges (SAT) are read-only by design and NVMe has no erase path yet.
HARDWARE_CONNECTIONS = ("SATA", "ATA")

# The ERASE UNIT command may run this long, or twice the drive's estimate.
MIN_ERASE_TIMEOUT_SECONDS = 7200

# SetThreadExecutionState flags (winbase.h).
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@dataclass(frozen=True)
class Refusal:
    reason: str      # no_path | unsupported | frozen | locked | has_password | error
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


def fits(device, media: str) -> bool:
    """Whether ``device`` may get the hardware erase: a non-system SSD on SATA.

    WMI facts only. The drive itself is asked later, after the safety gate.
    """
    return (
        not device.is_system_drive
        and media == "ssd"
        and (device.connection_type or "").upper() in HARDWARE_CONNECTIONS
    )


def check_ready(status: secure_erase.SecureEraseResult) -> Refusal | None:
    """Why the drive's own erase must not start, or ``None`` if it may."""
    if status.method in READY_METHODS and status.supported and not status.frozen:
        return None
    if status.frozen or status.method == "frozen":
        return Refusal("frozen", "the drive is SECURITY FROZEN (locked by the PC's firmware)")
    if status.method == "locked":
        return Refusal("locked", "the drive is locked with an ATA password")
    if status.method == "has_password":
        return Refusal("has_password",
                       "the drive already has an ATA password (set before this erase)")
    if status.method == "error":
        return Refusal("error", status.error or "the drive could not be asked")
    if status.error:  # wipe.transport found no drive-command path for this disk
        return Refusal("no_path", status.error)
    return Refusal("unsupported", "the drive does not offer the ATA security erase")


def erase_label(method: str) -> str:
    """Certificate name of the drive's erase variant."""
    return "ATA Secure Erase (enhanced)" if method == "ata-enhanced" else "ATA Secure Erase"


def erase_timeout_seconds(estimated_minutes: int) -> int:
    """Two hours, or twice the drive's own estimate if that is longer."""
    return max(MIN_ERASE_TIMEOUT_SECONDS, 2 * 60 * int(estimated_minutes or 0))


def _log(message: str) -> None:
    """Audit log that never changes the outcome (disk full, no rights)."""
    try:
        audit_log(message)
    except Exception:  # noqa: BLE001
        pass


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


def _report(report: EraseReport, on_erase_finished) -> EraseReport:
    """Tell the caller first, then log (a broken log must not hide the outcome)."""
    if on_erase_finished is not None:
        on_erase_finished(report)
    _log(f"Hardware erase: {report.status} ({report.reason or report.method}) "
         f"{report.detail}".rstrip())
    return report


def run(
    handle: int,
    drive_size: int,
    overwrite: WipeMethod,
    *,
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
        report = _report(EraseReport("refused", refusal.reason, detail=refusal.detail),
                         on_erase_finished)
        return HardwareEraseOutcome(report)

    enhanced = status.method == "ata-enhanced"
    minutes = int(status.estimated_minutes or 0)
    _log(
        f"Hardware erase: temporary ATA user password {TEMP_PASSWORD!r} — if the drive "
        f"stays locked after an interrupted erase, unlock it with this password"
    )
    if on_erase_started is not None:
        on_erase_started(status.method, minutes)

    erase: secure_erase.SecureEraseResult | None = None
    _set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        erase = secure_erase.secure_erase(
            handle, enhanced=enhanced, password=TEMP_PASSWORD,
            timeout_seconds=erase_timeout_seconds(minutes),
        )
        error = erase.error
    except Exception as exc:  # noqa: BLE001 — secure_erase should never raise
        error = str(exc) or type(exc).__name__
    finally:
        _set_execution_state(ES_CONTINUOUS)

    if erase is not None and erase.success:
        report = EraseReport("erased", method=erase.method,
                             duration_seconds=erase.duration_seconds,
                             estimated_minutes=minutes)
    elif erase is not None and erase.method in ("unsupported", "frozen", "locked",
                                                "has_password"):
        # The drive refused at the last re-check: no erase command was sent.
        refused = check_ready(erase)
        report = EraseReport("refused", refused.reason, detail=refused.detail)
    else:
        report = EraseReport(
            "failed", "error", method=status.method,
            detail=error or "the drive did not confirm the erase",
            # From the drive (secure_erase removes the password again when it
            # can); unknown after an unexpected exception.
            password_may_be_set=erase.password_set if erase is not None else True,
            duration_seconds=erase.duration_seconds if erase is not None else 0.0,
        )
    _report(report, on_erase_finished)
    if report.status != "erased":
        return HardwareEraseOutcome(report, erase)

    # Windows may still hold the old layout (a read-only partition blocks the
    # zeros): remove it now, never before the drive has said yes. A failure
    # is logged and the zeros run anyway.
    clear_partition_table(handle)
    wipe = overwrite.execute(
        handle=handle,
        drive_size=drive_size,
        progress_callback=progress_callback,
        verify_mode="full",
        verify_progress_callback=verify_progress_callback,
    )
    label = f"{erase_label(erase.method)} + {overwrite.name}"
    return HardwareEraseOutcome(report, erase, _combined(label, started, wipe))
