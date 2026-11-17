"""ATA Secure Erase — EXPERIMENTAL firmware-level erase for SATA/flash drives.

Instead of overwriting every sector, this asks the drive to run its *own*
built-in erase command (the ATA SECURITY feature set). On supporting hardware
this is far faster and reaches sectors a host overwrite cannot (wear-levelled
flash, reallocated sectors).

⚠️  EXPERIMENTAL — UNTESTED ON REAL HARDWARE.  A wrong or interrupted command
can leave a drive locked or unusable. This module is therefore deliberately
*safe in itself*:

  * it NEVER issues an erase command on a drive that is frozen, locked, or that
    does not advertise the SECURITY feature set;
  * it NEVER raises — every public function returns a typed result, errors and
    all, so the CLI/GUI caller can degrade gracefully.

The dangerous part (deciding to actually erase) is gated by the caller behind an
explicit "experimental" opt-in. This module only carries out a request that has
already cleared that gate, and only after re-checking the drive is in a safe
state.

NVMe (Format NVM / sanitize) is out of scope here — this module is ATA/SATA only.

Built on top of ``wipe.passthrough`` (the raw ATA pass-through layer).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from core.log import audit_log

# Reference the pass-through module by object (not `from ... import name`) so the
# functions are resolved at call time. This keeps the public seam patchable as
# ``wipe.passthrough.ata_identify`` / ``wipe.passthrough.send_ata_command`` in
# tests, and means the real implementation can be swapped in without touching
# this file.
from wipe import passthrough

# ── ATA SECURITY feature-set command codes ───────────────────────────────────
ATA_SECURITY_SET_PASSWORD = 0xF1
ATA_SECURITY_ERASE_PREPARE = 0xF3
ATA_SECURITY_ERASE_UNIT = 0xF4

SECURITY_BLOCK_SIZE = 512   # the SET PASSWORD / ERASE UNIT data block is 1 sector
PASSWORD_FIELD_BYTES = 32   # ATA password field: 32 bytes (words 1..16)

# Word 0 bits of the ERASE UNIT block.
ERASE_BIT_ENHANCED = 0x0002  # bit 1 — enhanced erase (else normal erase)
# Word 0 bit 0 of the SET PASSWORD block selects master(1)/user(0) password.
# We always set a user password, so control word 0 == 0.


@dataclass
class SecureEraseResult:
    success: bool
    method: str         # "ata-secure-erase" | "ata-enhanced" | "unsupported"
                        # | "frozen" | "locked" | "error"
    supported: bool
    frozen: bool
    duration_seconds: float
    error: str | None


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _build_security_block(password: str, *, enhanced: bool = False) -> bytes:
    """Build the 512-byte SET PASSWORD / ERASE UNIT data block.

    Layout (little-endian, per ATA8-ACS):
      word 0   — control word.  For ERASE UNIT, bit 1 selects enhanced erase.
                 For SET PASSWORD, bit 0 selects master/user (0 = user here).
      bytes 2..33 — 32-byte password, ASCII, NUL-padded.
      rest     — zero.
    """
    buf = bytearray(SECURITY_BLOCK_SIZE)
    word0 = ERASE_BIT_ENHANCED if enhanced else 0x0000
    buf[0] = word0 & 0xFF
    buf[1] = (word0 >> 8) & 0xFF

    pw = password.encode("ascii", errors="ignore")[:PASSWORD_FIELD_BYTES]
    buf[2:2 + len(pw)] = pw
    return bytes(buf)


def _result(
    *,
    success: bool,
    method: str,
    supported: bool,
    frozen: bool,
    start: float,
    error: str | None = None,
) -> SecureEraseResult:
    return SecureEraseResult(
        success=success,
        method=method,
        supported=supported,
        frozen=frozen,
        duration_seconds=time.monotonic() - start,
        error=error,
    )


# ─────────────────────────────────────────────────────────────────────
# Status probe — NEVER erases
# ─────────────────────────────────────────────────────────────────────

def secure_erase_supported(handle: int) -> SecureEraseResult:
    """Report whether ATA Secure Erase is ready on this drive. NEVER erases.

    No ATA command is issued beyond IDENTIFY (read-only). ``success`` is always
    ``False`` here — this is a *status* probe, not an erase. Read ``supported``,
    ``frozen`` and ``method`` to decide whether an erase is worth offering.
    """
    start = time.monotonic()
    try:
        identify = passthrough.ata_identify(handle)
        if identify is None or not identify.security_supported:
            return _result(
                success=False, method="unsupported",
                supported=False, frozen=False, start=start,
            )

        if identify.security_frozen:
            return _result(
                success=False, method="frozen",
                supported=True, frozen=True, start=start,
            )

        method = (
            "ata-enhanced"
            if identify.enhanced_erase_supported
            else "ata-secure-erase"
        )
        return _result(
            success=False, method=method,
            supported=True, frozen=False, start=start,
        )
    except Exception as exc:  # never raise across the public surface
        audit_log(f"secure_erase_supported: probe failed: {exc!r}")
        return _result(
            success=False, method="error",
            supported=False, frozen=False, start=start, error=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────
# The erase — EXPERIMENTAL
# ─────────────────────────────────────────────────────────────────────

def secure_erase(
    handle: int,
    *,
    enhanced: bool = False,
    password: str = "StickShredder",
    timeout_seconds: int = 7200,
) -> SecureEraseResult:
    """Issue the drive's own ATA Secure Erase. EXPERIMENTAL — may brick a drive.

    Caller is responsible for the experimental opt-in gate; this function still
    re-checks the drive and refuses (issuing NO command) if the drive is
    unsupported, frozen, or locked. It never raises.

    Sequence on a supported, unfrozen, unlocked drive:
      1. SECURITY SET PASSWORD   (0xF1) — set a known user password.
      2. SECURITY ERASE PREPARE  (0xF3) — arm the erase.
      3. SECURITY ERASE UNIT     (0xF4) — run it (enhanced if requested).
    """
    start = time.monotonic()
    try:
        identify = passthrough.ata_identify(handle)
        if identify is None or not identify.security_supported:
            return _result(
                success=False, method="unsupported",
                supported=False, frozen=False, start=start,
            )
        if identify.security_frozen:
            audit_log("secure_erase: refusing — drive is SECURITY frozen")
            return _result(
                success=False, method="frozen",
                supported=True, frozen=True, start=start,
            )
        if identify.security_locked:
            audit_log("secure_erase: refusing — drive is SECURITY locked")
            return _result(
                success=False, method="locked",
                supported=True, frozen=False, start=start,
            )

        method = "ata-enhanced" if enhanced else "ata-secure-erase"
        audit_log(
            f"secure_erase: EXPERIMENTAL {method} starting "
            f"(enhanced={enhanced}, timeout={timeout_seconds}s)"
        )

        # 1) SECURITY SET PASSWORD — control word 0 (user password).
        set_pw_block = _build_security_block(password, enhanced=False)
        res = passthrough.send_ata_command(
            handle,
            command=ATA_SECURITY_SET_PASSWORD,
            data_out=set_pw_block,
            timeout_seconds=timeout_seconds,
        )
        if res is None or not res.success:
            return _result(
                success=False, method="error",
                supported=True, frozen=False, start=start,
                error="SECURITY SET PASSWORD failed",
            )

        # 2) SECURITY ERASE PREPARE — no data.
        res = passthrough.send_ata_command(
            handle,
            command=ATA_SECURITY_ERASE_PREPARE,
            timeout_seconds=timeout_seconds,
        )
        if res is None or not res.success:
            return _result(
                success=False, method="error",
                supported=True, frozen=False, start=start,
                error="SECURITY ERASE PREPARE failed",
            )

        # 3) SECURITY ERASE UNIT — same password, enhanced bit if requested.
        erase_block = _build_security_block(password, enhanced=enhanced)
        res = passthrough.send_ata_command(
            handle,
            command=ATA_SECURITY_ERASE_UNIT,
            data_out=erase_block,
            timeout_seconds=timeout_seconds,
        )
        if res is None or not res.success:
            return _result(
                success=False, method="error",
                supported=True, frozen=False, start=start,
                error="SECURITY ERASE UNIT failed",
            )

        audit_log(f"secure_erase: {method} reported success")
        return _result(
            success=True, method=method,
            supported=True, frozen=False, start=start,
        )
    except Exception as exc:  # never raise across the public surface
        audit_log(f"secure_erase: unexpected failure: {exc!r}")
        return _result(
            success=False, method="error",
            supported=False, frozen=False, start=start, error=str(exc),
        )
