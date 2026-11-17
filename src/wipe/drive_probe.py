"""Probe what a disk's firmware can tell us — over exactly one path. READ-ONLY.

The path is chosen up front by ``wipe.transport`` from the bus type and port
driver Windows reports for the disk — never by trying one command after the
other (sending NVMe and ATA queries by trial and error crashed Windows twice
on 17.11.2026, see ``docs/hardware-safety.md``):

  "ata"   SATA disk on storahci     IDENTIFY, READ NATIVE MAX ADDRESS EXT,
                                    DEVICE CONFIGURATION IDENTIFY, SMART READ DATA
  "sat"   ATA drive behind a USB    IDENTIFY, DEVICE CONFIGURATION IDENTIFY,
          SAT bridge                SMART READ DATA (ATA PASS-THROUGH(16))
  "nvme"  NVMe disk on stornvme     Identify Controller, SMART / health log
  "none"  everything else           nothing

A system disk (or any disk while the system disks are unknown) is never
probed: ``probe_drive`` does not even open it. StickShredder never wipes a
system disk, so it never needs a drive command there either.

Access rights:
  ``probe_drive`` opens the disk with desired access 0 (query-only). The NVMe
  queries work on such a handle (FILE_ANY_ACCESS). The ATA and SCSI
  pass-through IOCTLs are defined with FILE_READ_ACCESS | FILE_WRITE_ACCESS,
  so on a query-only handle they could only be refused — ``probe_drive``
  does not send them and says so in ``note``. A caller holding a read/write
  handle (an elevated wipe session) uses ``probe_handle``.

Never raises: on any failure the result carries no facts.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from dataclasses import dataclass
from typing import Literal

from core.log import audit_log
from wipe import device, nvme, passthrough, sat, smart, transport
from wipe.smart import SmartSummary, smart_unavailable

# ── Win32 ────────────────────────────────────────────────────────────────

QUERY_ONLY_ACCESS = 0
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def _handle_is_invalid(h) -> bool:
    return h is None or h in (-1, 0xFFFFFFFFFFFFFFFF, 0)


# ── HPA status codes ─────────────────────────────────────────────────────
# Machine-readable; they fit the certificate payload's ``hpa`` field as is.

HPA_NONE = "none"                 # checked, nothing hidden
HPA_FOUND_PREFIX = "found:"       # "found:<hidden bytes>"
HPA_UNKNOWN = "unknown"           # could not be checked (no ATA access / no data)
HPA_NOT_APPLICABLE = "n/a"        # NVMe — no HPA/DCO concept

ATA_SECTOR_SIZE = 512


def hpa_hidden_bytes(status: str) -> int | None:
    """Hidden bytes for a status code: 0 for ``none``, ``None`` if unknown/n/a."""
    if status == HPA_NONE:
        return 0
    if status.startswith(HPA_FOUND_PREFIX):
        try:
            return int(status[len(HPA_FOUND_PREFIX):])
        except ValueError:
            return None
    return None


def describe_hpa(status: str, language: str = "de") -> str:
    """Short display text for a status code (German or English)."""
    english = language == "en"
    if status == HPA_NONE:
        return "none" if english else "keiner"
    if status == HPA_NOT_APPLICABLE:
        return "not applicable" if english else "nicht zutreffend"
    hidden = hpa_hidden_bytes(status)
    if hidden:
        gb = f"{hidden / 1024 ** 3:.1f}"
        return f"found: {gb} GB hidden" if english else f"gefunden: {gb.replace('.', ',')} GB versteckt"
    return "not checkable" if english else "nicht prüfbar"


# ── Result ───────────────────────────────────────────────────────────────

@dataclass
class DriveCapabilities:
    transport: Literal["ata", "sat", "nvme", "none"]   # the one path chosen for the disk
    identify_model: str | None
    security_supported: bool
    security_frozen: bool
    enhanced_erase: bool
    sanitize_crypto: bool
    sanitize_block: bool
    hpa_status: str
    smart: SmartSummary
    note: str = ""                                     # why facts are missing (English)


def _no_capabilities(path: str = "none", note: str = "") -> DriveCapabilities:
    return DriveCapabilities(
        transport=path,
        identify_model=None,
        security_supported=False,
        security_frozen=False,
        enhanced_erase=False,
        sanitize_crypto=False,
        sanitize_block=False,
        hpa_status=HPA_NOT_APPLICABLE if path == "nvme" else HPA_UNKNOWN,
        smart=smart_unavailable(path),
        note=note,
    )


# ── ATA helpers ──────────────────────────────────────────────────────────

def _identify_sectors(info: passthrough.IdentifyData) -> int:
    """User-accessible sectors: LBA48 words 100..103, else LBA28 words 60..61."""
    if info.lba48_max_sectors:
        return info.lba48_max_sectors
    words = info.raw_words
    return words[60] | (words[61] << 16)


def _ata_sanitize(words: tuple) -> tuple[bool, bool]:
    """IDENTIFY word 59: bit 12 SANITIZE feature set, 13 CRYPTO SCRAMBLE,
    15 BLOCK ERASE."""
    w59 = words[59]
    if not w59 & (1 << 12):
        return False, False
    return bool(w59 & (1 << 13)), bool(w59 & (1 << 15))


def _hpa_status(accessible: int, native_sectors: int | None, dco_max_lba: int | None) -> str:
    """Compare the accessible size with every upper bound we could read.

    READ NATIVE MAX gives the size including an HPA; DEVICE CONFIGURATION
    IDENTIFY gives the factory maximum *LBA* (size - 1), which also covers a
    DCO. With neither, nothing can be said.
    """
    bounds = []
    if native_sectors:
        bounds.append(native_sectors)
    if dco_max_lba:
        bounds.append(dco_max_lba + 1)
    if not bounds or accessible <= 0:
        return HPA_UNKNOWN
    hidden = max(bounds) - accessible
    return f"{HPA_FOUND_PREFIX}{hidden * ATA_SECTOR_SIZE}" if hidden > 0 else HPA_NONE


def _from_identify(path, info, hpa_status, smart_summary) -> DriveCapabilities:
    crypto, block = _ata_sanitize(info.raw_words)
    return DriveCapabilities(
        transport=path,
        identify_model=info.model or None,
        security_supported=info.security_supported,
        security_frozen=info.security_frozen,
        enhanced_erase=info.enhanced_erase_supported,
        sanitize_crypto=crypto,
        sanitize_block=block,
        hpa_status=hpa_status,
        smart=smart_summary,
    )


# ── One probe per transport ──────────────────────────────────────────────

def _probe_ata(handle) -> DriveCapabilities:
    info = passthrough.ata_identify(handle)
    if info is None or not sat.identify_words_plausible(info.raw_words):
        return _no_capabilities("ata", "ATA IDENTIFY failed or returned implausible data")
    hpa = _hpa_status(
        _identify_sectors(info),
        passthrough.read_native_max_address(handle),
        # PR #1 names it "max sectors"; words 3..6 actually hold the max LBA.
        passthrough.device_configuration_identify_max(handle),
    )
    return _from_identify("ata", info, hpa, smart.read_ata_smart(handle, "ata"))


def _probe_sat(handle) -> DriveCapabilities:
    info = sat.sat_identify(handle)
    if info is None:
        return _no_capabilities("sat", "SAT IDENTIFY failed or returned implausible data")
    # SAT here is data-in only, so READ NATIVE MAX (non-data) is not sent;
    # the DCO maximum is the only upper bound available.
    hpa = _hpa_status(_identify_sectors(info), None, sat.sat_dco_max_lba(handle))
    return _from_identify("sat", info, hpa, smart.read_ata_smart(handle, "sat"))


def _probe_nvme(handle) -> DriveCapabilities:
    info = nvme.read_nvme_identify(handle)
    if info is None:
        return _no_capabilities("nvme", "NVMe Identify Controller failed")
    return DriveCapabilities(
        transport="nvme",
        identify_model=info.model or None,
        security_supported=False,
        security_frozen=False,
        enhanced_erase=False,
        sanitize_crypto=info.sanitize_crypto,
        sanitize_block=info.sanitize_block,
        hpa_status=HPA_NOT_APPLICABLE,
        smart=nvme.read_nvme_health(handle),
    )


def _run(handle, info: transport.StorageInfo, *, read_write: bool) -> DriveCapabilities:
    """Send the chosen transport's read commands — and nothing else."""
    if info.transport == "nvme":
        return _probe_nvme(handle)
    if info.transport in ("ata", "sat"):
        if not read_write:
            return _no_capabilities(info.transport, (
                f"{info.reason}; ATA pass-through needs a read/write handle "
                f"(administrator) — not sent on the query-only handle"
            ))
        return _probe_ata(handle) if info.transport == "ata" else _probe_sat(handle)
    return _no_capabilities("none", info.reason)


