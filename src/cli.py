"""Command-line interface for StickShredder — Windows USB wipe tool."""

from __future__ import annotations

import argparse
import csv
import getpass
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qs

import core.config as settings
from cert.generator import CertificateData, format_capacity, generate_certificate
from cert.payload import PAYLOAD_LABELS, RESULT_LABELS, nist_category, result_status
from cert.signing import SigningKey, SigningKeyError, b64url_decode
from cert.verify_cert import (
    TrustStore, extract_key_from_keycard_pdf, extract_token_from_pdf, verify_token,
)
from core.config import AppConfig, get_next_cert_number, DEFAULT_CERT_OUTPUT, load_signing_key
from core.protection_class import class3_note
from core.log import (
    audit_log, history_result_status, history_result_value, log_wipe_to_csv,
    read_wipe_history, setup_logging, verify_wipe_history,
)
from wipe.device import (
    DeviceInfo, UnsafeTargetError, list_devices, open_physical_drive, close_drive,
    get_drive_size, is_safe_to_wipe, prepare_target, release_volumes, unique_disks,
)
from wipe.methods import ZeroFill, RandomThreePass, BsiVsitr, CustomWipe, WipeResult
from wipe.verify import VerifyResult
from wipe.hidden_areas import detect_hidden_areas, hidden_bytes
from wipe import hardware_erase, secure_erase
from wipe.recommend import (
    MULTI_PASS_NOTE_DE, MULTI_PASS_NOTE_EN, Recommendation, media_type_of, recommend,
)


def is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


# -- ANSI colour helpers ----------------------------------------------------

class _Ansi:
    """ANSI escape codes for coloured terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"

    @staticmethod
    def enabled() -> bool:
        """Return True if ANSI colours should be used."""
        return sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    """Wrap *text* in an ANSI colour code if the terminal supports it."""
    if _Ansi.enabled():
        return f"{code}{text}{_Ansi.RESET}"
    return text


# -- Formatting helpers -----------------------------------------------------

def _format_size_short(size_bytes: int) -> str:
    """Compact capacity string, e.g. '32.0 GB'."""
    if size_bytes >= 1024 ** 4:
        return f"{size_bytes / (1024 ** 4):.1f} TB"
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024 ** 3):.1f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    return f"{size_bytes / 1024:.1f} KB"


def _format_eta(seconds: float) -> str:
    """Format an ETA value as H:MM:SS or M:SS."""
    if seconds < 0:
        seconds = 0
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_duration(start: datetime, end: datetime) -> str:
    delta = end - start
    total = int(delta.total_seconds())
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _truncate(text: str, width: int) -> str:
    """Truncate text to *width*, appending an ellipsis if needed."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"


# -- Progress bar -----------------------------------------------------------

def _progress_bar(
    pass_num: int,
    total_passes: int,
    bytes_done: int,
    total_bytes: int,
    speed_mbps: float,
) -> None:
    """Render an in-place ASCII progress bar to stdout."""
    term_width = shutil.get_terminal_size((80, 24)).columns

    pct = bytes_done / total_bytes if total_bytes > 0 else 0.0
    pct_display = min(pct * 100, 100.0)

    if speed_mbps > 0:
        remaining_bytes = total_bytes - bytes_done
        eta_sec = (remaining_bytes / (1024 * 1024)) / speed_mbps
    else:
        eta_sec = 0.0

    pass_info = f"Pass {pass_num}/{total_passes} " if total_passes > 1 else ""
    suffix = f" {pct_display:5.1f}% {speed_mbps:6.1f} MB/s ETA {_format_eta(eta_sec)}"
    prefix = f"\r  {pass_info}"

    bar_space = term_width - len(prefix) - len(suffix) - 3  # 3 for [ ] and >
    if bar_space < 10:
        bar_space = 10

    filled = int(bar_space * pct)
    if filled > bar_space:
        filled = bar_space

    arrow = ">" if filled < bar_space else ""
    bar = "=" * filled + arrow + " " * (bar_space - filled - len(arrow))
    line = f"{prefix}[{bar}]{suffix}"

    sys.stdout.write(line)
    sys.stdout.flush()


def _verify_progress_bar(
    fraction: float,
    bytes_done: int,
    total_bytes: int,
    speed_mbps: float,
) -> None:
    """Render an in-place ASCII progress bar for full verification."""
    term_width = shutil.get_terminal_size((80, 24)).columns

    pct = fraction if fraction >= 0 else 0.0
    if pct > 1.0:
        pct = 1.0
    pct_display = pct * 100

    if speed_mbps > 0:
        remaining_bytes = max(0, total_bytes - bytes_done)
        eta_sec = (remaining_bytes / (1024 * 1024)) / speed_mbps
    else:
        eta_sec = 0.0

    suffix = f" {pct_display:5.1f}% {speed_mbps:6.1f} MB/s ETA {_format_eta(eta_sec)}"
    prefix = "\r  Verify "

    bar_space = term_width - len(prefix) - len(suffix) - 3  # 3 for [ ] and >
    if bar_space < 10:
        bar_space = 10

    filled = int(bar_space * pct)
    if filled > bar_space:
        filled = bar_space

    arrow = ">" if filled < bar_space else ""
    bar = "=" * filled + arrow + " " * (bar_space - filled - len(arrow))
    line = f"{prefix}[{bar}]{suffix}"

    sys.stdout.write(line)
    sys.stdout.flush()


# -- Device table -----------------------------------------------------------

def _device_status(dev: DeviceInfo) -> tuple[str, str]:
    """Return (status_label, ansi_colour_code) for a device."""
    if dev.is_system_drive:
        return ("SYSTEM", _Ansi.RED)
    if dev.is_internal and not dev.is_removable:
        return ("INTERNAL", _Ansi.YELLOW)
    return ("OK", _Ansi.GREEN)


def _print_device_table(devices: list[DeviceInfo]) -> None:
    """Print a formatted, colour-coded device table."""
    if not devices:
        print(_c(_Ansi.YELLOW, "  No devices detected."))
        return

    # Column headers
    headers = ["Drive", "Model", "Serial", "Capacity", "FS", "Type", "Status"]
    # Compute column widths
    rows: list[tuple[str, str, str, str, str, str, str, str]] = []  # + colour
    for dev in devices:
        status_label, colour = _device_status(dev)
        rows.append((
            dev.drive_letter or _physical_label(dev.device_id),
            _truncate(dev.model or "Unknown", 28),
            _truncate(dev.serial_number or "N/A", 20),
            _format_size_short(dev.capacity_bytes),
            dev.filesystem or "RAW",
            dev.connection_type,
            status_label,
            colour,
        ))

    # Fixed column widths
    widths = [6, 30, 22, 12, 8, 8, 10]

    # Print header
    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(f"\n  {_c(_Ansi.BOLD, header_line)}")
    print(f"  {'=' * sum(widths + [2 * (len(widths) - 1)])}")

    # Print rows
    for drive, model, serial, cap, fs, conn, status, colour in rows:
        cells = [drive, model, serial, cap, fs, conn]
        line_parts = [cell.ljust(w) for cell, w in zip(cells, widths[:-1])]
        status_str = _c(colour + _Ansi.BOLD, status.ljust(widths[-1]))
        line = "  ".join(line_parts) + "  " + status_str
        print(f"  {line}")

    print()


# -- Wipe method resolution -------------------------------------------------

_METHOD_MAP = {
    "zero": "ZeroFill",
    "standard": "RandomThreePass",
    "bsi": "BSI-VSITR",
    "custom": "Custom",
}


def _resolve_wipe_method(args: argparse.Namespace) -> ZeroFill | RandomThreePass | BsiVsitr | CustomWipe:
    """Return a WipeMethod instance based on CLI arguments."""
    method = args.method.lower()
    if method == "zero":
        return ZeroFill()
    if method == "standard":
        return RandomThreePass()
    if method == "bsi":
        return BsiVsitr()
    if method == "custom":
        pattern = getattr(args, "pattern", "random") or "random"
        passes = getattr(args, "passes", 3) or 3
        return CustomWipe(passes=passes, pattern=pattern)
    # argparse choices should prevent reaching here
    _die(f"Unknown wipe method: {method}")


# -- Helpers ----------------------------------------------------------------

def _die(message: str) -> NoReturn:
    print(_c(_Ansi.RED + _Ansi.BOLD, f"\n  ERROR: {message}\n"), file=sys.stderr)
    sys.exit(1)


def _warn(message: str) -> None:
    print(_c(_Ansi.YELLOW, f"  WARNING: {message}"))


def _info(message: str) -> None:
    print(f"  {message}")


def _success(message: str) -> None:
    print(_c(_Ansi.GREEN, f"  {message}"))


def _printable(value: object) -> str:
    """Text safe for the terminal: control characters (e.g. ANSI escapes
    smuggled into a certificate field) are replaced, never interpreted."""
    return "".join(ch if ch.isprintable() else "�" for ch in str(value))


def _load_key_or_die() -> SigningKey:
    """This installation's signing key (created on first use), or exit 1."""
    try:
        return load_signing_key()
    except (SigningKeyError, OSError) as exc:
        _die(
            "Der Signaturschlüssel kann nicht geladen werden; es wurde nichts verändert. / "
            f"The signing key cannot be loaded; nothing was changed.\n  {exc}\n"
            "  Abhilfe / Fix: stickshredder key import <exportierte Schlüsseldatei / "
            "exported key file>"
        )


