"""Device detection and raw disk access for Windows removable storage."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import re
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field


import pythoncom
import wmi

from core.log import audit_log

# ── Windows API constants ──────────────────────────────────────────────

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = -1

IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C
FSCTL_LOCK_VOLUME = 0x00090018
FSCTL_DISMOUNT_VOLUME = 0x00090020
# Answered by the volume manager (which disks a volume lies on); never
# reaches the drive itself.
IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000

# Load kernel32 with use_last_error=True so ctypes.get_last_error() returns the
# real Win32 error code set by the last API call (rather than whatever errno
# the Python interpreter happened to stash in the thread state).
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Explicit prototypes for every kernel32 function used in this module.
# Without these, ctypes defaults to int-sized arguments and return values,
# which silently truncates 64-bit HANDLEs on 64-bit Windows and leads to
# "handle looks valid but all subsequent calls fail" bugs.
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

kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
kernel32.DeviceIoControl.restype = wintypes.BOOL

kernel32.GetWindowsDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
kernel32.GetWindowsDirectoryW.restype = wintypes.UINT

kernel32.FindFirstVolumeW.argtypes = [wintypes.LPWSTR, wintypes.DWORD]
kernel32.FindFirstVolumeW.restype = wintypes.HANDLE

kernel32.FindNextVolumeW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD]
kernel32.FindNextVolumeW.restype = wintypes.BOOL

kernel32.FindVolumeClose.argtypes = [wintypes.HANDLE]
kernel32.FindVolumeClose.restype = wintypes.BOOL

kernel32.GetVolumeNameForVolumeMountPointW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
]
kernel32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL


def _handle_is_invalid(h) -> bool:
    """True if a Win32 HANDLE value represents failure.

    CreateFileW returns INVALID_HANDLE_VALUE on failure. Depending on how
    ctypes interprets the return type (signed vs unsigned, 32- vs 64-bit),
    that value surfaces in Python as either -1 or 0xFFFFFFFFFFFFFFFF. A
    None or 0 handle is also treated as invalid defensively.
    """
    if h is None:
        return True
    return h in (-1, 0xFFFFFFFFFFFFFFFF, 0)


# ── Data model ─────────────────────────────────────────────────────────

@dataclass
class DeviceInfo:
    drive_letter: str                # e.g. "E:"
    device_id: str                   # e.g. r"\\.\PhysicalDrive1"
    model: str
    serial_number: str
    capacity_bytes: int
    filesystem: str                  # NTFS, FAT32, exFAT, RAW, …
    connection_type: str             # USB / SATA / NVMe / SD / ... (MSFT_Disk.BusType)
    is_removable: bool
    is_system_drive: bool
    is_internal: bool = False
    has_bitlocker: bool = False
    has_active_processes: bool = False
    partition_count: int = 0
    friendly_name: str = ""
    # Every drive letter on the same physical disk (sorted, includes own).
    disk_letters: list[str] = field(default_factory=list)

    @property
    def capacity_gb(self) -> float:
        return round(self.capacity_bytes / (1024 ** 3), 2)

    @property
    def safe_to_wipe(self) -> bool:
        return not self.is_system_drive


# ── WMI helpers ────────────────────────────────────────────────────────

def _get_wmi_connection() -> wmi.WMI:
    # WMI is COM: every thread must initialise COM before using it. The GUI
    # calls prepare_target() from the WipeWorker QThread; without this the
    # re-check failed there and refused every real wipe. Safe to call
    # repeatedly on the same thread.
    pythoncom.CoInitialize()
    return wmi.WMI()


_STORAGE_NAMESPACE = r"root\Microsoft\Windows\Storage"


def _get_storage_wmi_connection() -> wmi.WMI:
    """WMI connection to the Windows storage stack (MSFT_Disk, MSFT_Partition).

    Same rule as _get_wmi_connection(): initialise COM on this thread first.
    """
    pythoncom.CoInitialize()
    return wmi.WMI(namespace=_STORAGE_NAMESPACE)


def _storage_system_disks() -> set[int] | None:
    """Disk numbers the Windows storage stack flags as boot or system disk.

    IsBoot marks the disk with the running Windows' boot volume; IsSystem
    marks the disk holding the EFI/system partition the PC actually booted
    from — which can be a different disk than the one with Windows on it.

    The MBR "active" flag of a partition is deliberately not used: bootable
    USB installer sticks carry it, and it says nothing about the running
    Windows. Returns None when the query fails, so callers can fail closed.
    """
    try:
        storage = _get_storage_wmi_connection()
        return {
            int(d.Number) for d in storage.MSFT_Disk()
            if d.IsSystem or d.IsBoot
        }
    except Exception as exc:  # noqa: BLE001 — caller fails closed on None
        audit_log(f"Storage system-disk query failed: {exc}")
        return None


def _system_drive_letter() -> str:
    """Return the boot volume drive letter (e.g. 'C:') via Win32 API.

    Uses GetWindowsDirectoryW rather than trusting the %SystemRoot%
    environment variable, which is user-controllable and therefore
    unsafe as the authoritative source for "which drive must never
    be wiped".
    """
    try:
        buf = ctypes.create_unicode_buffer(260)
        n = kernel32.GetWindowsDirectoryW(buf, 260)
        if n > 0:
            return buf.value[:2].upper()
    except Exception:
        pass
    # Last-resort fallback if the API call ever fails on a weird system.
    return "C:"


@dataclass
class _DiskLayout:
    """Which disk each drive letter lives on, and partitions per disk.

    Read once per scan: every association object WMI hands out costs a
    round trip when it is followed, and the list used to walk both
    association classes again for every drive letter.
    """
    letter_disk: dict[str, int] = field(default_factory=dict)   # "E:" -> disk
    partitions: dict[int, int] = field(default_factory=dict)    # disk -> count


# DeviceID="..." in a WMI object path; backslash escapes the next character.
_PATH_DEVICE_ID_RE = re.compile(r'\.DeviceID="((?:[^"\\]|\\.)*)"')


def _ref_device_id(assoc, role: str) -> str | None:
    """DeviceID an association end refers to, read from its object path.

    Following the reference (``assoc.Antecedent``) fetches the whole object,
    one WMI round trip each; the path already names the key. None when the
    path cannot be read — the caller follows the reference then.
    """
    try:
        path = assoc.wmi_property(role).value
    except Exception:  # noqa: BLE001 — fall back to following the reference
        return None
    if not isinstance(path, str):
        return None
    match = _PATH_DEVICE_ID_RE.search(path)
    return re.sub(r"\\(.)", r"\1", match.group(1)) if match else None


def _disk_layout(c: wmi.WMI, disks=None) -> _DiskLayout:
    """One pass over Win32_DiskDriveToDiskPartition and Win32_LogicalDiskToPartition.

    ``disks`` is the scan's Win32_DiskDrive list (queried when None).
    Raises when WMI fails; callers decide how to fail.
    """
    index_by_id: dict[str, int] = {}
    for disk in (c.Win32_DiskDrive() if disks is None else disks):
        try:
            index_by_id[str(disk.DeviceID).upper()] = int(disk.Index)
        except Exception:  # noqa: BLE001 — that disk is then looked up the slow way
            continue

    partition_disk: dict[str, int] = {}
    partitions: dict[int, int] = {}
    for assoc in c.Win32_DiskDriveToDiskPartition():
        disk_id = _ref_device_id(assoc, "Antecedent")
        partition = _ref_device_id(assoc, "Dependent")
        index = index_by_id.get(disk_id.upper()) if disk_id else None
        if index is None or partition is None:
            index = int(assoc.Antecedent.Index)
            partition = assoc.Dependent.DeviceID
        partition_disk[partition] = index
        partitions[index] = partitions.get(index, 0) + 1

    letter_disk: dict[str, int] = {}
    for assoc in c.Win32_LogicalDiskToPartition():
        partition = _ref_device_id(assoc, "Antecedent")
        letter = _ref_device_id(assoc, "Dependent")
        if partition is None or letter is None:
            partition = assoc.Antecedent.DeviceID
            letter = assoc.Dependent.DeviceID
        index = partition_disk.get(partition)
        if index is not None:
            letter_disk[letter.upper()] = index
    return _DiskLayout(letter_disk, partitions)


def _system_disk_indices(
    c: wmi.WMI, layout: _DiskLayout | None = None
) -> set[int] | None:
    """Return the physical disk indices that must never be written.

    The union of (a) the disk(s) holding the Windows volume and (b) every
    disk the storage stack reports as boot or system disk. Every partition
    on these disks counts as system: wiping any of them writes the whole
    physical disk, i.e. destroys Windows or leaves the PC unbootable.

    ``layout`` is the scan's already-read layout (re-read when None).
    Returns None when either part cannot be determined, so callers can fail
    closed (see _blocked_as_system).
    """
    sys_letter = _system_drive_letter().upper()
    try:
        if layout is None:
            layout = _disk_layout(c)
        indices = {
            index for letter, index in layout.letter_disk.items()
            if letter == sys_letter
        }
    except Exception as exc:  # noqa: BLE001 — fail closed below
        audit_log(f"System disk lookup failed: {exc}")
        return None
    if not indices:
        audit_log(f"Windows disk for {sys_letter} could not be determined")
        return None
    storage = _storage_system_disks()
    if storage is None:
        return None
    return indices | storage


def _current_system_disks() -> set[int] | None:
    """_system_disk_indices() on a fresh WMI connection; None on any failure."""
    try:
        return _system_disk_indices(_get_wmi_connection())
    except Exception as exc:  # noqa: BLE001 — caller fails closed on None
        audit_log(f"System disk lookup failed: {exc}")
        return None


def _blocked_as_system(
    index: int, connection: str, sys_indices: set[int] | None
) -> bool:
    """True if physical disk ``index`` must be treated as a system disk.

    When the system layout is unknown (None/empty), every non-USB disk is
    blocked: an internal disk might be the one Windows lives or boots on.
    """
    if not sys_indices:
        return connection != "USB"
    return index in sys_indices


def _letters_on_disk(c: wmi.WMI, index: int) -> list[str]:
    """Drive letters currently mounted from partitions of physical disk ``index``."""
    layout = _disk_layout(c)
    return sorted(
        letter for letter, disk in layout.letter_disk.items() if disk == index
    )


# MSFT_Disk.BusType (STORAGE_BUS_TYPE) -> connection shown to the user.
_BUS_TYPE_NAMES = {
    1: "SCSI", 3: "ATA", 7: "USB", 8: "RAID", 10: "SAS",
    11: "SATA", 12: "SD", 13: "MMC", 17: "NVMe",
}

# Buses whose disks are plugged in from outside, not built into the PC.
_EXTERNAL_CONNECTIONS = {"USB", "SD", "MMC"}


def _storage_bus_types() -> dict[int, int] | None:
    """{disk number: MSFT_Disk.BusType}; None when the storage query fails.

    Win32_DiskDrive.InterfaceType is unreliable: a USB enclosure speaking
    UAS reports "SCSI", like a system NVMe disk does. The storage stack
    reports the real bus.
    """
    try:
        storage = _get_storage_wmi_connection()
        return {int(d.Number): int(d.BusType or 0) for d in storage.MSFT_Disk()}
    except Exception as exc:  # noqa: BLE001 — fall back to InterfaceType
        audit_log(f"Storage bus-type query failed: {exc}")
        return None


def _connection_type(
    index: int, interface_type: str | None, bus_types: dict[int, int] | None
) -> str:
    """Connection of physical disk ``index``: storage BusType, else InterfaceType."""
    name = _BUS_TYPE_NAMES.get((bus_types or {}).get(index, 0))
    return name or _connection_type_from_interface(interface_type)


def _connection_type_from_interface(interface_type: str | None) -> str:
    """Fallback from Win32_DiskDrive.InterfaceType when BusType is unknown.

    "SCSI" stays "SCSI": NVMe disks and UAS USB enclosures both report it,
    so it says nothing about the real bus.
    """
    if not interface_type:
        return "Unknown"
    iface = interface_type.upper()
    if "USB" in iface:
        return "USB"
    if "NVME" in iface:
        return "NVMe"
    if "IDE" in iface or "SATA" in iface:
        return "SATA"
    return interface_type


def _is_external(connection: str, media_type: str) -> bool:
    """True for disks plugged in from outside (USB, SD/MMC, external media)."""
    media = media_type.lower()
    return (
        connection in _EXTERNAL_CONNECTIONS
        or "removable" in media
        or "external" in media
    )


def _is_admin() -> bool:
    """True when this process runs with administrator rights."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 — unknown counts as "no"
        return False


