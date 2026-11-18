"""ATA Secure Erase — EXPERIMENTAL firmware-level erase for SATA/flash drives.

Instead of overwriting every sector, this asks the drive to run its *own*
built-in erase command (the ATA SECURITY feature set). On supporting hardware
this is far faster and reaches sectors a host overwrite cannot (wear-levelled
flash, reallocated sectors).

⚠️  EXPERIMENTAL — UNTESTED ON REAL HARDWARE.  A wrong or interrupted command
can leave a drive locked or unusable. This module is therefore deliberately
*safe in itself*:

  * it sends NO command at all unless ``wipe.transport`` chose the "ata" path
    for the handle's disk (a SATA disk on Microsoft's storahci driver that is
    not a system disk) — never to USB, NVMe, vendor RAID/VMD drivers or the
    system disk;
  * it NEVER issues an erase command on a drive that is frozen, locked, or that
    does not advertise the SECURITY feature set;
  * when an erase step fails after the temporary password was set, it asks the
    drive again and removes that password (SECURITY DISABLE PASSWORD) while
    the drive is still unlocked, and reports whether it may still be set;
  * ``unlock_drive`` removes the temporary password from a drive an
    interrupted erase left locked — one try, with that password only;
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
from wipe import passthrough, transport

# ── ATA SECURITY feature-set command codes ───────────────────────────────────
ATA_SECURITY_SET_PASSWORD = 0xF1
ATA_SECURITY_UNLOCK = 0xF2
ATA_SECURITY_ERASE_PREPARE = 0xF3
ATA_SECURITY_ERASE_UNIT = 0xF4
ATA_SECURITY_DISABLE_PASSWORD = 0xF6

SECURITY_BLOCK_SIZE = 512   # the SET PASSWORD / ERASE UNIT data block is 1 sector
PASSWORD_FIELD_BYTES = 32   # ATA password field: 32 bytes (words 1..16)

# The temporary ATA user password StickShredder sets for an erase. Callers
# show it and log it before it is set, so a drive left locked can be unlocked.
DEFAULT_PASSWORD = "StickShredder"

# Word 0 bits of the ERASE UNIT block.
ERASE_BIT_ENHANCED = 0x0002  # bit 1 — enhanced erase (else normal erase)
# Word 0 bit 0 of the SET PASSWORD / UNLOCK / DISABLE PASSWORD block selects
# master(1)/user(0) password. We only ever use the user password: word 0 == 0.

# IDENTIFY word 128 bit 4: too many wrong passwords, the drive refuses UNLOCK
# and DISABLE PASSWORD until it is powered off and on again.
_W128_ATTEMPTS_EXPIRED = 0x0010

# DISABLE PASSWORD and IDENTIFY answer at once; no reason to wait long.
_CLEANUP_TIMEOUT_SECONDS = 30


@dataclass
class SecureEraseResult:
    success: bool
    method: str         # "ata-secure-erase" | "ata-enhanced" | "unsupported"
                        # | "frozen" | "locked" | "error"
    supported: bool
    frozen: bool
    duration_seconds: float
    error: str | None
    # The drive's own erase-time estimate (IDENTIFY word 89, or 90 for the
    # enhanced erase) in minutes; 0 when unknown. Only the status probe sets it.
    estimated_minutes: int = 0
    # The temporary password may still be set on the drive (a failed erase
    # step whose clean-up could not confirm it was removed again).
    password_set: bool = False


@dataclass
class UnlockResult:
    success: bool       # the drive has no password (any more)
    state: str          # "unlocked" | "no_password" | "frozen" | "expired"
                        # | "wrong_password" | "unsupported" | "error"
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _log(message: str) -> None:
    """Audit log that never fails the drive operation (disk full, no rights)."""
    try:
        audit_log(message)
    except Exception:  # noqa: BLE001 — logging must not change the outcome
        pass


def _build_security_block(password: str, *, enhanced: bool = False) -> bytes:
    """Build the 512-byte SET PASSWORD / ERASE UNIT data block.

    Layout (little-endian, per ATA8-ACS):
      word 0   — control word.  For ERASE UNIT, bit 1 selects enhanced erase.
                 For SET PASSWORD, bit 0 selects master/user (0 = user here).
      bytes 2..33 — 32-byte password, ASCII, NUL-padded.
      rest     — zero.
    UNLOCK and DISABLE PASSWORD use the same layout (word 0 == 0: user).
    """
    buf = bytearray(SECURITY_BLOCK_SIZE)
    word0 = ERASE_BIT_ENHANCED if enhanced else 0x0000
    buf[0] = word0 & 0xFF
    buf[1] = (word0 >> 8) & 0xFF

    pw = password.encode("ascii", errors="ignore")[:PASSWORD_FIELD_BYTES]
    buf[2:2 + len(pw)] = pw
    return bytes(buf)


def _ata_path_refusal(handle) -> str | None:
    """Why ``handle``'s disk must get no ATA command, or ``None`` if it may."""
    try:
        storage = transport.inspect_handle(handle)
    except Exception as exc:  # inspect_handle should not raise, but never trust it
        return f"drive-command path could not be determined: {exc}"
    if storage.transport != "ata":
        return storage.reason
    return None