# ── Public API ───────────────────────────────────────────────────────────

def probe_handle(handle) -> DriveCapabilities:
    """Probe an already-open disk handle over its one transport. Never raises.

    The handle should carry GENERIC_READ | GENERIC_WRITE (an elevated wipe
    session); pass-through commands are refused on anything less.
    """
    try:
        return _run(handle, transport.inspect_handle(handle), read_write=True)
    except Exception as exc:  # noqa: BLE001 — never raise
        audit_log(f"drive probe raised: {exc}")
        return _no_capabilities("none", f"probe failed: {exc}")


def probe_drive(device_id: str) -> DriveCapabilities:
    """Open ``device_id`` query-only, probe it, close it. Never raises.

    A system disk — or any disk while the system disks are unknown — is
    refused before it is opened.
    """
    try:
        index = device._physical_index(device_id)
    except Exception:  # noqa: BLE001 — not \\.\PhysicalDriveN
        return _no_capabilities("none", f"not a physical drive path: {device_id!r}")

    try:
        system_disks = transport.current_system_disks()
    except Exception as exc:  # noqa: BLE001
        audit_log(f"drive probe: system disk lookup raised: {exc}")
        system_disks = None
    refusal = transport.system_disk_refusal(index, system_disks)
    if refusal:
        audit_log(f"drive probe {device_id}: not probed — {refusal}")
        return _no_capabilities("none", refusal)

    try:
        handle = kernel32.CreateFileW(
            device_id,
            QUERY_ONLY_ACCESS,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
    except Exception as exc:  # noqa: BLE001
        audit_log(f"drive probe: opening {device_id} raised: {exc}")
        return _no_capabilities("none", f"cannot open {device_id}")
    if _handle_is_invalid(handle):
        audit_log(
            f"drive probe: cannot open {device_id} query-only "
            f"(error {ctypes.get_last_error()})"
        )
        return _no_capabilities("none", f"cannot open {device_id}")

    try:
        info = transport.inspect_handle(handle, expected_disk=index,
                                        system_disks=system_disks)
        caps = _run(handle, info, read_write=False)
    except Exception as exc:  # noqa: BLE001 — never raise
        audit_log(f"drive probe {device_id} raised: {exc}")
        caps = _no_capabilities("none", f"probe failed: {exc}")
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass

    audit_log(
        f"drive probe {device_id}: transport={caps.transport} "
        f"model={caps.identify_model!r} hpa={caps.hpa_status} "
        f"smart={'yes' if caps.smart.available else 'no'}"
        + (f" ({caps.note})" if caps.note else "")
    )
    return caps