# ── Per-scan cache ─────────────────────────────────────────────────────
#
# A slow answer (one WMI query for every volume) is fetched once per
# list_devices() call and reused for each further drive of that scan. Per
# thread: the GUI scans on the GUI thread while a worker thread may re-check.

_scan = threading.local()


@contextmanager
def _scan_cache():
    """Keep slow answers for the duration of one device scan."""
    _scan.cache = {}
    try:
        yield
    finally:
        _scan.cache = None


def _cached(key: str, compute):
    """``compute()`` once per scan (every call outside a scan computes)."""
    cache = getattr(_scan, "cache", None)
    if cache is None:
        return compute()
    if key not in cache:
        cache[key] = compute()
    return cache[key]


# ── BitLocker ──────────────────────────────────────────────────────────

_BITLOCKER_NAMESPACE = r"root\CIMV2\Security\MicrosoftVolumeEncryption"

# Set after the first failed BitLocker WMI query: skip WMI then.
_bitlocker_wmi_denied = False

# Explorer's BitLocker state of a volume (System.Volume.BitLockerProtection),
# readable without administrator rights: 1 on, 3 encrypting, 4 decrypting,
# 5 suspended, 6 locked, 8 waiting for activation — data on the volume is
# (partly) encrypted. 2 is off; other values (0, 7: FAT, RAW, network) mean
# the volume carries no BitLocker.
_SHELL_BITLOCKER_ENCRYPTED = frozenset({1, 3, 4, 5, 6, 8})
_SHELL_BITLOCKER_PROPERTY = "System.Volume.BitLockerProtection"
_SSF_DRIVES = 17   # Shell namespace "This PC"