def _load_trust_store_or_die() -> TrustStore:
    try:
        return TrustStore.load(settings.TRUST_FILE)
    except ValueError as exc:
        _die(str(exc))


def _physical_label(device_id: str) -> str:
    """Short label for a physical drive id, e.g. r'\\\\.\\PhysicalDrive2' -> 'PD2'."""
    import re
    m = re.search(r"PhysicalDrive(\d+)", device_id or "")
    return f"PD{m.group(1)}" if m else "?"


def _find_device(identifier: str, devices: list[DeviceInfo]) -> DeviceInfo | None:
    """Find a DeviceInfo by drive letter, or by physical-drive id for letterless
    disks (e.g. 'PhysicalDrive2', r'\\\\.\\PhysicalDrive2', or a bare '2')."""
    target = identifier.strip()
    target_letter = target.upper().rstrip(":")
    # 1) Drive-letter match (lettered volumes).
    for dev in devices:
        if dev.drive_letter and dev.drive_letter.upper().rstrip(":") == target_letter:
            return dev
    # 2) Physical-drive match (letterless internal/raw disks).
    import re
    m = re.search(r"(?:physicaldrive)?(\d+)$", target.lower())
    if m:
        want = f"\\\\.\\physicaldrive{int(m.group(1))}"
        for dev in devices:
            if dev.device_id.lower() == want:
                return dev
    return None


# -- Three-level result and recommendations ---------------------------------

# ``wipe`` exit codes per three-level result (spec G4).
_RESULT_EXIT = {"ok": 0, "limited": 3, "failed": 1}
_RESULT_COLOUR = {"ok": _Ansi.GREEN, "limited": _Ansi.YELLOW, "failed": _Ansi.RED}

_VERIFY_MODE_LABEL = {
    "none": "Keine / Skipped",
    "sample": "Stichprobe (100 Sektoren) / Sample (100 sectors)",
    "full": "Vollständig / Full",
}

_MEDIA_LABEL = {
    "hdd": "Festplatte (HDD) / Hard disk (HDD)",
    "ssd": "SSD",
    "flash": "USB-Stick/SD-Karte / USB stick/SD card",
    "unknown": "Unbekannt / Unknown",
}

_METHOD_LABEL = {
    "zero": "zero — Nullen, 1 Durchgang / zeros, 1 pass",
    "standard": "standard — 3 Durchgänge Zufall / 3 random passes",
    "bsi": "bsi — BSI-VSITR, 7 Durchgänge / 7 passes",
}

# Multi-pass methods: allowed, never recommended (spec G6).
_MULTI_PASS_METHODS = ("standard", "bsi")


def wipe_outcome(data: CertificateData) -> tuple[int, str]:
    """Exit code and result line for a finished wipe.

    Same three levels as the certificate (``result_status``) and the history
    (``OK``/``LIMITED``/``FAILED``): exit 0 ok, 3 with limitations, 1 failed.
    """
    status = result_status(data)
    de, en = RESULT_LABELS[status]
    return _RESULT_EXIT[status], f"Ergebnis / Result: {de} / {en}"


def _print_recommendation(rec: Recommendation, indent: str = "    ") -> None:
    method = _METHOD_LABEL.get(rec.method_key, rec.method_key)
    if rec.hardware_erase:
        method = f"Hardware-Löschen (Beta), danach / hardware erase (Beta), then {method}"
    verify = _VERIFY_MODE_LABEL.get(rec.verify_mode, rec.verify_mode)
    print(f"{indent}Verfahren / Method:        {method}")
    print(f"{indent}Prüfung / Verification:    {verify}")
    print(f"{indent}NIST SP 800-88:            {rec.nist_category}")
    print(f"{indent}Begründung:                {rec.reason_de}")
    print(f"{indent}Reason:                    {rec.reason_en}")
    for de, en in zip(rec.warnings_de, rec.warnings_en):
        _warn(de)
        print(f"  {' ' * len('WARNING: ')}{en}")


# Why a hardware erase did not start (EraseReport.reason), German / English.
_HARDWARE_REFUSAL = {
    "frozen": (
        "Das Laufwerk ist vom PC gesperrt („frozen“). PC in den Standby schicken und "
        "wieder aufwecken oder das Laufwerk ab- und wieder anstecken, dann erneut "
        "versuchen. / The drive is frozen by the PC. Put the PC to sleep and wake it, "
        "or unplug and replug the drive, then try again."
    ),
    "no_path": (
        "Dieser Anschluss reicht den Löschbefehl nicht weiter (nur SATA direkt am "
        "Microsoft-Treiber storahci). / This connection does not pass the erase "
        "command on (only SATA on Microsoft's storahci driver)."
    ),
    "unsupported": (
        "Das Laufwerk bietet keine Hardware-Löschung an. / The drive does not offer "
        "a hardware erase."
    ),
    "locked": (
        "Das Laufwerk ist mit einem Passwort gesperrt. / The drive is locked with "
        "a password."
    ),
    "has_password": (
        "Das Laufwerk hat bereits ein Passwort (nicht von dieser Löschung). Stammt es "
        "von einer abgebrochenen Hardware-Löschung: 'stickshredder unlock'. / The drive "
        "already has a password (not from this erase). If it is left from an interrupted "
        "hardware erase: 'stickshredder unlock'."
    ),
    "error": (
        "Das Laufwerk konnte nicht abgefragt werden. / The drive could not be asked."
    ),
}


def _hardware_stop_message(report: hardware_erase.EraseReport) -> str:
    """Why the hardware erase stopped and what that means for the disk."""
    if report.status == "refused":
        reason = _HARDWARE_REFUSAL.get(report.reason, _HARDWARE_REFUSAL["error"])
        detail = f" ({report.detail})" if report.reason in ("no_path", "error") else ""
        return (
            f"Hardware-Löschung nicht möglich / hardware erase not possible: {reason}"
            f"{detail}\n  Nichts wurde geschrieben. Zum Überschreiben: --method zero / "
            "Nothing was written. To overwrite instead: --method zero"
        )
    return (
        f"Hardware-Löschung fehlgeschlagen / hardware erase failed: {report.detail}\n"
        f"  Der Datenträger ist NICHT sicher gelöscht. Das Laufwerk hat eventuell noch das "
        f"temporäre Passwort „{hardware_erase.TEMP_PASSWORD}“ — ist es nach dem Abstecken "
        f"gesperrt, damit entsperren. / The disk is NOT securely wiped. The drive may "
        f"still have the temporary password \"{hardware_erase.TEMP_PASSWORD}\" — if it is "
        f"locked after replugging, unlock it with that password."
    )


def _hardware_interrupted_message() -> str:
    """Ctrl+C during a hardware erase: what state the disk may be in."""
    return (
        "Hardware-Löschung abgebrochen (Strg+C) / hardware erase interrupted (Ctrl+C).\n"
        "  Der Datenträger ist NICHT sicher gelöscht. Hat das Laufwerk schon mit dem "
        "Löschen begonnen, löscht es eventuell weiter — nicht abstecken, bis es fertig "
        "ist. Ist es danach gesperrt: temporäres Passwort "
        f"„{hardware_erase.TEMP_PASSWORD}“, entsperren mit 'stickshredder unlock'. / "
        "The disk is NOT securely wiped. If the drive had started erasing it may "
        "still be at work — do not unplug it until it is done. If it is locked "
        f"afterwards: temporary password \"{hardware_erase.TEMP_PASSWORD}\", "
        "unlock it with 'stickshredder unlock'."
    )


# Outcome of `stickshredder unlock` (UnlockResult.state), German / English.
_UNLOCK_TEXT = {
    "unlocked": (
        "Entsperrt: Das temporäre Passwort ist entfernt. / Unlocked: the temporary "
        "password is removed."
    ),
    "no_password": (
        "Das Laufwerk hat kein Passwort — nichts zu tun. / The drive has no "
        "password — nothing to do."
    ),
    "frozen": _HARDWARE_REFUSAL["frozen"],
    "expired": (
        "Zu viele Fehlversuche: Das Laufwerk nimmt erst nach dem Aus- und "
        "Einschalten (abstecken und wieder anstecken) wieder ein Passwort an. / Too "
        "many failed attempts: the drive only takes a password again after it was "
        "powered off and on (unplug and plug it back in)."
    ),
    "wrong_password": (
        "Das Passwort passt nicht: Die Sperre stammt nicht von StickShredder. Ein "
        "Versuch wurde verbraucht, es gibt keinen zweiten. / The password does not fit: "
        "the lock is not StickShredder's. One attempt was used; there is no second."
    ),
    "unsupported": _HARDWARE_REFUSAL["no_path"],
    "error": "Entsperren fehlgeschlagen / unlock failed.",
}


def _print_erase_started(method: str, estimated_minutes: int) -> None:
    estimate = (f"Schätzung des Laufwerks / drive's estimate: {estimated_minutes} min"
                if estimated_minutes else "keine Schätzung / no estimate")
    _info(
        f"Das Laufwerk löscht sich selbst / the drive erases itself: "
        f"{hardware_erase.erase_label(method)} ({estimate}). Nicht abstecken / do not unplug."
    )