def _attempts_expired(identify) -> bool:
    """IDENTIFY word 128 bit 4 (absent from the parsed fields)."""
    words = getattr(identify, "raw_words", ()) or ()
    return len(words) > 128 and bool(words[128] & _W128_ATTEMPTS_EXPIRED)


def _result(
    *,
    success: bool,
    method: str,
    supported: bool,
    frozen: bool,
    start: float,
    error: str | None = None,
    estimated_minutes: int = 0,
    password_set: bool = False,
) -> SecureEraseResult:
    return SecureEraseResult(
        success=success,
        method=method,
        supported=supported,
        frozen=frozen,
        duration_seconds=time.monotonic() - start,
        error=error,
        estimated_minutes=estimated_minutes,
        password_set=password_set,
    )


def _estimated_minutes(identify, *, enhanced: bool) -> int:
    """The drive's erase-time estimate in minutes (0 if it gives none).

    Word 89 times the normal erase, word 90 the enhanced one; both use the
    encoding ``passthrough._erase_unit_minutes`` decodes.
    """
    if not enhanced:
        return int(getattr(identify, "erase_unit_minutes", 0) or 0)
    words = getattr(identify, "raw_words", ()) or ()
    if len(words) <= 90:
        return 0
    return passthrough._erase_unit_minutes(words[90])