def _get_bitlocker_wmi_connection() -> wmi.WMI:
    """WMI connection to the BitLocker namespace (Win32_EncryptableVolume).

    Needs administrator rights; raises when access is denied.
    """
    pythoncom.CoInitialize()
    return wmi.WMI(namespace=_BITLOCKER_NAMESPACE)


def _bitlocker_states_via_wmi() -> dict[str, bool]:
    """{"E:": encrypted} for every volume with a letter, in one WMI query.

    ProtectionStatus 1 = on, 2 = unknown (volume locked). ConversionStatus
    0 = fully decrypted; anything else means (some) data is encrypted, even
    while protection is suspended. Raises if WMI is denied.
    """
    states: dict[str, bool] = {}
    for vol in _get_bitlocker_wmi_connection().Win32_EncryptableVolume():
        letter = str(getattr(vol, "DriveLetter", "") or "").strip().upper()
        if not letter:
            continue
        protection = int(getattr(vol, "ProtectionStatus", 0) or 0)
        conversion = int(getattr(vol, "ConversionStatus", 0) or 0)
        states[letter] = protection in (1, 2) or conversion != 0
    return states


def _shell_application():
    """Windows Explorer's Shell.Application COM object (this thread)."""
    from win32com.client import Dispatch

    pythoncom.CoInitialize()
    return Dispatch("Shell.Application")