def _print_erase_finished(report: hardware_erase.EraseReport) -> None:
    if report.status == "erased":
        _success(
            f"Laufwerk meldet: gelöscht / drive reports: erased "
            f"({report.duration_seconds:.0f} s). Jetzt 1× Nullen + Prüfung / now one "
            "pass of zeros + verification."
        )


def _status_text(dev: DeviceInfo) -> str:
    if dev.is_system_drive:
        return "Systemplatte — wird nie gelöscht / System disk — never wiped"
    if dev.is_internal and not dev.is_removable:
        return "Intern / Internal (nur mit / only with --allow-internal)"
    return "Wechseldatenträger / Removable"


# -- Command: info ----------------------------------------------------------

def cmd_info(args: argparse.Namespace) -> None:
    """Show a device and the recommended procedure.

    WMI only: the disk is never opened and no drive command is sent, so
    whether a hardware erase would work is not known here.
    """
    config = AppConfig.load()
    schutzklasse = args.schutzklasse or config.default_schutzklasse or 2
    device = _find_device(args.device, list_devices())
    if device is None:
        _die(
            f"Datenträger '{args.device}' nicht gefunden / Device '{args.device}' not "
            "found. Run 'stickshredder list' to see available devices."
        )

    media = media_type_of(device)
    letters = ", ".join(
        getattr(device, "disk_letters", None) or [device.drive_letter or "-"]
    )
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Datenträger / Device"))
    print()
    print(f"    Modell / Model:            {_printable(device.model or 'Unknown')}")
    print(f"    Typ / Type:                {_MEDIA_LABEL[media]}")
    print(f"    Laufwerke / Drive letters: {letters}")
    print(f"    Datenträger / Disk:        {device.device_id}")
    print(f"    Kapazität / Capacity:      {_format_size_short(device.capacity_bytes)}")
    print(f"    Anschluss / Connection:    {device.connection_type}")
    print(f"    Status:                    {_status_text(device)}")
    print()

    if device.is_system_drive:
        _info(
            "Keine Empfehlung: StickShredder löscht die Windows-Systemplatte nie. / "
            "No recommendation: StickShredder never wipes the Windows system disk."
        )
        print()
        return

    rec = recommend(device, media, False, schutzklasse)
    print(_c(_Ansi.BOLD, f"  Empfehlung / Recommendation (Schutzklasse {schutzklasse}):"))
    _print_recommendation(rec)
    print()


# -- Command: unlock --------------------------------------------------------

def cmd_unlock(args: argparse.Namespace) -> None:
    """„Gesperrte Platte entsperren“: remove StickShredder's temporary password.

    For a drive an interrupted hardware erase left locked. Same gates as a
    wipe (administrator, safety check, prepare_target), then one try with
    the temporary password (SECURITY UNLOCK + DISABLE PASSWORD).
    """
    if not is_admin():
        _die("Administrator privileges required. Please run StickShredder from an "
             "elevated (Administrator) terminal and try again.")
    if not getattr(args, "experimental", False):
        _die("'unlock' sends ATA security commands (Beta, untested on real hardware). "
             "Re-run with --experimental to acknowledge.")
    device = _find_device(args.device, list_devices())
    if device is None:
        _die(f"Device '{args.device}' not found. Run 'stickshredder list' to see "
             "available devices.")
    safe, reason = is_safe_to_wipe(device, allow_internal=getattr(args, "allow_internal", False))
    if not safe:
        _die(reason)

    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Gesperrte Platte entsperren / Unlock"))
    print(f"    Laufwerk / Drive:  {device.model or 'Unknown'} · SN "
          f"{device.serial_number or 'N/A'} · {device.device_id}")
    print(
        "    StickShredder versucht EINMAL, das Laufwerk mit dem temporären Passwort "
        f"„{hardware_erase.TEMP_PASSWORD}“ zu entsperren und das Passwort zu entfernen.\n"
        "    Passt es nicht, zählt das als Fehlversuch. / StickShredder tries ONCE to "
        f"unlock the drive with the temporary password \"{hardware_erase.TEMP_PASSWORD}\" "
        "and to remove it.\n    A wrong password counts as a failed attempt."
    )
    print()
    if not getattr(args, "yes", False):
        resp = input("  Fortfahren? / Continue? [y/N] ").strip().lower()
        if resp not in ("y", "yes", "j", "ja"):
            print("  Aborted.")
            sys.exit(0)

    handle: int | None = None
    volume_locks: list[int] = []
    try:
        try:
            volume_locks = prepare_target(device)
        except UnsafeTargetError as exc:
            _die(str(exc))
        handle = open_physical_drive(device.device_id)
        result = secure_erase.unlock_drive(handle)
    except OSError as exc:
        _die(f"Drive access error: {exc}")
    finally:
        if handle is not None:
            close_drive(handle)
        release_volumes(volume_locks)

    audit_log(f"CLI unlock: device={device.device_id} state={result.state}")
    message = _UNLOCK_TEXT.get(result.state, _UNLOCK_TEXT["error"])
    if result.state in ("unsupported", "error") and result.error:
        message = f"{message} ({result.error})"
    if not result.success:
        _die(message)
    _success(message)
    print()


# -- Command: list ----------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> None:
    """Handle the 'list' command."""
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Detected Devices"))
    print(_c(_Ansi.DIM, "  Scanning devices...\n"))

    devices = list_devices()
    _print_device_table(devices)

    # One row per drive letter, but a wipe always takes the whole disk:
    # count physical disks, not letters.
    removable = unique_disks([d for d in devices if d.safe_to_wipe and d.is_removable])
    if removable:
        _info(f"Found {len(removable)} removable disk(s) ready for wiping.")
    else:
        _warn("No safe removable devices found.")

    internal = unique_disks([d for d in devices if d.is_internal and not d.is_system_drive])
    if internal:
        _warn(
            f"{len(internal)} internal disk(s) detected (shown as INTERNAL). "
            "Wipe them with 'wipe --allow-internal --device PhysicalDriveN'. "
            "The Windows system disk is always protected."
        )


# -- Command: wipe ----------------------------------------------------------

