"""Command-line interface for StickShredder — Windows USB wipe tool."""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from cert.generator import CertificateData, format_capacity, generate_certificate
from core.config import AppConfig, get_next_cert_number, DEFAULT_CERT_OUTPUT
from core.log import audit_log, log_wipe_to_csv, read_wipe_history, setup_logging
from wipe.device import DeviceInfo, list_devices, open_physical_drive, close_drive, get_drive_size, lock_volume, unlock_volume, dismount_volume
from wipe.methods import ZeroFill, RandomThreePass, BsiVsitr, CustomWipe, WipeResult
from wipe.verify import VerifyResult


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
            dev.drive_letter,
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


def _find_device(drive_letter: str, devices: list[DeviceInfo]) -> DeviceInfo | None:
    """Find a DeviceInfo by drive letter (case-insensitive, with or without colon)."""
    target = drive_letter.upper().rstrip(":")
    for dev in devices:
        if dev.drive_letter.upper().rstrip(":") == target:
            return dev
    return None


# -- Command: list ----------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> None:
    """Handle the 'list' command."""
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "\n  StickShredder -- Detected Devices"))
    print(_c(_Ansi.DIM, "  Scanning devices...\n"))

    devices = list_devices()
    _print_device_table(devices)

    removable = [d for d in devices if d.safe_to_wipe and d.is_removable]
    if removable:
        _info(f"Found {len(removable)} removable device(s) ready for wiping.")
    else:
        _warn("No safe removable devices found.")


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
    if device.is_system_drive:
        _die(
            f"Device {device.drive_letter} is the SYSTEM drive. "
            "Wiping the system drive is not allowed."
        )

    if device.has_bitlocker:
        _die(
            f"Device {device.drive_letter} is BitLocker-encrypted. "
            "Decrypt or remove BitLocker protection before wiping."
        )

    # -- Resolve method and parameters --------------------------------------
    wipe_method = _resolve_wipe_method(args)
    schutzklasse: int = getattr(args, "schutzklasse", 2) or 2
    client: str = getattr(args, "client", "") or ""
    asset_tag: str = getattr(args, "asset_tag", "") or ""
    output_dir: str = getattr(args, "output_dir", "") or config.cert_output_dir
    language: str = getattr(args, "language", "") or config.cert_language
    verify_mode: str = getattr(args, "verify", "sample") or "sample"
    auto_yes: bool = getattr(args, "yes", False)

    # -- Display device details ---------------------------------------------
    print(_c(_Ansi.BOLD, "  Device Details:"))
    print(f"    Drive Letter:    {device.drive_letter}")
    print(f"    Model:           {device.model or 'Unknown'}")
    print(f"    Serial:          {device.serial_number or 'N/A'}")
    print(f"    Capacity:        {_format_size_short(device.capacity_bytes)} ({device.capacity_bytes:,} bytes)")
    print(f"    Filesystem:      {device.filesystem}")
    print(f"    Connection:      {device.connection_type}")
    print(f"    Removable:       {'Yes' if device.is_removable else 'No'}")
    print()
    print(f"    Wipe Method:     {wipe_method.name} ({wipe_method.passes} pass{'es' if wipe_method.passes != 1 else ''})")
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

    # -- Confirmation 1 -----------------------------------------------------
    if not auto_yes:
        print(_c(_Ansi.RED + _Ansi.BOLD,
               f"  ALL DATA on {device.drive_letter} ({device.model or 'Unknown'}) "
               f"will be PERMANENTLY DESTROYED."))
        print()
        resp = input("  Are you sure? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("  Aborted.")
            sys.exit(0)

    # -- Confirmation 2 -----------------------------------------------------
    if not auto_yes:
        print()
        resp = input(_c(_Ansi.RED, "  Type DELETE to confirm: ")).strip()
        if resp != "DELETE":
            print("  Aborted.")
            sys.exit(0)
        print()

    # -- Execute wipe -------------------------------------------------------
    handle: int | None = None
    wipe_result: WipeResult | None = None
    verify_result: VerifyResult | None = None

    try:
        # Dismount the volume first
        _info("Dismounting volume...")
        try:
            dismount_volume(device.drive_letter)
        except OSError as exc:
            _warn(f"Dismount failed: {exc}")
            _info("Attempting to continue...")

        # Open physical drive
        _info(f"Opening physical drive {device.device_id}...")
        handle = open_physical_drive(device.device_id)

        # Lock volume
        _info("Locking volume...")
        try:
            lock_volume(handle)
        except OSError as exc:
            _warn(f"Lock failed (non-fatal): {exc}")

        # Get drive size
        drive_size = get_drive_size(handle)
        _info(f"Drive size: {_format_size_short(drive_size)}")
        print()

        # Run the wipe
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

        # Clear the progress bar line (covers both wipe and verify bars)
        sys.stdout.write("\r" + " " * shutil.get_terminal_size((80, 24)).columns + "\r")
        sys.stdout.flush()

        if not wipe_result.success:
            _die(f"Wipe failed: {wipe_result.error_message}")

        _success(f"Wipe completed successfully in {_format_duration(wipe_result.start_time, wipe_result.end_time)}.")
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

        # -- Unlock --------------------------------------------------------
        try:
            unlock_volume(handle)
        except OSError:
            pass

    except OSError as exc:
        _die(f"Drive access error: {exc}")

    finally:
        if handle is not None:
            close_drive(handle)

    # -- Reformat (optional) ------------------------------------------------
    if args.reformat != "none" and wipe_result.success:
        _info(f"Reformatting drive to {args.reformat.upper()}...")
        from wipe.format import reformat_drive
        # Extract the disk index from device_id like r"\\.\PhysicalDrive1" -> 1
        import re
        m = re.search(r"PhysicalDrive(\d+)", device.device_id)
        if not m:
            _warn("Could not parse disk number; skipping reformat")
            format_result = None
        else:
            disk_number = int(m.group(1))
            format_result = reformat_drive(
                disk_number=disk_number,
                filesystem=args.reformat,
                label=args.reformat_label,
                partition_style=args.reformat_partition,
                progress_callback=lambda msg: _info(f"  {msg}"),
            )
            if format_result.success:
                _success(
                    f"Reformat completed: {format_result.filesystem} "
                    f"label={format_result.label!r} in {format_result.duration_seconds:.1f}s"
                )
            else:
                _warn(f"Reformat failed: {format_result.error_message}")
    else:
        format_result = None

    # -- Generate certificate -----------------------------------------------
    cert_number = get_next_cert_number()
    cert_date = datetime.now()

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
        wipe_method=wipe_method.name,
        sicherheitsstufe=wipe_method.sicherheitsstufe,
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
    )
    try:
        cert_data = CertificateData(**cert_kwargs)
    except TypeError:
        # Reformat fields not yet present on CertificateData (agent F not landed).
        # Drop them and retry so the certificate can still be generated.
        for _key in ("reformat_performed", "reformat_filesystem", "reformat_label"):
            cert_kwargs.pop(_key, None)
        cert_data = CertificateData(**cert_kwargs)

    cert_filename = f"SS-{cert_number:06d}_{device.serial_number or 'UNKNOWN'}_{cert_date.strftime('%Y%m%d')}.pdf"
    cert_path = str(Path(output_dir) / cert_filename)

    _info("Generating certificate...")
    try:
        abs_cert_path = generate_certificate(cert_data, cert_path)
        _success(f"Certificate saved: {abs_cert_path}")
    except Exception as exc:
        _warn(f"Certificate generation failed: {exc}")
        abs_cert_path = ""

    # -- Log to CSV ---------------------------------------------------------
    duration_seconds = int((wipe_result.end_time - wipe_result.start_time).total_seconds())
    log_wipe_to_csv({
        "date": cert_date.strftime("%Y-%m-%d %H:%M:%S"),
        "device_model": device.model or "Unknown",
        "serial_number": device.serial_number or "N/A",
        "capacity_bytes": str(device.capacity_bytes),
        "method": wipe_method.name,
        "passes": str(wipe_result.passes),
        "operator": args.operator,
        "start_time": wipe_result.start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": wipe_result.end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": str(duration_seconds),
        "result": "SUCCESS" if wipe_result.success else "FAILED",
        "verification": (
            "SKIPPED" if verify_result is None else
            f"{verify_result.method.upper()}-{'PASSED' if verify_result.success else 'FAILED'}"
        ),
        "cert_number": str(cert_number),
        "reformat": format_result.filesystem if format_result and format_result.success else "NONE",
        "reformat_label": format_result.label if format_result and format_result.success else "",
    })

    # -- Summary ------------------------------------------------------------
    print()
    print(_c(_Ansi.BOLD + _Ansi.CYAN, "  === Wipe Summary ==="))
    print(f"    Device:          {device.drive_letter} ({device.model or 'Unknown'})")
    print(f"    Serial:          {device.serial_number or 'N/A'}")
    print(f"    Method:          {wipe_method.name} ({wipe_result.passes} passes)")
    print(f"    Duration:        {_format_duration(wipe_result.start_time, wipe_result.end_time)}")
    print(f"    Result:          {_c(_Ansi.GREEN + _Ansi.BOLD, 'SUCCESS') if wipe_result.success else _c(_Ansi.RED + _Ansi.BOLD, 'FAILED')}")

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

    print(f"    Certificate:     SS-{cert_number:06d}")
    if abs_cert_path:
        print(f"    Certificate PDF: {abs_cert_path}")
    print(f"    Operator:        {args.operator}")
    print()


# -- Command: history -------------------------------------------------------

def cmd_history(args: argparse.Namespace) -> None:
    """Handle the 'history' command."""
    export_file: str | None = getattr(args, "export", None)

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

        if result_raw == "SUCCESS":
            result_display = _c(_Ansi.GREEN, "SUCCESS")
        elif result_raw == "FAILED":
            result_display = _c(_Ansi.RED, "FAILED")
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
            '  stickshredder wipe --device E: --method standard --operator "Robin Oertel"\n'
            '  stickshredder wipe --device E: --method bsi --schutzklasse 3 --operator "Robin" --client "ACME GmbH"\n'
            '  stickshredder wipe --device E: --method custom --passes 5 --pattern random --operator "Robin"\n'
            '  stickshredder history\n'
            '  stickshredder history --export out.csv\n'
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
        required=True,
        choices=["zero", "standard", "bsi", "custom"],
        help="Wipe method: zero (1-pass zeros), standard (3-pass random), bsi (7-pass BSI-VSITR), custom",
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
        default="sample",
        help=(
            "Verification mode after wiping (default: sample). "
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
    sub_history.set_defaults(func=cmd_history)

    return parser


# -- Entry point ------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    setup_logging()

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