def _shell_bitlocker_value(drive_letter: str):
    """Explorer's BitLocker property of ``E:`` (raw value; None if not readable)."""
    item = _shell_application().NameSpace(_SSF_DRIVES).ParseName(drive_letter + "\\")
    if item is None:
        return None
    return item.ExtendedProperty(_SHELL_BITLOCKER_PROPERTY)


def _bitlocker_via_shell(drive_letter: str) -> bool | None:
    """BitLocker state as Explorer shows it; None when there is no answer."""
    try:
        value = _shell_bitlocker_value(drive_letter)
    except Exception as exc:  # noqa: BLE001 — fall back to WMI
        audit_log(f"BitLocker shell property not readable for {drive_letter}: {exc}")
        return None
    if value is None or value == "":
        return None
    try:
        return int(value) in _SHELL_BITLOCKER_ENCRYPTED
    except (TypeError, ValueError):
        return None


def _check_bitlocker(drive_letter: str) -> bool:
    """True if the volume is BitLocker-encrypted. Best-effort, never raises.

    Explorer's BitLocker property first: it needs no administrator rights
    and answers in milliseconds. Only when it has no answer, and only as
    administrator, one WMI query for all volumes (once per scan). Without
    administrator rights WMI and manage-bde are refused, and each refusal
    took ~5 s per drive — that made the device list slow.
    """
    global _bitlocker_wmi_denied
    letter = drive_letter.upper()
    state = _bitlocker_via_shell(letter)
    if state is not None:
        return state
    if _bitlocker_wmi_denied or not _is_admin():
        return False
    try:
        states = _cached("bitlocker_wmi", _bitlocker_states_via_wmi)
    except Exception as exc:  # noqa: BLE001 — best effort
        # Rights don't change while we run: don't pay the refusal again.
        _bitlocker_wmi_denied = True
        audit_log(f"BitLocker WMI query failed for {letter}: {exc}")
        return False
    return states.get(letter, False)


# Set once handle.exe (Sysinternals, optional) turned out not to be installed.
_handle_exe_missing = False


