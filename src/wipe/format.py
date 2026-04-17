"""Optional reformat-after-wipe support.

After a successful secure wipe, the target disk has no partition table and no
filesystem. Windows reports it as "Size=0, OperationalStatus=Unknown" and the
user has to open Disk Management to make it usable again. This module wraps the
PowerShell storage cmdlets so that — on request — the freshly wiped disk is
re-initialised, partitioned, and formatted in a single call.

The contract (FormatResult, reformat_drive signature) is intentionally locked:
other modules build against it. Don't rename fields or parameters.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

# ─────────────────────────────────────────────────────────────────────
# Public types
# ─────────────────────────────────────────────────────────────────────

FormatProgressCallback = Callable[[str], None]  # status message


@dataclass
class FormatResult:
    success: bool
    method: str            # "powershell" | "none"
    filesystem: str        # "FAT32" | "exFAT" | "NTFS" | "none"
    label: str             # volume label (post-sanitisation/truncation)
    partition_style: str   # "MBR" | "GPT"
    duration_seconds: float
    error_message: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Internal constants
# ─────────────────────────────────────────────────────────────────────

# Map normalised user input → PowerShell -FileSystem value (canonical casing).
_SUPPORTED_FILESYSTEMS = {
    "fat32": "FAT32",
    "exfat": "exFAT",
    "ntfs": "NTFS",
}

_SUPPORTED_PARTITION_STYLES = {"MBR", "GPT"}

# FAT32 volume labels are limited to 11 ASCII characters. exFAT/NTFS allow 32.
_FAT32_LABEL_MAX = 11
_OTHER_LABEL_MAX = 32

# Per-step timeout (seconds). Format-Volume on a large disk can take a while
# but PowerShell's storage cmdlets are quick-format by default, so 5 min is
# generous.
_STEP_TIMEOUT = 300

# Regex for label sanitisation: keep ASCII letters, digits, underscore, hyphen,
# and space. Strip everything else (quotes, semicolons, backticks, etc.) so the
# resulting string is safe to interpolate into the PowerShell command.
_LABEL_SANITISE_RE = re.compile(r"[^A-Za-z0-9_\- ]")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _sanitise_label(label: str, filesystem_canonical: str) -> str:
    """Strip dangerous characters and truncate per filesystem limits."""
    cleaned = _LABEL_SANITISE_RE.sub("", label)
    limit = _FAT32_LABEL_MAX if filesystem_canonical == "FAT32" else _OTHER_LABEL_MAX
    return cleaned[:limit]


def _notify(callback: FormatProgressCallback | None, message: str) -> None:
    if callback is not None:
        try:
            callback(message)
        except Exception:
            # A misbehaving progress callback must never abort the format op.
            pass


def _powershell_available() -> bool:
    """Return True iff a PowerShell executable is on PATH."""
    return shutil.which("powershell") is not None or shutil.which("pwsh") is not None


def _build_command_script(
    disk_number: int,
    partition_style: str,
    fs_powershell: str,
    label: str,
) -> str:
    """Build the single -Command string that runs all four storage cmdlets.

    Putting them in one PowerShell session avoids three additional process
    startup costs and — more importantly — keeps the `$p` variable in scope
    between New-Partition and Format-Volume.
    """
    # Note: label is pre-sanitised to ASCII alnum/underscore/hyphen/space, so
    # double-quoting it inside the PowerShell script is safe.
    return (
        f"Clear-Disk -Number {disk_number} -RemoveData -RemoveOEM -Confirm:$false; "
        f"Initialize-Disk -Number {disk_number} -PartitionStyle {partition_style}; "
        f"$p = New-Partition -DiskNumber {disk_number} -UseMaximumSize -AssignDriveLetter; "
        f"Format-Volume -Partition $p -FileSystem {fs_powershell} "
        f'-NewFileSystemLabel "{label}" -Confirm:$false'
    )


def _run_powershell(script: str) -> subprocess.CompletedProcess:
    """Invoke PowerShell with the assembled script.

    Wrapped in a function so tests can patch `subprocess.run` at the module
    level via `wipe.format.subprocess.run`.
    """
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", script,
        ],
        text=True,
        capture_output=True,
        timeout=_STEP_TIMEOUT,
    )


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def reformat_drive(
    disk_number: int,
    filesystem: str = "exfat",
    label: str = "USB",
    partition_style: str = "MBR",
    progress_callback: FormatProgressCallback | None = None,
) -> FormatResult:
    """Clear, partition, and format the disk at `disk_number`.

    On success, the drive ends up with a single primary partition spanning the
    full disk, formatted with the chosen filesystem and assigned a drive
    letter. On failure, returns a FormatResult describing what went wrong —
    callers should never need to catch an exception from this function for the
    normal failure paths (PowerShell errors, invalid input, missing platform
    support). Truly unexpected exceptions still propagate.
    """
    started = time.monotonic()

    # Platform guard — we only support Windows because the implementation
    # depends on Windows-specific PowerShell storage cmdlets.
    if sys.platform != "win32":
        _notify(progress_callback, "Reformat skipped: not running on Windows.")
        return FormatResult(
            success=False,
            method="none",
            filesystem="none",
            label=label,
            partition_style=partition_style,
            duration_seconds=time.monotonic() - started,
            error_message="Reformat requires Windows",
        )

    # Validate filesystem.
    fs_key = filesystem.strip().lower()
    if fs_key not in _SUPPORTED_FILESYSTEMS:
        supported = ", ".join(sorted(_SUPPORTED_FILESYSTEMS.values()))
        return FormatResult(
            success=False,
            method="none",
            filesystem="none",
            label=label,
            partition_style=partition_style,
            duration_seconds=time.monotonic() - started,
            error_message=(
                f"Unsupported filesystem '{filesystem}'. "
                f"Supported options: {supported}."
            ),
        )
    fs_canonical = _SUPPORTED_FILESYSTEMS[fs_key]

    # Validate partition style.
    style_key = partition_style.strip().upper()
    if style_key not in _SUPPORTED_PARTITION_STYLES:
        return FormatResult(
            success=False,
            method="none",
            filesystem=fs_canonical,
            label=label,
            partition_style=partition_style,
            duration_seconds=time.monotonic() - started,
            error_message=(
                f"Unsupported partition style '{partition_style}'. "
                "Supported options: MBR, GPT."
            ),
        )

    # Sanitise + truncate label.
    safe_label = _sanitise_label(label, fs_canonical)

    # Verify PowerShell is on PATH before attempting the call. This produces a
    # nicer error message than an ambiguous FileNotFoundError later.
    if not _powershell_available():
        return FormatResult(
            success=False,
            method="none",
            filesystem=fs_canonical,
            label=safe_label,
            partition_style=style_key,
            duration_seconds=time.monotonic() - started,
            error_message="PowerShell not found on PATH; cannot reformat.",
        )

    script = _build_command_script(disk_number, style_key, fs_canonical, safe_label)

    # Emit a progress message before each logical step so a UI can render a
    # status line. The actual cmdlets run inside a single PowerShell session,
    # so we cannot interleave between them — the messages fire up front in
    # the order PowerShell will execute them.
    _notify(progress_callback, "Clearing disk...")
    _notify(progress_callback, "Initializing...")
    _notify(progress_callback, "Creating partition...")
    _notify(progress_callback, "Formatting...")

    try:
        completed = _run_powershell(script)
    except subprocess.TimeoutExpired as exc:
        return FormatResult(
            success=False,
            method="powershell",
            filesystem=fs_canonical,
            label=safe_label,
            partition_style=style_key,
            duration_seconds=time.monotonic() - started,
            error_message=f"Reformat timed out after {exc.timeout:.0f} seconds.",
        )
    except FileNotFoundError as exc:
        return FormatResult(
            success=False,
            method="none",
            filesystem=fs_canonical,
            label=safe_label,
            partition_style=style_key,
            duration_seconds=time.monotonic() - started,
            error_message=f"PowerShell executable not found: {exc}",
        )
    except OSError as exc:
        return FormatResult(
            success=False,
            method="powershell",
            filesystem=fs_canonical,
            label=safe_label,
            partition_style=style_key,
            duration_seconds=time.monotonic() - started,
            error_message=f"Failed to launch PowerShell: {exc}",
        )

    if completed.returncode != 0:
        # Prefer stderr but fall back to stdout — PowerShell sometimes writes
        # cmdlet errors to the standard output stream.
        err = (completed.stderr or "").strip() or (completed.stdout or "").strip()
        if not err:
            err = f"PowerShell exited with code {completed.returncode}."
        return FormatResult(
            success=False,
            method="powershell",
            filesystem=fs_canonical,
            label=safe_label,
            partition_style=style_key,
            duration_seconds=time.monotonic() - started,
            error_message=err,
        )

    _notify(progress_callback, "Done.")
    return FormatResult(
        success=True,
        method="powershell",
        filesystem=fs_canonical,
        label=safe_label,
        partition_style=style_key,
        duration_seconds=time.monotonic() - started,
        error_message=None,
    )