def cmd_wipe(args: argparse.Namespace) -> None:
    """Handle the 'wipe' command."""
    # Admin check — wipe requires raw disk access
    if not is_admin():
        _die(
            "Administrator privileges required for wiping. "
            "Please run StickShredder from an elevated (Administrator) terminal "
            "and try again."
        )

    config = AppConfig.load()

    # -- Resolve device -----------------------------------------------------
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Secure Wipe"))
    print(_c(_Ansi.DIM, "  Scanning devices...\n"))

    devices = list_devices()
    device = _find_device(args.device, devices)

    if device is None:
        _die(f"Device '{args.device}' not found. Run 'stickshredder list' to see available devices.")

    # -- Safety checks ------------------------------------------------------
    # Central gate: refuses the Windows disk always, internal drives without
    # --allow-internal, and (fail-safe) internal drives when the system disk
    # cannot be identified.
    safe, reason = is_safe_to_wipe(
        device, allow_internal=getattr(args, "allow_internal", False)
    )
    if not safe:
        _die(reason)

    # -- Resolve method and parameters --------------------------------------
    schutzklasse: int = getattr(args, "schutzklasse", 2) or 2
    # Media type (WMI, read-only): drives the recommendation, the hardware
    # erase check and the protection class 3 note on screen and certificate.
    media = media_type_of(device)
    recommendation: Recommendation | None = None
    if not getattr(args, "method", None):
        # No --method: the recommended procedure for this device type (WMI
        # only; a hardware erase is never chosen automatically).
        recommendation = recommend(device, media, False, schutzklasse)
        args.method = recommendation.method_key
    is_secure_erase = args.method == "secure-erase"
    if is_secure_erase and not getattr(args, "experimental", False):
        _die(
            "--method secure-erase is EXPERIMENTAL and untested on real "
            "hardware; it may render a drive unusable. Re-run with "
            "--experimental to acknowledge the risk."
        )
    if is_secure_erase:
        if not hardware_erase.fits(device, media):
            _die(
                "Hardware-Löschung nur für SSDs, die direkt per SATA angeschlossen sind "
                f"(hier: {_MEDIA_LABEL.get(media, media)}, {device.connection_type}). / "
                "Hardware erase is only for SSDs connected directly via SATA. "
                "Use --method zero to overwrite instead."
            )
    # A hardware erase is followed by one pass of zeros and a full
    # verification (spec G2): the drive's own report alone is never "ok".
    wipe_method = _resolve_wipe_method(
        argparse.Namespace(**{**vars(args), "method": "zero"}) if is_secure_erase else args
    )
    client: str = getattr(args, "client", "") or ""
    asset_tag: str = getattr(args, "asset_tag", "") or ""
    output_dir: str = getattr(args, "output_dir", "") or config.cert_output_dir
    language: str = getattr(args, "language", "") or config.cert_language
    verify_mode: str = getattr(args, "verify", "full") or "full"
    if is_secure_erase and verify_mode != "full":
        _info("Hardware-Löschung prüft immer jeden Sektor / a hardware erase always "
              "verifies every sector (--verify ignored).")
        verify_mode = "full"
    auto_yes: bool = getattr(args, "yes", False)

    # -- Signing key ----------------------------------------------------------
    # Loaded before anything is touched: every certificate is signed, and a
    # key problem must stop the run while the data is still intact.
    signing_key = _load_key_or_die()

    # -- Display device details ---------------------------------------------
    print(_c(_Ansi.BOLD, "  Device Details:"))
    # Wiping writes the whole physical disk: name every letter on it.
    # Letterless disks (raw / freshly wiped) are named by their disk path.
    all_letters = ", ".join(
        getattr(device, "disk_letters", None)
        or [device.drive_letter or device.device_id]
    )
    print(f"    Drive Letters:   {all_letters}")
    print(f"    Model:           {device.model or 'Unknown'}")
    print(f"    Serial:          {device.serial_number or 'N/A'}")
    print(f"    Capacity:        {_format_size_short(device.capacity_bytes)} ({device.capacity_bytes:,} bytes)")
    print(f"    Filesystem:      {device.filesystem}")
    print(f"    Connection:      {device.connection_type}")
    print(f"    Removable:       {'Yes' if device.is_removable else 'No'}")
    print()
    if is_secure_erase:
        method_display = (
            "Hardware-Löschung (Beta) / hardware erase (Beta): ATA Secure Erase, "
            f"danach / then {wipe_method.name} (1 pass)"
        )
    else:
        method_display = (
            f"{wipe_method.name} "
            f"({wipe_method.passes} pass{'es' if wipe_method.passes != 1 else ''})"
        )
    print(f"    Wipe Method:     {method_display}")
    print(f"    Schutzklasse:    {schutzklasse}")
    print(f"    Operator:        {args.operator}")
    if client:
        print(f"    Client:          {client}")
    if asset_tag:
        print(f"    Asset Tag:       {asset_tag}")
    verify_label = {
        "none": "Skipped",
        "sample": "Sample (100 random sectors)",
        "full": "Full (every sector read-back)",
    }.get(verify_mode, verify_mode)
    print(f"    Verification:    {verify_label}")
    print()

    if recommendation is not None:
        print(_c(
            _Ansi.BOLD,
            "  Kein --method angegeben: Empfohlenes Verfahren / Recommended method "
            "(no --method given):",
        ))
        _print_recommendation(recommendation)
        print()
    elif args.method in _MULTI_PASS_METHODS:
        _info(f"Hinweis: --method {args.method} {MULTI_PASS_NOTE_DE}. Empfohlen: zero.")
        _info(f"Note: --method {args.method} {MULTI_PASS_NOTE_EN}. Recommended: zero.")
        print()
    # Protection class 3 on an SSD or stick (the recommendation above
    # already carries this note).
    class3 = class3_note(media, schutzklasse, hardware_erase=is_secure_erase)
    if class3 is not None and recommendation is None:
        _warn(class3[0])
        print(f"  {' ' * len('WARNING: ')}{class3[1]}")
        print()

    # -- SSD warning --------------------------------------------------------
    if device.is_internal and not device.is_removable:
        _warn(
            "This appears to be an internal / non-removable drive. "
            "Software-based wiping may not fully erase data on SSDs due to "
            "wear-leveling and over-provisioning. Physical destruction or "
            "manufacturer-specific Secure Erase is recommended for SSDs."
        )
        print()
        if not auto_yes:
            resp = input(_c(_Ansi.YELLOW, "  Continue anyway? [y/N] ")).strip().lower()
            if resp not in ("y", "yes"):
                print("  Aborted.")
                sys.exit(0)

    # -- BitLocker ------------------------------------------------------------
    # Wiped like any other disk (as in the GUI); the prompt says what it means.
    if device.has_bitlocker:
        _warn(
            "BitLocker-verschlüsselt: alle Daten werden unwiderruflich gelöscht. / "
            "BitLocker-encrypted: all data will be irrevocably deleted."
        )
        print()

    # -- Confirmation 1 -----------------------------------------------------
    if not auto_yes:
        print(_c(_Ansi.RED + _Ansi.BOLD,
               f"  ALL DATA on {all_letters} ({device.model or 'Unknown'}) "
               f"will be PERMANENTLY DESTROYED."))
        print()
        resp = input("  Are you sure? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("  Aborted.")
            sys.exit(0)

    # -- Confirmation 2 -----------------------------------------------------
    # Internal drives demand extra friction: the operator must type the exact
    # model name (not just DELETE), making an accidental internal-disk wipe far
    # harder than fat-fingering a USB stick.
    if not auto_yes:
        print()
        if device.is_internal and device.model:
            resp = input(_c(
                _Ansi.RED + _Ansi.BOLD,
                f"  INTERNAL DRIVE. Type the exact model name "
                f"('{device.model}') to confirm: ",
            )).strip()
            if resp != device.model:
                print("  Aborted.")
                sys.exit(0)
        else:
            resp = input(_c(_Ansi.RED, "  Type DELETE to confirm: ")).strip()
            if resp != "DELETE":
                print("  Aborted.")
                sys.exit(0)
        print()

    # -- Execute wipe -------------------------------------------------------
    handle: int | None = None
    volume_locks: list[int] = []
    wipe_result: WipeResult | None = None
    verify_result: VerifyResult | None = None
    hidden = None
    secure_erase_result = None

    try:
        # Re-check the disk, dismount every volume on it and keep them locked
        # until wipe + verify are done (else Windows may remount mid-wipe).
        _info("Checking drive and dismounting volumes...")
        try:
            volume_locks = prepare_target(device)
        except UnsafeTargetError as exc:
            _die(str(exc))

        # Open physical drive (no FSCTL_LOCK_VOLUME on the physical handle:
        # it is meant for volumes and deadlocks some USB controllers; the
        # volume locks above already give exclusive access)
        _info(f"Opening physical drive {device.device_id}...")
        handle = open_physical_drive(device.device_id)

        # Get drive size
        drive_size = get_drive_size(handle)
        _info(f"Drive size: {_format_size_short(drive_size)}")
        print()

        # Probe for hidden areas (HPA/DCO) — read-only. USB bridges report
        # unsupported; we never modify the drive here.
        hidden = detect_hidden_areas(handle)
        if hidden.supported and (hidden.hpa_present or hidden.dco_present):
            parts = []
            if hidden.hpa_present:
                parts.append(f"HPA {_format_size_short(hidden_bytes(hidden.hpa_hidden_sectors))}")
            if hidden.dco_present:
                parts.append(f"DCO {_format_size_short(hidden_bytes(hidden.dco_hidden_sectors))}")
            _warn(
                "Hidden area(s) detected (" + ", ".join(parts) + "). These are NOT "
                "reached by an overwrite wipe and may retain old data — physical "
                "destruction is recommended for high-sensitivity media. (Recorded "
                "on the certificate.)"
            )
            print()

        # Run the wipe — an overwrite method, or the hardware erase (Beta):
        # the drive erases itself, then one pass of zeros + verification.
        if is_secure_erase:
            print(_c(_Ansi.BOLD,
                     f"  Hardware-Löschung (Beta) / hardware erase (Beta) on {all_letters}...\n"))
            _info(
                f"Temporäres Laufwerks-Passwort / temporary drive password: "
                f"{hardware_erase.TEMP_PASSWORD} — PC nicht in den Standby schicken / "
                "do not let the PC sleep."
            )
            audit_log(
                f"CLI hardware erase started: device={device.drive_letter} "
                f"verify={verify_mode} operator={args.operator}"
            )
            try:
                outcome = hardware_erase.run(
                    handle, drive_size, wipe_method,
                    progress_callback=_progress_bar,
                    verify_progress_callback=_verify_progress_bar,
                    on_erase_started=_print_erase_started,
                    on_erase_finished=_print_erase_finished,
                )
            except KeyboardInterrupt:
                audit_log(f"CLI hardware erase interrupted (Ctrl+C): {device.device_id}")
                _die(_hardware_interrupted_message())
            secure_erase_result = outcome.erase
            if outcome.report.status != "erased":
                _die(_hardware_stop_message(outcome.report))
            wipe_result = outcome.wipe_result
        else:
            print(_c(_Ansi.BOLD, f"  Wiping {device.drive_letter} with {wipe_method.name}...\n"))
            audit_log(
                f"CLI wipe started: device={device.drive_letter} method={wipe_method.name} "
                f"operator={args.operator}"
            )

            verify_cb = _verify_progress_bar if verify_mode == "full" else None
            wipe_result = wipe_method.execute(
                handle=handle,
                drive_size=drive_size,
                block_size=1_048_576,
                progress_callback=_progress_bar,
                verify_mode=verify_mode,
                verify_progress_callback=verify_cb,
            )

        # Wipe/erase + verify done: let go of the volumes (a reformat needs
        # the disk free; the finally block covers every error path).
        locks, volume_locks = volume_locks, []
        release_volumes(locks)

        # Clear the progress bar line (covers both wipe and verify bars)
        sys.stdout.write("\r" + " " * shutil.get_terminal_size((80, 24)).columns + "\r")
        sys.stdout.flush()

        if not wipe_result.success:
            _die(f"Wipe failed: {wipe_result.error_message}")

        _success(f"Wipe completed successfully in {_format_duration(wipe_result.start_time, wipe_result.end_time)}.")
        if wipe_result.bad_sector_count > 0:
            _warn(
                f"{wipe_result.bad_sector_count} unwritable region(s) "
                f"({_format_size_short(wipe_result.bad_sector_bytes)}) were skipped "
                f"and recorded on the certificate. These sectors may still hold old "
                f"data — consider physical destruction for high-sensitivity media."
            )
        print()

        # -- Verification --------------------------------------------------
        verify_result = wipe_result.verify_result

        if verify_result is None:
            _info("Verification: skipped.")
        elif verify_result.method == "sample":
            if verify_result.success:
                _success(
                    f"Sample verification PASSED "
                    f"({verify_result.sectors_checked} sectors checked)."
                )
            else:
                _warn(
                    f"Sample verification FAILED "
                    f"({verify_result.sectors_matched}/{verify_result.sectors_checked} sectors matched). "
                    f"The drive may not be fully wiped."
                )
        elif verify_result.method == "full":
            gb = verify_result.bytes_verified / (1024 ** 3)
            if verify_result.success:
                _success(
                    f"Full verification PASSED ({gb:.2f} GB verified, "
                    f"pattern={verify_result.expected_pattern}, "
                    f"duration={verify_result.duration_seconds:.1f}s)."
                )
            else:
                offsets = ", ".join(
                    f"0x{o:08X}" for o in verify_result.mismatch_offsets[:10]
                )
                _warn(
                    f"Full verification FAILED: {verify_result.error_count} mismatches. "
                    f"First 10 offsets: {offsets}"
                )
        print()

    except OSError as exc:
        _die(f"Drive access error: {exc}")

    finally:
        if handle is not None:
            close_drive(handle)
        release_volumes(volume_locks)

    # -- Reformat (optional) ------------------------------------------------
    # Runs after the finally above: the physical handle is closed and every
    # volume lock released, so PowerShell's Clear-Disk gets the disk. The
    # wipe result is final here — a skipped or failed reformat only means
    # the certificate names no new file system.
    format_result = None
    if args.reformat != "none" and wipe_result.success:
        _info(f"Reformatting drive to {args.reformat.upper()}...")
        from wipe.format import reformat_drive
        # Extract the disk index from device_id like r"\\.\PhysicalDrive1" -> 1
        import re
        m = re.search(r"PhysicalDrive(\d+)", device.device_id)
        # Reformatting writes a new partition table: same gate as the wipe.
        # The check dismounts and locks the volumes again; Clear-Disk needs
        # the disk free, so the locks go before the reformat runs.
        try:
            release_volumes(prepare_target(device))
            unsafe = None
        except Exception as exc:  # noqa: BLE001 — only skips the reformat
            unsafe = exc
        if unsafe is not None:
            _warn(f"Reformat skipped / Formatieren übersprungen: {unsafe}")
        elif not m:
            _warn("Could not parse disk number; skipping reformat")
        else:
            disk_number = int(m.group(1))
            try:
                format_result = reformat_drive(
                    disk_number=disk_number,
                    filesystem=args.reformat,
                    label=args.reformat_label,
                    partition_style=args.reformat_partition,
                    progress_callback=lambda msg: _info(f"  {msg}"),
                )
            except Exception as exc:  # noqa: BLE001 — the wipe result stands
                audit_log(f"CLI reformat crashed for {device.device_id}: {exc}")
                _warn(f"Reformat failed / Formatieren fehlgeschlagen: {exc}")
                format_result = None
            if format_result is not None and format_result.success:
                _success(
                    f"Reformat completed: {format_result.filesystem} "
                    f"label={format_result.label!r} in {format_result.duration_seconds:.1f}s"
                )
            elif format_result is not None:
                _warn(f"Reformat failed: {format_result.error_message}")

    # -- Generate certificate -----------------------------------------------
    cert_number = get_next_cert_number()
    cert_date = datetime.now()

    # A hardware erase is named with its zero pass ("ATA Secure Erase + ZeroFill");
    # the level is the overwrite's.
    cert_method_name = wipe_result.method_name
    cert_sicherheitsstufe = wipe_method.sicherheitsstufe

    cert_kwargs = dict(
        cert_number=cert_number,
        date=cert_date,
        operator=args.operator,
        client_reference=client,
        asset_tag=asset_tag,
        device_model=device.model or "Unknown",
        device_manufacturer="",  # WMI model string typically includes manufacturer
        serial_number=device.serial_number or "N/A",
        capacity_bytes=device.capacity_bytes,
        filesystem=device.filesystem,
        connection_type=device.connection_type,
        wipe_method=cert_method_name,
        sicherheitsstufe=cert_sicherheitsstufe,
        schutzklasse=schutzklasse,
        passes=wipe_result.passes,  # actual total including zero-blank
        start_time=wipe_result.start_time,
        end_time=wipe_result.end_time,
        verification_passed=verify_result.success if verify_result else False,
        sectors_checked=verify_result.sectors_checked if verify_result else 0,
        verification_hash=verify_result.sample_hash if verify_result else "N/A (skipped)",
        company_name=config.company.name,
        company_address=config.company.address,
        company_logo_path=config.company.logo_path,
        language=language,
        verify_method=verify_result.method if verify_result else "none",
        verify_bytes=verify_result.bytes_verified if verify_result else 0,
        verify_pattern=verify_result.expected_pattern if verify_result else "",
        verify_error_count=verify_result.error_count if verify_result else 0,
        verify_mismatch_offsets=list(verify_result.mismatch_offsets) if verify_result else [],
        verify_duration_seconds=verify_result.duration_seconds if verify_result else 0.0,
        reformat_performed=bool(format_result and format_result.success),
        reformat_filesystem=(format_result.filesystem if format_result and format_result.success else ""),
        reformat_label=(format_result.label if format_result and format_result.success else ""),
        bad_sector_count=wipe_result.bad_sector_count,
        bad_sector_bytes=wipe_result.bad_sector_bytes,
        bad_sector_offsets=list(wipe_result.bad_sector_offsets),
        media_type=media,
        hidden_area_probed=bool(hidden is not None and hidden.supported),
        hpa_present=bool(hidden and hidden.hpa_present),
        hpa_hidden_bytes=(
            hidden_bytes(hidden.hpa_hidden_sectors)
            if hidden and hidden.hpa_present else 0
        ),
        dco_present=bool(hidden and hidden.dco_present),
        dco_hidden_bytes=(
            hidden_bytes(hidden.dco_hidden_sectors)
            if hidden and hidden.dco_present else 0
        ),
        secure_erase_used=is_secure_erase,
        secure_erase_method=(
            secure_erase_result.method if secure_erase_result else ""
        ),
        wipe_success=bool(wipe_result.success),
    )
    try:
        cert_data = CertificateData(**cert_kwargs)
    except TypeError:
        # Optional fields not present on an older CertificateData. Drop them and
        # retry so the certificate can still be generated.
        for _key in (
            "reformat_performed", "reformat_filesystem", "reformat_label",
            "bad_sector_count", "bad_sector_bytes", "bad_sector_offsets",
            "hidden_area_probed", "hpa_present", "hpa_hidden_bytes",
            "dco_present", "dco_hidden_bytes",
            "secure_erase_used", "secure_erase_method", "wipe_success", "media_type",
        ):
            cert_kwargs.pop(_key, None)
        cert_data = CertificateData(**cert_kwargs)

    # -- Log to the history first: its chain hash goes into the certificate ---
    duration_seconds = int((wipe_result.end_time - wipe_result.start_time).total_seconds())
    if hidden is not None and hidden.supported:
        _ha_parts = []
        if hidden.hpa_present:
            _ha_parts.append(f"HPA:{_format_size_short(hidden_bytes(hidden.hpa_hidden_sectors))}")
        if hidden.dco_present:
            _ha_parts.append(f"DCO:{_format_size_short(hidden_bytes(hidden.dco_hidden_sectors))}")
        hidden_area_str = "+".join(_ha_parts) if _ha_parts else "NONE"
    else:
        hidden_area_str = "n/a"
    history_row = {
        "date": cert_date.strftime("%Y-%m-%d %H:%M:%S"),
        "device_model": device.model or "Unknown",
        "serial_number": device.serial_number or "N/A",
        "capacity_bytes": str(device.capacity_bytes),
        "method": cert_method_name,
        "passes": str(wipe_result.passes),
        "operator": args.operator,
        "start_time": wipe_result.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": wipe_result.end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": str(duration_seconds),
        "result": history_result_value(result_status(cert_data)),
        "verification": (
            "SKIPPED" if verify_result is None else
            f"{verify_result.method.upper()}-{'PASSED' if verify_result.success else 'FAILED'}"
        ),
        "cert_number": str(cert_number),
        "reformat": format_result.filesystem if format_result and format_result.success else "NONE",
        "reformat_label": format_result.label if format_result and format_result.success else "",
        "bad_sectors": str(wipe_result.bad_sector_count),
        "hidden_area": hidden_area_str,
    }
    try:
        history_hash = log_wipe_to_csv(history_row)
    except (OSError, ValueError) as exc:
        _warn(f"Verlauf konnte nicht geschrieben werden / History could not be written: {exc}")
        history_hash = ""

    # -- Generate the signed certificate ------------------------------------
    cert_filename = f"SS-{cert_number:06d}_{device.serial_number or 'UNKNOWN'}_{cert_date.strftime('%Y%m%d')}.pdf"
    cert_path = str(Path(output_dir) / cert_filename)

    _info("Generating certificate...")
    try:
        abs_cert_path = generate_certificate(
            cert_data, cert_path, signing_key=signing_key, history_hash=history_hash
        )
        _success(f"Certificate saved: {abs_cert_path}")
    except Exception as exc:
        _warn(f"Certificate generation failed: {exc}")
        abs_cert_path = ""

    # -- Summary ------------------------------------------------------------
    print()
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "  === Wipe Summary ==="))
    print(f"    Device:          {device.drive_letter} ({device.model or 'Unknown'})")
    print(f"    Serial:          {device.serial_number or 'N/A'}")
    print(f"    Method:          {cert_method_name} ({wipe_result.passes} passes)")
    print(f"    Duration:        {_format_duration(wipe_result.start_time, wipe_result.end_time)}")
    exit_code, result_line = wipe_outcome(cert_data)
    label, _, value = result_line.partition(": ")
    colour = _RESULT_COLOUR[result_status(cert_data)]
    print(f"    {label}: {_c(colour + _Ansi.BOLD, value)}")
    print(f"    NIST SP 800-88:  {nist_category(cert_data)}")

    if verify_result:
        if verify_result.success:
            v_status = _c(
                _Ansi.GREEN + _Ansi.BOLD,
                f"{verify_result.method.upper()} PASSED",
            )
        else:
            v_status = _c(
                _Ansi.RED + _Ansi.BOLD,
                f"{verify_result.method.upper()} FAILED",
            )
        print(f"    Verification:    {v_status}")
    else:
        print(f"    Verification:    {_c(_Ansi.YELLOW, 'SKIPPED')}")

    if format_result:
        if format_result.success:
            print(f"    Reformat:        {_c(_Ansi.GREEN, format_result.filesystem)} "
                  f"(label: {format_result.label})")
        else:
            print(f"    Reformat:        {_c(_Ansi.RED, 'FAILED')}")

    if wipe_result.bad_sector_count > 0:
        print(
            f"    Bad Sectors:     "
            f"{_c(_Ansi.YELLOW + _Ansi.BOLD, str(wipe_result.bad_sector_count) + ' region(s)')} "
            f"({_format_size_short(wipe_result.bad_sector_bytes)})"
        )

    print(f"    Certificate:     SS-{cert_number:06d}")
    if abs_cert_path:
        print(f"    Certificate PDF: {abs_cert_path}")
        print(f"    Signed by key:   {signing_key.fingerprint()}")
    print(f"    Operator:        {args.operator}")
    print()
    if exit_code:
        sys.exit(exit_code)