def _check_active_processes(drive_letter: str) -> bool:
    """Check for open handles on a volume. Best-effort, never raises.

    Uses Sysinternals handle.exe when it is installed. It only sees other
    programs' handles with administrator rights, so without them (or
    without the tool) the probe is skipped instead of run per drive.
    """
    global _handle_exe_missing
    if not drive_letter or _handle_exe_missing or not _is_admin():
        return False
    try:
        result = subprocess.run(
            ["handle.exe", "-accepteula", drive_letter + "\\"],
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout = result.stdout or ""
        lines = [
            ln for ln in stdout.strip().splitlines()
            if drive_letter.upper() in ln.upper() and "pid:" in ln.lower()
        ]
        return len(lines) > 0
    except FileNotFoundError:
        _handle_exe_missing = True
        return False
    except Exception:  # noqa: BLE001 — probe must never break enumeration
        return False


# ── Public API ─────────────────────────────────────────────────────────

def list_devices() -> list[DeviceInfo]:
    """Enumerate removable (and optionally internal non-system) storage devices.

    WMI only, read-only. Slow answers (the partition layout, a BitLocker
    WMI query) are read once for the whole scan, not once per drive letter.
    """
    with _scan_cache():
        return _list_devices()


def _list_devices() -> list[DeviceInfo]:
    devices: list[DeviceInfo] = []
    seen_phys_indices: set[int] = set()
    sys_letter = _system_drive_letter()

    try:
        c = _get_wmi_connection()
    except Exception as exc:
        audit_log(f"WMI connection failed: {exc}")
        return devices

    # Build a map: physical drive index -> DiskDrive WMI object
    disk_map: dict[int, object] = {}
    try:
        for disk in c.Win32_DiskDrive():
            disk_map[disk.Index] = disk
    except Exception as exc:
        audit_log(f"Failed to enumerate physical disks: {exc}")
        return devices

    # Letters and partitions per disk, read once. Unreadable layout: no
    # letter maps to a disk (every disk is listed letterless) and the
    # system disk is unknown (all non-USB disks blocked below).
    try:
        layout: _DiskLayout | None = _disk_layout(c, list(disk_map.values()))
    except Exception as exc:  # noqa: BLE001 — fail closed below
        audit_log(f"Failed to read the partition layout: {exc}")
        layout = None

    # Disks that must never be wiped: the Windows disk plus every disk the
    # storage stack reports as boot/system disk. None = unknown layout.
    sys_indices = _system_disk_indices(c, layout) if layout is not None else None
    audit_log(
        f"System disk indices: {sorted(sys_indices)}" if sys_indices
        else "System/boot disk could not be determined — all non-USB disks blocked"
    )
    layout = layout or _DiskLayout()
    # Real bus per disk from the storage stack; None = use InterfaceType.
    bus_types = _storage_bus_types()

    # Walk logical disks (the ones with drive letters)
    try:
        logical_disks = c.Win32_LogicalDisk()
    except Exception as exc:
        audit_log(f"Failed to enumerate logical disks: {exc}")
        return devices

    for ldisk in logical_disks:
        drive_letter = ldisk.DeviceID  # e.g. "E:"
        try:
            phys_index = layout.letter_disk.get(str(drive_letter).upper())
            if phys_index is None:
                continue
            phys_disk = disk_map.get(phys_index)
            if phys_disk is None:
                continue

            interface_type = getattr(phys_disk, "InterfaceType", None)
            connection = _connection_type(phys_index, interface_type, bus_types)
            media_type = getattr(phys_disk, "MediaType", "") or ""

            is_removable = (
                _is_external(connection, media_type)
                or getattr(ldisk, "DriveType", 0) == 2  # DriveType 2 = Removable
            )

            # Block every partition of the Windows disk, not just C:.
            # If the layout is unknown, block every non-USB disk.
            is_system = (
                drive_letter.upper() == sys_letter.upper()
                or _blocked_as_system(phys_index, connection, sys_indices)
            )
            is_internal = connection != "USB" and not is_removable

            serial = (getattr(phys_disk, "SerialNumber", "") or "").strip()
            model = (getattr(phys_disk, "Model", "") or "").strip()
            capacity = int(getattr(phys_disk, "Size", 0) or 0)
            filesystem = (getattr(ldisk, "FileSystem", "") or "").strip() or "RAW"
            part_count = layout.partitions.get(phys_index, 0)

            has_bitlocker = _check_bitlocker(drive_letter)
            has_active = _check_active_processes(drive_letter)

            device_id = f"\\\\.\\PhysicalDrive{phys_index}"
            friendly = f"{model} ({drive_letter})" if model else drive_letter

            info = DeviceInfo(
                drive_letter=drive_letter,
                device_id=device_id,
                model=model,
                serial_number=serial,
                capacity_bytes=capacity,
                filesystem=filesystem,
                connection_type=connection,
                is_removable=is_removable,
                is_system_drive=is_system,
                is_internal=is_internal,
                has_bitlocker=has_bitlocker,
                has_active_processes=has_active,
                partition_count=part_count,
                friendly_name=friendly,
            )
            devices.append(info)
            seen_phys_indices.add(phys_index)
            audit_log(
                f"Detected device: {friendly} | {device_id} | "
                f"{info.capacity_gb} GB | {connection} | "
                f"removable={is_removable} | system={is_system}"
            )

        except Exception as exc:
            import traceback as _tb
            tb_line = _tb.format_exc().strip().splitlines()[-3:]
            audit_log(
                f"Error processing drive {drive_letter}: {type(exc).__name__}: {exc} "
                f"| traceback_tail: {' | '.join(tb_line)}"
            )
            continue

    # Also surface physical disks that have NO drive letter (raw, unpartitioned,
    # or unmounted internal data disks). Without this, an internal disk that
    # Windows did not assign a letter — exactly the kind of disk wiped in a
    # drive caddy — would be invisible to the tool.
    for phys_index, phys_disk in disk_map.items():
        if phys_index in seen_phys_indices:
            continue
        try:
            interface_type = getattr(phys_disk, "InterfaceType", None)
            connection = _connection_type(phys_index, interface_type, bus_types)
            media_type = getattr(phys_disk, "MediaType", "") or ""

            is_removable = _is_external(connection, media_type)
            is_system = _blocked_as_system(phys_index, connection, sys_indices)
            is_internal = connection != "USB" and not is_removable

            serial = (getattr(phys_disk, "SerialNumber", "") or "").strip()
            model = (getattr(phys_disk, "Model", "") or "").strip()
            capacity = int(getattr(phys_disk, "Size", 0) or 0)
            part_count = layout.partitions.get(phys_index, 0)

            device_id = f"\\\\.\\PhysicalDrive{phys_index}"
            friendly = (
                f"{model} (PhysicalDrive{phys_index}, no letter)"
                if model else f"PhysicalDrive{phys_index} (no letter)"
            )

            info = DeviceInfo(
                drive_letter="",
                device_id=device_id,
                model=model,
                serial_number=serial,
                capacity_bytes=capacity,
                filesystem="RAW",
                connection_type=connection,
                is_removable=is_removable,
                is_system_drive=is_system,
                is_internal=is_internal,
                has_bitlocker=False,  # no mounted volume to probe
                has_active_processes=False,
                partition_count=part_count,
                friendly_name=friendly,
            )
            devices.append(info)
            seen_phys_indices.add(phys_index)
            audit_log(
                f"Detected letterless device: {friendly} | {device_id} | "
                f"{info.capacity_gb} GB | {connection} | "
                f"removable={is_removable} | system={is_system} | internal={is_internal}"
            )
        except Exception as exc:
            audit_log(
                f"Error processing letterless disk {phys_index}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

    # Every letter on the same physical disk (empty for letterless disks).
    letters_by_disk: dict[str, list[str]] = {}
    for d in devices:
        if d.drive_letter:
            letters_by_disk.setdefault(d.device_id, []).append(d.drive_letter.upper())
    for d in devices:
        d.disk_letters = sorted(letters_by_disk.get(d.device_id, []))

    return devices


def is_safe_to_wipe(device: DeviceInfo, allow_internal: bool = False) -> tuple[bool, str]:
    """Central safety gate: decide whether a device may be wiped.

    Returns ``(safe, reason)``. ``reason`` is empty when safe, otherwise a
    human-readable explanation of the refusal. The CLI and GUI must call this
    before opening a device for writing.

    Rules:
      * The Windows system disk is never wipeable.
      * Internal / non-removable disks are refused unless ``allow_internal``.
      * Fail-safe: even with ``allow_internal``, an internal disk is refused
        when StickShredder cannot determine which disks hold Windows or boot
        the PC — because that unknown disk *might* be the one we are about
        to wipe. The layout is re-read here, not taken from the listing.

    prepare_target() still runs right before the disk is written.
    """
    system_reason = (
        "Das ist die Windows-Systemplatte und kann nie gelöscht werden. / "
        "This is the Windows system drive and can never be wiped."
    )
    if device.is_system_drive:
        return False, system_reason
    if device.is_internal:
        if not allow_internal:
            return False, (
                "Interne (fest eingebaute) Platte: nur mit „Interne Festplatten "
                "erlauben“ löschbar. / This is an internal (non-removable) drive. "
                "Internal drives are refused by default — enable 'allow internal "
                "drives' to wipe it."
            )
        sys_indices = _current_system_disks()
        if not sys_indices:
            return False, (
                "Interne Platte verweigert: Es ließ sich nicht sicher bestimmen, "
                "auf welcher Platte Windows liegt bzw. von welcher der PC startet. / "
                "Refusing to wipe an internal drive: StickShredder could not "
                "reliably determine which disk holds Windows or boots the PC "
                "(system / boot layout unknown), so it cannot guarantee this is "
                "not the system disk (fail-safe)."
            )
        try:
            index = _physical_index(device.device_id)
        except UnsafeTargetError as exc:
            return False, str(exc)
        if index in sys_indices:
            return False, system_reason
    return True, ""


# ── Raw disk access helpers ────────────────────────────────────────────

def open_physical_drive(device_id: str) -> int:
    """Open a physical drive for raw read/write. Requires admin privileges.

    Returns the Win32 HANDLE as an integer.
    Raises PermissionError if device_id is the system physical drive.
    Raises OSError on failure.
    """
    # Last-line-of-defence: refuse to open the physical disk that hosts Windows,
    # even if the caller did not check is_system_drive first.
    # prepare_target() is the authoritative gate (it also fails closed on an
    # unknown layout); this check only refuses disks known to be system.
    try:
        # Extract trailing integer from e.g. r"\\.\PhysicalDrive3" -> 3
        drive_index = int(device_id.rstrip().split("PhysicalDrive")[-1])
        sys_indices = _current_system_disks() or set()
        if drive_index in sys_indices:
            audit_log(
                f"Refused to open system drive: {device_id} is PhysicalDrive{drive_index}"
            )
            raise PermissionError(
                f"Refused to open system drive {device_id}: "
                f"this is a system physical drive (PhysicalDrive{drive_index}). "
                "Wiping it would destroy Windows or leave the PC unbootable."
            )
    except PermissionError:
        raise
    except Exception as exc:
        audit_log(f"System-disk safeguard parse failed for {device_id!r}: {exc} — proceeding")

    handle = kernel32.CreateFileW(
        device_id,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if _handle_is_invalid(handle):
        err = ctypes.get_last_error()
        raise OSError(
            f"CreateFileW failed for {device_id} (error {err}). "
            "Are you running as administrator?"
        )
    audit_log(f"Opened physical drive: {device_id} (handle={handle})")
    return handle


def close_drive(handle: int) -> None:
    if not _handle_is_invalid(handle):
        kernel32.CloseHandle(handle)
        audit_log(f"Closed drive handle {handle}")


def get_drive_size(handle: int) -> int:
    """Return the total byte size of an opened physical drive."""

    class DISK_LENGTH_INFO(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_longlong)]

    length_info = DISK_LENGTH_INFO()
    bytes_returned = wintypes.DWORD(0)

    success = kernel32.DeviceIoControl(
        handle,
        IOCTL_DISK_GET_LENGTH_INFO,
        None,
        0,
        ctypes.byref(length_info),
        ctypes.sizeof(length_info),
        ctypes.byref(bytes_returned),
        None,
    )
    if not success:
        err = ctypes.get_last_error()
        raise OSError(f"DeviceIoControl IOCTL_DISK_GET_LENGTH_INFO failed (error {err})")

    size = length_info.Length
    audit_log(f"Drive size: {size} bytes ({size / (1024**3):.2f} GB)")
    return size


def _ioctl_no_data(handle: int, control_code: int, label: str) -> None:
    """Send a DeviceIoControl that has no input/output buffer."""
    bytes_returned = wintypes.DWORD(0)
    success = kernel32.DeviceIoControl(
        handle,
        control_code,
        None,
        0,
        None,
        0,
        ctypes.byref(bytes_returned),
        None,
    )
    if not success:
        raise _win_os_error(f"{label} failed", ctypes.get_last_error())
    audit_log(f"{label} succeeded (handle={handle})")


ERROR_ACCESS_DENIED = 5


def _win_os_error(message: str, code: int) -> OSError:
    """OSError whose ``winerror`` is the Win32 error code (message unchanged)."""
    exc = OSError(f"{message} (error {code})")
    exc.winerror = code
    return exc


def _volume_device_path(volume: str) -> str:
    r"""Path that opens a volume: "E:" -> \\.\E:, "\\?\Volume{G}\" -> \\.\Volume{G}."""
    if volume.startswith(("\\\\?\\", "\\\\.\\")):
        return "\\\\.\\" + volume[4:].rstrip("\\")
    return f"\\\\.\\{volume}"


def dismount_volume(drive_letter: str) -> int:
    r"""Lock and dismount a volume and KEEP it locked.

    ``drive_letter`` is a letter ("E:") or a volume name
    ("\\?\Volume{GUID}\") for a volume without one.

    Returns the open volume handle; the lock lasts until the handle is
    closed with release_volumes(). Closing it right after the dismount (as
    older versions did) let Windows remount the file system as soon as anything
    touched the drive, and every further raw write into the volume then
    failed with error 5 (access denied).
    """
    # Open the volume (not the physical drive)
    volume_path = _volume_device_path(drive_letter)
    handle = kernel32.CreateFileW(
        volume_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if _handle_is_invalid(handle):
        raise _win_os_error(
            f"Cannot open volume {volume_path} for dismount",
            ctypes.get_last_error(),
        )
    try:
        try:
            _ioctl_no_data(handle, FSCTL_LOCK_VOLUME, "Lock before dismount")
            locked = True
        except OSError as exc:
            # Open handles (e.g. an Explorer window) block the lock. A forced
            # dismount invalidates them; the lock is taken right after.
            audit_log(f"Lock failed for {drive_letter}, forcing dismount: {exc}")
            locked = False
        _ioctl_no_data(handle, FSCTL_DISMOUNT_VOLUME, "FSCTL_DISMOUNT_VOLUME")
        if not locked:
            _ioctl_no_data(handle, FSCTL_LOCK_VOLUME, "Lock after forced dismount")
        audit_log(f"Dismounted volume {drive_letter} (kept locked)")
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    return handle


def release_volumes(handles: list[int]) -> None:
    """Close volume handles from dismount_volume(); closing ends the lock."""
    for h in handles:
        try:
            kernel32.CloseHandle(h)
        except Exception:  # noqa: BLE001 — releasing must never raise
            pass
    if handles:
        try:
            audit_log(f"Released {len(handles)} volume lock(s)")
        except Exception:  # noqa: BLE001 — a locked/full log must not break release
            pass


# ── Volumes without a drive letter ─────────────────────────────────────
#
# Win32_LogicalDisk only lists volumes with a drive letter. A partition
# without one (recovery, EFI, a second data partition) or one mounted in a
# folder stayed mounted during the wipe, so Windows could keep writing to
# it. The volume manager names every volume and the disks it lies on.

ERROR_NO_MORE_FILES = 18
ERROR_MORE_DATA = 234
_MAX_PATH = 260


class DISK_EXTENT(ctypes.Structure):
    _fields_ = [
        ("DiskNumber", wintypes.DWORD),
        ("StartingOffset", ctypes.c_longlong),
        ("ExtentLength", ctypes.c_longlong),
    ]


def _volume_disk_extents(count: int) -> ctypes.Structure:
    """A VOLUME_DISK_EXTENTS buffer with room for ``count`` extents."""

    class VOLUME_DISK_EXTENTS(ctypes.Structure):
        _fields_ = [
            ("NumberOfDiskExtents", wintypes.DWORD),
            ("Extents", DISK_EXTENT * count),
        ]

    return VOLUME_DISK_EXTENTS()


def _volume_names() -> list[str]:
    r"""Every volume Windows knows, as "\\?\Volume{GUID}\". Raises OSError."""
    buf = ctypes.create_unicode_buffer(_MAX_PATH)
    find = kernel32.FindFirstVolumeW(buf, _MAX_PATH)
    if _handle_is_invalid(find):
        err = ctypes.get_last_error()
        if err == ERROR_NO_MORE_FILES:
            return []
        raise _win_os_error("FindFirstVolumeW failed", err)
    names: list[str] = []
    try:
        while True:
            names.append(buf.value)
            if not kernel32.FindNextVolumeW(find, buf, _MAX_PATH):
                err = ctypes.get_last_error()
                if err == ERROR_NO_MORE_FILES:
                    return names
                raise _win_os_error("FindNextVolumeW failed", err)
    finally:
        kernel32.FindVolumeClose(find)


def _volume_disks(volume: str) -> set[int] | None:
    """Disk numbers ``volume`` lies on; None when that can't be read.

    The handle is opened without read or write access, and only the volume
    manager answers: nothing reaches the drive. CD drives and card readers
    without a card have no disk extents (None).
    """
    handle = kernel32.CreateFileW(
        _volume_device_path(volume), 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if _handle_is_invalid(handle):
        return None
    try:
        count = 1
        while True:
            extents = _volume_disk_extents(count)
            returned = wintypes.DWORD(0)
            if kernel32.DeviceIoControl(
                handle, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, None, 0,
                ctypes.byref(extents), ctypes.sizeof(extents),
                ctypes.byref(returned), None,
            ):
                n = min(int(extents.NumberOfDiskExtents), count)
                return {int(extents.Extents[i].DiskNumber) for i in range(n)}
            # A volume spanning several disks: retry with room for all.
            wanted = int(extents.NumberOfDiskExtents)
            if ctypes.get_last_error() != ERROR_MORE_DATA or not count < wanted <= 64:
                return None
            count = wanted
    finally:
        kernel32.CloseHandle(handle)


def _volume_at(mount_point: str) -> str | None:
    r"""Volume name mounted at ``mount_point`` ("E:\"); None when unknown."""
    buf = ctypes.create_unicode_buffer(_MAX_PATH)
    if not kernel32.GetVolumeNameForVolumeMountPointW(mount_point, buf, _MAX_PATH):
        return None
    return buf.value


def _unlettered_volumes_on_disk(index: int, letters: list[str]) -> list[str]:
    """Volumes on disk ``index`` that none of ``letters`` reaches.

    Raises UnsafeTargetError when the volumes cannot be listed or when the
    Windows volume lies on this disk (fail closed).
    """
    try:
        names = _volume_names()
    except OSError as exc:
        raise UnsafeTargetError(
            "Volumes konnten nicht gelesen werden / could not list the volumes: "
            f"{exc}"
        ) from exc
    on_disk: list[str] = []
    for name in names:
        disks = _volume_disks(name)
        if disks is None:
            audit_log(f"Volume {name}: disks not readable (no media?), skipped")
        elif index in disks:
            on_disk.append(name)
    if not on_disk:
        return []
    windows = _volume_at(_system_drive_letter() + "\\")
    if windows is not None and windows.lower() in {n.lower() for n in on_disk}:
        raise UnsafeTargetError(
            f"PhysicalDrive{index} trägt das Windows-Volume / holds the Windows volume"
        )
    covered = {
        volume.lower() for volume in (_volume_at(f"{letter}\\") for letter in letters)
        if volume
    }
    return [name for name in on_disk if name.lower() not in covered]


# ── Safety gate before writing ─────────────────────────────────────────

class UnsafeTargetError(Exception):
    """The selected disk must not be written to (changed, system, or busy)."""


def unique_disks(devices: list[DeviceInfo]) -> list[DeviceInfo]:
    """Keep the first entry per physical disk, preserving order.

    Two rows (E:, G:) of the same disk would otherwise wipe it twice.
    """
    seen: set[str] = set()
    result: list[DeviceInfo] = []
    for d in devices:
        if d.device_id not in seen:
            seen.add(d.device_id)
            result.append(d)
    return result


def _physical_index(device_id: str) -> int:
    prefix = "\\\\.\\PhysicalDrive"
    if not device_id.startswith(prefix) or not device_id[len(prefix):].isdigit():
        raise UnsafeTargetError(f"Unexpected device path: {device_id}")
    return int(device_id[len(prefix):])


def prepare_target(device: DeviceInfo) -> list[int]:
    """Last check right before the disk is opened for writing.

    Re-reads the disk behind ``device.device_id`` and refuses if it is no
    longer the disk the user selected (serial, size or model changed — e.g.
    stick swapped), if it is (or might be) the Windows disk, or if any of
    its volumes cannot be dismounted. Raises UnsafeTargetError.

    Returns the locked volume handles. The caller keeps them for the whole
    wipe and verification and then passes them to release_volumes().
    """
    if device.is_system_drive:
        raise UnsafeTargetError(
            f"{device.device_id} ist die Systemplatte / is the system disk"
        )
    index = _physical_index(device.device_id)
    try:
        c = _get_wmi_connection()
        disk = next(
            (d for d in c.Win32_DiskDrive() if int(d.Index) == index), None
        )
        sys_indices = _system_disk_indices(c)
        # Re-read the letters now: one added since listing must be
        # dismounted too, one that moved to another disk must not be.
        letters = _letters_on_disk(c, index)
    except UnsafeTargetError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed
        raise UnsafeTargetError(
            f"Laufwerk konnte nicht erneut geprüft werden / "
            f"could not re-check the drive: {exc}"
        ) from exc

    if disk is None:
        raise UnsafeTargetError(
            f"{device.device_id} ist nicht mehr vorhanden / is no longer present"
        )
    serial = (getattr(disk, "SerialNumber", "") or "").strip()
    model = (getattr(disk, "Model", "") or "").strip()
    size = int(getattr(disk, "Size", 0) or 0)
    if (serial, model, size) != (
        device.serial_number.strip(), device.model.strip(), device.capacity_bytes
    ):
        raise UnsafeTargetError(
            f"{device.device_id} ist nicht mehr das ausgewählte Gerät / "
            f"is no longer the selected device (now: {model} {serial})"
        )
    if _blocked_as_system(index, device.connection_type, sys_indices):
        raise UnsafeTargetError(
            f"{device.device_id} ist die Systemplatte / is the system disk"
        )
    # Volumes without a letter or mounted in a folder: looked up before
    # anything is locked, dismounted and kept locked like the letters.
    unlettered = _unlettered_volumes_on_disk(index, letters)

    locks: list[int] = []
    for volume in [*letters, *unlettered]:
        name = volume if volume in letters else (
            f"Volume ohne Laufwerksbuchstaben / volume without a drive letter {volume}"
        )
        try:
            locks.append(dismount_volume(volume))
        except OSError as exc:
            release_volumes(locks)
            admin = (
                "Administratorrechte erforderlich / administrator rights "
                "required — "
                if getattr(exc, "winerror", None) == ERROR_ACCESS_DENIED else ""
            )
            raise UnsafeTargetError(
                f"{admin}{name} konnte nicht ausgehängt werden / "
                f"could not be dismounted: {exc}"
            ) from exc
        except BaseException:
            release_volumes(locks)
            raise
    audit_log(
        f"Target verified, dismounted and locked: {device.device_id} "
        f"({len(letters)} letter(s), {len(unlettered)} volume(s) without one)"
    )
    return locks