def _password_still_set(handle: int, password: str) -> bool:
    """After a failed erase step: remove the temporary password again.

    Asks the drive (IDENTIFY). Security not enabled → no password, nothing to
    do. Enabled and still unlocked → SECURITY DISABLE PASSWORD with the
    temporary password. Returns whether the password may still be set.
    """
    try:
        identify = passthrough.ata_identify(handle)
        if identify is None:
            _log("secure_erase: clean-up could not read the drive; password may be set")
            return True
        if not identify.security_enabled:
            return False
        if identify.security_locked or identify.security_frozen:
            _log("secure_erase: clean-up impossible (drive locked or frozen); "
                 "temporary password stays set")
            return True
        res = passthrough.send_ata_command(
            handle,
            command=ATA_SECURITY_DISABLE_PASSWORD,
            data_out=_build_security_block(password),
            timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
        )
        if res is None or not res.success:
            _log("secure_erase: SECURITY DISABLE PASSWORD failed; password may be set")
            return True
        check = passthrough.ata_identify(handle)
        still = check is None or bool(check.security_enabled)
        _log("secure_erase: temporary password " + ("may still be set" if still
                                                    else "removed again"))
        return still
    except Exception as exc:  # noqa: BLE001 — never raise across the public surface
        _log(f"secure_erase: clean-up failed: {exc!r}")
        return True


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
        refusal = _ata_path_refusal(handle)
        if refusal:
            _log(f"secure_erase_supported: no ATA command sent — {refusal}")
            return _result(
                success=False, method="unsupported",
                supported=False, frozen=False, start=start, error=refusal,
            )

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

        if identify.security_locked:
            # Someone's password locks it (maybe ours from an interrupted
            # erase): no erase, and no new password on top of it.
            return _result(
                success=False, method="locked",
                supported=True, frozen=False, start=start,
            )

        enhanced = bool(identify.enhanced_erase_supported)
        method = "ata-enhanced" if enhanced else "ata-secure-erase"
        return _result(
            success=False, method=method,
            supported=True, frozen=False, start=start,
            estimated_minutes=_estimated_minutes(identify, enhanced=enhanced),
        )
    except Exception as exc:  # never raise across the public surface
        _log(f"secure_erase_supported: probe failed: {exc!r}")
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
    password: str = DEFAULT_PASSWORD,
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
    If a step fails once the password was sent, the drive is asked again and
    the password removed (``_password_still_set``); ``password_set`` says
    whether it may still be on the drive.
    """
    start = time.monotonic()
    password_sent = False

    def failed(error: str, *, supported: bool = True) -> SecureEraseResult:
        still = _password_still_set(handle, password) if password_sent else False
        return _result(
            success=False, method="error",
            supported=supported, frozen=False, start=start, error=error,
            password_set=still,
        )

    try:
        refusal = _ata_path_refusal(handle)
        if refusal:
            _log(f"secure_erase: refusing, no ATA command sent — {refusal}")
            return _result(
                success=False, method="unsupported",
                supported=False, frozen=False, start=start, error=refusal,
            )

        identify = passthrough.ata_identify(handle)
        if identify is None or not identify.security_supported:
            return _result(
                success=False, method="unsupported",
                supported=False, frozen=False, start=start,
            )
        if identify.security_frozen:
            _log("secure_erase: refusing — drive is SECURITY frozen")
            return _result(
                success=False, method="frozen",
                supported=True, frozen=True, start=start,
            )
        if identify.security_locked:
            _log("secure_erase: refusing — drive is SECURITY locked")
            return _result(
                success=False, method="locked",
                supported=True, frozen=False, start=start,
            )

        method = "ata-enhanced" if enhanced else "ata-secure-erase"
        _log(
            f"secure_erase: EXPERIMENTAL {method} starting "
            f"(enhanced={enhanced}, timeout={timeout_seconds}s)"
        )

        # 1) SECURITY SET PASSWORD — control word 0 (user password).
        set_pw_block = _build_security_block(password, enhanced=False)
        password_sent = True
        res = passthrough.send_ata_command(
            handle,
            command=ATA_SECURITY_SET_PASSWORD,
            data_out=set_pw_block,
            timeout_seconds=timeout_seconds,
        )
        if res is None or not res.success:
            return failed("SECURITY SET PASSWORD failed")

        # 2) SECURITY ERASE PREPARE — no data.
        res = passthrough.send_ata_command(
            handle,
            command=ATA_SECURITY_ERASE_PREPARE,
            timeout_seconds=timeout_seconds,
        )
        if res is None or not res.success:
            return failed("SECURITY ERASE PREPARE failed")

        # 3) SECURITY ERASE UNIT — same password, enhanced bit if requested.
        erase_block = _build_security_block(password, enhanced=enhanced)
        res = passthrough.send_ata_command(
            handle,
            command=ATA_SECURITY_ERASE_UNIT,
            data_out=erase_block,
            timeout_seconds=timeout_seconds,
        )
        if res is None or not res.success:
            return failed("SECURITY ERASE UNIT failed")

        _log(f"secure_erase: {method} reported success")
        return _result(
            success=True, method=method,
            supported=True, frozen=False, start=start,
        )
    except Exception as exc:  # never raise across the public surface
        _log(f"secure_erase: unexpected failure: {exc!r}")
        return failed(str(exc), supported=False)


# ─────────────────────────────────────────────────────────────────────
# „Gesperrte Platte entsperren“ — remove the temporary password
# ─────────────────────────────────────────────────────────────────────

def unlock_drive(handle: int, *, password: str = DEFAULT_PASSWORD) -> UnlockResult:
    """Remove the temporary password from a drive. One try. Never raises.

    For a drive an interrupted hardware erase left with StickShredder's
    password: SECURITY UNLOCK (0xF2, only when locked) and SECURITY DISABLE
    PASSWORD (0xF6), both with ``password``. A wrong password counts as a
    failed attempt on the drive (it allows about five), so there is no second
    try. Only on the "ata" path, like every other ATA command here.
    """
    try:
        refusal = _ata_path_refusal(handle)
        if refusal:
            _log(f"unlock_drive: no ATA command sent — {refusal}")
            return UnlockResult(False, "unsupported", refusal)

        identify = passthrough.ata_identify(handle)
        if identify is None or not identify.security_supported:
            return UnlockResult(False, "unsupported",
                                "the drive does not offer the ATA security feature set")
        if not identify.security_enabled:
            _log("unlock_drive: no password set, nothing sent")
            return UnlockResult(True, "no_password")
        if identify.security_frozen:
            return UnlockResult(False, "frozen", "the drive is SECURITY FROZEN")
        if _attempts_expired(identify):
            return UnlockResult(False, "expired",
                                "too many wrong passwords; power the drive off and on")

        block = _build_security_block(password)
        _log(f"unlock_drive: removing the temporary password {password!r} (one try)")
        if identify.security_locked:
            res = passthrough.send_ata_command(
                handle, command=ATA_SECURITY_UNLOCK, data_out=block,
                timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
            )
            if res is None or not res.success:
                _log("unlock_drive: SECURITY UNLOCK refused (wrong password?)")
                return UnlockResult(False, "wrong_password",
                                    "the drive did not accept the password")

        res = passthrough.send_ata_command(
            handle, command=ATA_SECURITY_DISABLE_PASSWORD, data_out=block,
            timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
        )
        if res is None or not res.success:
            return UnlockResult(False, "error", "SECURITY DISABLE PASSWORD failed")
        check = passthrough.ata_identify(handle)
        if check is None or check.security_enabled:
            return UnlockResult(False, "error", "the password is still set")
        _log("unlock_drive: unlocked, password removed")
        return UnlockResult(True, "unlocked")
    except Exception as exc:  # noqa: BLE001 — never raise across the public surface
        _log(f"unlock_drive: unexpected failure: {exc!r}")
        return UnlockResult(False, "error", str(exc) or type(exc).__name__)