# -- Command: history -------------------------------------------------------

def cmd_history(args: argparse.Namespace) -> None:
    """Handle the 'history' command."""
    export_file: str | None = getattr(args, "export", None)

    if getattr(args, "verify", False):
        _verify_history()
        return

    history = read_wipe_history()

    if export_file:
        # Export to CSV
        if not history:
            _warn("No wipe history to export.")
            return
        try:
            dest = Path(export_file)
            dest.parent.mkdir(parents=True, exist_ok=True)
            from core.log import CSV_HEADERS
            with open(dest, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
                writer.writeheader()
                writer.writerows(history)
            _success(f"History exported to {dest.resolve()}")
        except OSError as exc:
            _die(f"Failed to export history: {exc}")
        return

    # Print to terminal
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Wipe History"))
    print()

    if not history:
        _info("No wipe history found.")
        print()
        return

    # Table headers
    headers = ["Date", "Model", "Serial", "Method", "Passes", "Result", "Verify", "Cert #", "Operator"]
    widths = [20, 22, 16, 16, 7, 8, 14, 10, 16]

    header_line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(f"  {_c(_Ansi.BOLD, header_line)}")
    print(f"  {'=' * sum(widths + [2 * (len(widths) - 1)])}")

    for entry in history:
        result_raw = entry.get("result", "")
        verify_raw = entry.get("verification", "")

        # Three-level values (OK/LIMITED/FAILED); legacy SUCCESS shows as OK.
        status = history_result_status(result_raw)
        if status is not None:
            result_raw = history_result_value(status)
            result_display = _c(_RESULT_COLOUR[status], result_raw)
        else:
            result_display = result_raw

        if "PASSED" in verify_raw:
            verify_display = _c(_Ansi.GREEN, verify_raw)
        elif "FAILED" in verify_raw:
            verify_display = _c(_Ansi.RED, verify_raw)
        elif verify_raw == "SKIPPED":
            verify_display = _c(_Ansi.YELLOW, "SKIPPED")
        else:
            verify_display = verify_raw

        cells = [
            _truncate(entry.get("date", ""), widths[0]),
            _truncate(entry.get("device_model", ""), widths[1]),
            _truncate(entry.get("serial_number", ""), widths[2]),
            _truncate(entry.get("method", ""), widths[3]),
            _truncate(entry.get("passes", ""), widths[4]),
        ]
        plain_parts = [cell.ljust(w) for cell, w in zip(cells, widths[:5])]

        # Result and verify columns need special handling for ANSI padding
        result_padded = result_raw.ljust(widths[5]) if not _Ansi.enabled() else result_display + " " * max(0, widths[5] - len(result_raw))
        verify_padded = verify_raw.ljust(widths[6]) if not _Ansi.enabled() else verify_display + " " * max(0, widths[6] - len(verify_raw))

        cert_num = _truncate(entry.get("cert_number", ""), widths[7])
        operator = _truncate(entry.get("operator", ""), widths[8])

        line = "  ".join(plain_parts) + "  " + result_padded + "  " + verify_padded + "  " + cert_num.ljust(widths[7]) + "  " + operator.ljust(widths[8])
        print(f"  {line}")

    print(f"\n  {len(history)} record(s) total.\n")


def _cert_label(value: object) -> str:
    try:
        return f"SS-{int(str(value)):06d}"
    except ValueError:
        return _printable(value)


def _verify_history() -> None:
    """``history --verify``: exit 0 when the chain is intact, 1 at the first break."""
    check = verify_wipe_history()
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Verlauf prüfen / Verify history"))
    print()
    if check.total == 0:
        _info("Kein Verlauf vorhanden / No history found.")
        print()
        return
    if check.legacy:
        _info(
            f"{check.legacy} ältere(r) Eintrag/Einträge ohne Prüfsumme (vor Version 2.0) "
            f"übersprungen / older entr(y/ies) without checksum (before 2.0) skipped."
        )
    if check.ok:
        _success(
            f"Verlauf unverändert / History unchanged — {check.checked} "
            f"Eintrag/Einträge geprüft / entr(y/ies) checked."
        )
        print()
        return
    row = check.broken_entry or {}
    print(_c(
        _Ansi.RED + _Ansi.BOLD,
        f"  Verlauf verändert / History changed — Eintrag / entry {check.broken_row} "
        f"von / of {check.total}",
    ))
    print(f"    Datum / Date:              {_printable(row.get('date') or '-')}")
    print(f"    Zertifikat / Certificate:  {_cert_label(row.get('cert_number') or '-')}")
    print(f"    Seriennummer / Serial:     {_printable(row.get('serial_number') or '-')}")
    status = history_result_status(row.get("result"))
    if status is not None:
        de, en = RESULT_LABELS[status]
        print(f"    Ergebnis / Result:         {de} / {en}")
    print(f"    Grund / Reason:            {check.reason}")
    print()
    sys.exit(1)


# -- Command: verify-cert ---------------------------------------------------

_VERIFY_EXIT = {"trusted": 0, "unknown_key": 1, "invalid": 2}
_VERIFY_MODE_TEXT = {
    "none": "Nicht durchgeführt / Skipped",
    "sample": "Stichprobe / Sample",
    "full": "Vollständig / Full",
}


def _payload_value(key: str, value: object) -> str:
    if key == "n":
        return _cert_label(value)
    if key == "dc" and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return f"{format_capacity(value)} ({value:,} Bytes)".replace(",", ".")
    if key == "r" and value in RESULT_LABELS:
        de, en = RESULT_LABELS[value]
        return f"{de} / {en}"
    if key == "vm" and value in _VERIFY_MODE_TEXT:
        return _VERIFY_MODE_TEXT[value]
    if key == "h" and not value:
        return "-"
    return _printable(value)


def cmd_verify_cert(args: argparse.Namespace) -> None:
    """Check a certificate PDF's signature. Exit 0 trusted, 1 unknown key, 2 invalid."""
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Zertifikat prüfen / Verify certificate"))
    print()
    try:
        pdf_bytes = Path(args.pdf).read_bytes()
    except OSError as exc:
        print(_c(_Ansi.RED + _Ansi.BOLD, f"  Datei nicht lesbar / Cannot read file: {exc}"))
        sys.exit(2)

    token = extract_token_from_pdf(pdf_bytes)
    if token is None:
        print(_c(_Ansi.RED + _Ansi.BOLD, "  Ungültig oder verändert / Invalid or altered"))
        _info(
            "Kein Prüfcode im PDF gefunden — das Zertifikat ist nicht signiert oder kein "
            "StickShredder-Zertifikat. / No verification code found in the PDF — the "
            "certificate is unsigned or not a StickShredder certificate."
        )
        print()
        sys.exit(2)

    try:
        trusted = TrustStore.load(settings.TRUST_FILE).as_dict()
    except ValueError as exc:
        _warn(f"{exc} — es wird ohne vertraute Schlüssel geprüft / checking without trusted keys.")
        trusted = {}

    outcome = verify_token(token, trusted)
    if outcome.status == "trusted":
        print(_c(_Ansi.GREEN + _Ansi.BOLD, f"  {_printable(outcome.reason)}"))
    elif outcome.status == "unknown_key":
        print(_c(_Ansi.YELLOW + _Ansi.BOLD, f"  {outcome.reason}"))
    else:
        print(_c(_Ansi.RED + _Ansi.BOLD, "  Ungültig oder verändert / Invalid or altered"))
        _info(f"Grund / Reason: {outcome.reason}")

    rows: list[tuple[str, str]] = []
    if outcome.fingerprint:
        rows.append(("Fingerabdruck / Fingerprint", outcome.fingerprint))
    for key, (de, en) in PAYLOAD_LABELS.items():
        if outcome.payload and key not in ("k", "v") and key in outcome.payload:
            rows.append((f"{de} / {en}", _payload_value(key, outcome.payload[key])))
    if rows:
        width = max(len(label) for label, _ in rows)
        print()
        for label, value in rows:
            print(f"    {label:<{width}}  {value}")
    print()
    sys.exit(_VERIFY_EXIT[outcome.status])


# -- Command: keycard -------------------------------------------------------

def cmd_keycard(args: argparse.Namespace) -> None:
    """Write the key card PDF for this installation's signing key."""
    from cert.keycard import generate_keycard

    config = AppConfig.load()
    key = _load_key_or_die()
    language = args.language or config.cert_language or "de"
    if args.output:
        output = Path(args.output)
    else:
        prefix = key.fingerprint()[:9]
        output = Path(config.cert_output_dir) / f"StickShredder-Schluesselkarte_{prefix}.pdf"
    if not config.company.name:
        _warn(
            "Kein Firmenname eingestellt — er fehlt auf der Karte / No company name set — "
            "it is missing on the card."
        )
    try:
        path = generate_keycard(key, config.company.name, output, language=language)
    except (OSError, ValueError) as exc:
        _die(f"Schlüsselkarte konnte nicht erstellt werden / Key card could not be created: {exc}")
    _success(f"Schlüsselkarte gespeichert / Key card saved: {Path(path).resolve()}")
    _info(f"Fingerabdruck / Fingerprint: {key.fingerprint()}")
    print()


# -- Command: trust ---------------------------------------------------------

def _key_from_source(source: str) -> tuple[bytes, str]:
    """Public key (and a suggested name) from a key-card URL, key-card PDF or base64url key."""
    text = source.strip()
    candidate = Path(text)
    if text.lower().endswith(".pdf") or candidate.is_file():
        try:
            public_key = extract_key_from_keycard_pdf(candidate.read_bytes())
        except OSError as exc:
            raise ValueError(f"Datei nicht lesbar / Cannot read file: {exc}") from exc
        if public_key is None:
            raise ValueError(
                "Keine Schlüsselkarte von StickShredder / Not a StickShredder key card"
            )
        return public_key, ""
    name = ""
    key_text = text
    if "#" in text:
        params = parse_qs(text.split("#", 1)[1], keep_blank_values=True)
        key_text = (params.get("key") or [""])[0]
        name = (params.get("name") or [""])[0].strip()
    try:
        public_key = b64url_decode(key_text)
    except ValueError:
        public_key = b""
    if len(public_key) != 32:
        raise ValueError(
            "Kein gültiger Schlüssel (Schlüsselkarten-Link, Schlüsselkarte als PDF oder "
            "Base64url-Schlüssel erwartet) / Not a valid key (expected a key-card link, a "
            "key-card PDF or a base64url key)"
        )
    return public_key, name


def cmd_trust(args: argparse.Namespace) -> None:
    """Manage the list of trusted signing keys."""
    store = _load_trust_store_or_die()

    if args.trust_command == "add":
        try:
            public_key, suggested = _key_from_source(args.source)
        except ValueError as exc:
            _die(str(exc))
        name = (args.name or suggested).strip()
        if not name:
            _die("Bitte einen Namen angeben: --name NAME / Please give a name: --name NAME")
        fingerprint = store.add(public_key, name)
        _success(f"Vertrauter Schlüssel gespeichert / Trusted key saved: {_printable(name)}")
        _info(f"Fingerabdruck / Fingerprint: {fingerprint}")
        _info(
            "Fingerabdruck mit der Schlüsselkarte vergleichen / Compare the fingerprint "
            "with the key card."
        )
        return

    if args.trust_command == "remove":
        if not store.remove(args.fingerprint):
            _die(
                f"Schlüssel nicht gefunden / Key not found: {_printable(args.fingerprint)}"
            )
        _success(f"Schlüssel entfernt / Key removed: {args.fingerprint.strip().upper()}")
        return

    # list
    trusted = store.as_dict()
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Vertraute Schlüssel / Trusted keys"))
    print()
    if not trusted:
        _info("Keine vertrauten Schlüssel / No trusted keys.")
    for fingerprint, name in trusted.items():
        print(f"    {fingerprint}  {_printable(name)}")
    print()


# -- Command: key -----------------------------------------------------------

def cmd_key(args: argparse.Namespace) -> None:
    """Export or import this installation's signing key (password protected)."""
    path = Path(args.path)
    if args.key_command == "export":
        if path.exists():
            _die(f"Datei existiert bereits / File already exists: {path}")
        key = _load_key_or_die()
        password = getpass.getpass("  Passwort für die Exportdatei / Password for the export file: ")
        if not password:
            _die("Passwort darf nicht leer sein / Password must not be empty")
        if getpass.getpass("  Passwort wiederholen / Repeat password: ") != password:
            _die("Passwörter stimmen nicht überein / Passwords do not match")
        if len(password) < 10:
            _warn("Kurzes Passwort — mindestens 10 Zeichen empfohlen / Short password — "
                  "at least 10 characters recommended.")
        try:
            key.export_protected(path, password)
        except (OSError, ValueError) as exc:
            _die(f"Export fehlgeschlagen / Export failed: {exc}")
        audit_log(f"Signing key exported: fingerprint={key.fingerprint()} path={path.resolve()}")
        _success(f"Schlüssel exportiert / Key exported: {path.resolve()}")
        _info(f"Fingerabdruck / Fingerprint: {key.fingerprint()}")
        _warn("Die Datei enthält den privaten Schlüssel — sicher aufbewahren / The file "
              "contains the private key — keep it safe.")
        return

    # import
    password = getpass.getpass("  Passwort der Schlüsseldatei / Password of the key file: ")
    try:
        key = SigningKey.import_protected(path, password, settings.KEY_DIR)
    except SigningKeyError as exc:
        _die(str(exc))
    settings.trust_own_key(key)
    audit_log(f"Signing key imported: fingerprint={key.fingerprint()} path={path.resolve()}")
    _success("Schlüssel importiert / Key imported.")
    _info(f"Fingerabdruck / Fingerprint: {key.fingerprint()}")
    _info("Ein vorheriger Schlüssel wurde als signing_key.<Zeit>.bak gesichert / A previous "
          "key was kept as signing_key.<time>.bak.")


# -- Argument parser --------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="stickshredder",
        description="StickShredder -- Secure USB wipe tool with DIN 66399 certificates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            '  stickshredder list\n'
            '  stickshredder info --device E:\n'
            '  stickshredder wipe --device E: --operator "Robin Oertel"\n'
            '  stickshredder wipe --device E: --method standard --operator "Robin Oertel"\n'
            '  stickshredder wipe --device E: --method bsi --schutzklasse 3 --operator "Robin" --client "ACME GmbH"\n'
            '  stickshredder wipe --device E: --method custom --passes 5 --pattern random --operator "Robin"\n'
            '  stickshredder history\n'
            '  stickshredder history --export out.csv\n'
            '  stickshredder history --verify\n'
            '  stickshredder verify-cert SS-000042_DEMO00000001_20261117.pdf\n'
            '  stickshredder keycard --language both\n'
            '  stickshredder trust add "https://rctom16-bit.github.io/stickshredder/verify/#key=...&name=..."\n'
            '  stickshredder trust list\n'
            '  stickshredder key export D:\\backup\\stickshredder-key.pem\n'
        ),
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── list ──────────────────────────────────────────────────────────────
    sub_list = subparsers.add_parser(
        "list",
        help="List detected storage devices",
        description="Scan and display all detected storage devices with safety status.",
    )
    sub_list.set_defaults(func=cmd_list)

    # ── wipe ──────────────────────────────────────────────────────────────
    sub_wipe = subparsers.add_parser(
        "wipe",
        help="Securely wipe a device",
        description="Securely wipe a USB or storage device and generate a DIN 66399 certificate.",
    )
    sub_wipe.add_argument(
        "--device",
        required=True,
        metavar="DRIVE",
        help="Drive letter of the device to wipe (e.g. E: or F:)",
    )
    sub_wipe.add_argument(
        "--method",
        default=None,
        choices=["zero", "standard", "bsi", "custom", "secure-erase"],
        help="Wipe method (default: the recommendation for this device, see "
             "'stickshredder info'): zero (1-pass zeros), standard (3-pass random), "
             "bsi (7-pass BSI-VSITR), custom, or secure-erase (hardware erase, "
             "Beta: the drive's own ATA erase, then 1 pass of zeros + --verify; "
             "requires --experimental). standard and bsi only "
             "if a policy requires them — they add no security on today's media.",
    )
    sub_wipe.add_argument(
        "--operator",
        required=True,
        metavar="NAME",
        help="Operator name for the deletion certificate",
    )
    sub_wipe.add_argument(
        "--schutzklasse",
        type=int,
        choices=[1, 2, 3],
        default=2,
        metavar="N",
        help="DIN 66399 Schutzklasse / protection class (1, 2, or 3; default: 2)",
    )
    sub_wipe.add_argument(
        "--client",
        default="",
        metavar="REF",
        help="Client reference for the certificate (optional)",
    )
    sub_wipe.add_argument(
        "--asset-tag",
        default="",
        metavar="TAG",
        help="Asset tag or ticket number (optional)",
    )
    sub_wipe.add_argument(
        "--passes",
        type=int,
        default=3,
        metavar="N",
        help="Number of passes for custom method (default: 3)",
    )
    sub_wipe.add_argument(
        "--pattern",
        choices=["zero", "ones", "random"],
        default="random",
        help="Fill pattern for custom method (default: random)",
    )
    sub_wipe.add_argument(
        "--output-dir",
        default="",
        metavar="DIR",
        help="Certificate output directory (default: from config)",
    )
    sub_wipe.add_argument(
        "--verify",
        choices=["none", "sample", "full"],
        default="full",
        help=(
            "Verification mode after wiping (default: full). "
            "'none' skips verification. "
            "'sample' checks 100 random sectors (fast, probabilistic). "
            "'full' reads every sector and compares against the expected pattern "
            "(slow -- roughly doubles runtime -- but detects silent sector failures). "
            "Random-data methods (standard, bsi, custom-random) add a final zero-blanking "
            "pass automatically when verification is enabled."
        ),
    )
    sub_wipe.add_argument(
        "--language",
        choices=["de", "en", "both"],
        default="",
        metavar="LANG",
        help="Certificate language: de, en, or both (default: from config)",
    )
    sub_wipe.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip all confirmation prompts (for scripting)",
    )
    sub_wipe.add_argument(
        "--allow-internal",
        action="store_true",
        help="Allow wiping internal (non-removable) drives. Off by default. "
             "The Windows system disk is ALWAYS refused regardless of this flag. "
             "Internal drives are also refused if the system disk cannot be "
             "identified (fail-safe).",
    )
    sub_wipe.add_argument(
        "--experimental",
        action="store_true",
        help="Acknowledge experimental, hardware-untested features "
             "(required for --method secure-erase).",
    )
    sub_wipe.add_argument(
        "--enhanced",
        action="store_true",
        help="Kept for compatibility: --method secure-erase uses the enhanced "
             "erase automatically when the drive offers it.",
    )
    sub_wipe.add_argument(
        "--reformat",
        choices=["none", "fat32", "exfat", "ntfs"],
        default="none",
        help=(
            "Reformat the drive after wiping so it's immediately usable "
            "(default: none). 'fat32' caps at 32 GB, 'exfat' recommended for "
            "modern USB, 'ntfs' for Windows-only use."
        ),
    )
    sub_wipe.add_argument(
        "--reformat-label",
        default="USB",
        metavar="LABEL",
        help="Volume label when --reformat is set (default: USB). "
             "Max 11 chars for FAT32.",
    )
    sub_wipe.add_argument(
        "--reformat-partition",
        choices=["MBR", "GPT"],
        default="MBR",
        help="Partition style when --reformat is set (default: MBR).",
    )
    sub_wipe.set_defaults(func=cmd_wipe)

    # ── info ──────────────────────────────────────────────────────────────
    sub_info = subparsers.add_parser(
        "info",
        help="Show a device and the recommended wipe procedure",
        description="Show model, type and status of a device and the recommended "
                    "procedure (method, verification, NIST SP 800-88 category). Reads "
                    "Windows' device information only; the disk is not opened.",
    )
    sub_info.add_argument(
        "--device", required=True, metavar="DRIVE",
        help="Drive letter (e.g. F:) or disk number (e.g. 2 or PhysicalDrive2)",
    )
    sub_info.add_argument(
        "--schutzklasse", type=int, choices=[1, 2, 3], default=0, metavar="N",
        help="DIN 66399 protection class for the recommendation (default: from config)",
    )
    sub_info.set_defaults(func=cmd_info)

    # ── unlock ────────────────────────────────────────────────────────────
    sub_unlock = subparsers.add_parser(
        "unlock",
        help="Remove the temporary password an interrupted hardware erase left (Beta)",
        description="Gesperrte Platte entsperren: removes StickShredder's temporary drive "
                    "password (SECURITY UNLOCK + DISABLE PASSWORD) from a SATA drive an "
                    "interrupted hardware erase left locked. One try only.",
    )
    sub_unlock.add_argument(
        "--device", required=True, metavar="DRIVE",
        help="Drive letter (e.g. F:) or disk number (e.g. 2 or PhysicalDrive2)",
    )
    sub_unlock.add_argument(
        "--experimental", action="store_true",
        help="Acknowledge this Beta feature (required).",
    )
    sub_unlock.add_argument(
        "--allow-internal", action="store_true",
        help="Allow internal drives (the Windows system disk is always refused).",
    )
    sub_unlock.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the confirmation prompt",
    )
    sub_unlock.set_defaults(func=cmd_unlock)

    # ── history ───────────────────────────────────────────────────────────
    sub_history = subparsers.add_parser(
        "history",
        help="Show wipe history",
        description="Display or export the log of all past wipe operations.",
    )
    sub_history.add_argument(
        "--export",
        metavar="FILE",
        help="Export wipe history to a CSV file",
    )
    sub_history.add_argument(
        "--verify",
        action="store_true",
        help="Check the history's hash chain for changed, removed or inserted entries "
             "(exit 0 unchanged, 1 changed)",
    )
    sub_history.set_defaults(func=cmd_history)

    # ── verify-cert ───────────────────────────────────────────────────────
    sub_verify = subparsers.add_parser(
        "verify-cert",
        help="Check the signature of a certificate PDF",
        description="Check a signed deletion certificate. Exit code 0: genuine and key "
                    "trusted; 1: signature valid but key unknown (compare the fingerprint "
                    "with the key card); 2: invalid, altered or unsigned.",
    )
    sub_verify.add_argument("pdf", metavar="PDF", help="Certificate PDF file")
    sub_verify.set_defaults(func=cmd_verify_cert)

    # ── keycard ───────────────────────────────────────────────────────────
    sub_keycard = subparsers.add_parser(
        "keycard",
        help="Create the key card PDF for this installation's signing key",
        description="Create the one-page key card (company, fingerprint, QR code) to file "
                    "with the IT documentation or hand to the auditor.",
    )
    sub_keycard.add_argument(
        "--output", metavar="PATH", default="",
        help="Output PDF (default: certificate folder from config)",
    )
    sub_keycard.add_argument(
        "--language", choices=["de", "en", "both"], default="",
        help="Card language: de, en or both (default: certificate language from config)",
    )
    sub_keycard.set_defaults(func=cmd_keycard)

    # ── trust ─────────────────────────────────────────────────────────────
    sub_trust = subparsers.add_parser(
        "trust",
        help="Manage trusted signing keys",
        description="Keys in this list make certificates verify as genuine.",
    )
    trust_sub = sub_trust.add_subparsers(
        dest="trust_command", metavar="{add,list,remove}", required=True,
    )
    trust_add = trust_sub.add_parser(
        "add", help="Trust a key from a key-card link, key-card PDF or base64url key",
    )
    trust_add.add_argument(
        "source", metavar="KEY",
        help="Key-card URL (…/verify/#key=…&name=…), key-card PDF or base64url public key",
    )
    trust_add.add_argument(
        "--name", default="", metavar="NAME",
        help="Display name (default: the name in the key-card link)",
    )
    trust_sub.add_parser("list", help="List trusted keys")
    trust_remove = trust_sub.add_parser("remove", help="Remove a trusted key")
    trust_remove.add_argument("fingerprint", metavar="FINGERPRINT")
    sub_trust.set_defaults(func=cmd_trust)

    # ── key ───────────────────────────────────────────────────────────────
    sub_key = subparsers.add_parser(
        "key",
        help="Export or import the signing key (password protected)",
        description="Move this installation's signing key to another PC so both sign "
                    "with the same key. The password is asked for interactively.",
    )
    key_sub = sub_key.add_subparsers(
        dest="key_command", metavar="{export,import}", required=True,
    )
    key_export = key_sub.add_parser("export", help="Write the key to a password-protected file")
    key_export.add_argument("path", metavar="PATH")
    key_import = key_sub.add_parser(
        "import", help="Replace this installation's key with an exported one",
    )
    key_import.add_argument("path", metavar="PATH")
    sub_key.set_defaults(func=cmd_key)

    return parser


# -- Entry point ------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    # Redirected output uses the ANSI code page on Windows; replace characters
    # it cannot encode (e.g. in a certificate's company name) instead of crashing.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    setup_logging()

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
