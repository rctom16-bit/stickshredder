"""Device detection and raw disk access for Windows removable storage."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import subprocess
from dataclasses import dataclass, field
from typing import Optional

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
FSCTL_UNLOCK_VOLUME = 0x0009001C
FSCTL_DISMOUNT_VOLUME = 0x00090020

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
    connection_type: str             # USB / SATA / NVMe / Unknown
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


def _storage_system_disks() -> set[int]:
    """Disk numbers the Windows storage stack flags as boot or system disk.

    IsSystem marks the disk holding the EFI/system partition the PC actually
    booted from — which can be a different disk than the one with Windows
    on it. Best-effort: returns an empty set if the query fails (the
    Windows-folder check still applies).
    """
    try:
        pythoncom.CoInitialize()
        storage = wmi.WMI(namespace=r"root\Microsoft\Windows\Storage")
        return {
            int(d.Number) for d in storage.MSFT_Disk()
            if d.IsSystem or d.IsBoot
        }
    except Exception as exc:  # noqa: BLE001
        audit_log(f"Storage system-disk query failed: {exc}")
        return set()


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


def _physical_drive_index_for_letter(
    c: wmi.WMI, drive_letter: str
) -> Optional[int]:
    """Map a drive letter like 'E:' to a PhysicalDisk index via WMI."""
    try:
        # Logical disk -> Partition -> Physical disk
        for assoc in c.Win32_LogicalDiskToPartition():
            if assoc.Dependent.DeviceID.upper() == drive_letter.upper():
                partition_id = assoc.Antecedent.DeviceID
                for disk_assoc in c.Win32_DiskDriveToDiskPartition():
                    if disk_assoc.Dependent.DeviceID == partition_id:
                        return disk_assoc.Antecedent.Index
    except Exception as exc:
        audit_log(f"Failed to resolve physical drive for {drive_letter}: {exc}")
    return None


def _system_disk_indices(c: wmi.WMI) -> set[int] | None:
    """Return the physical disk indices hosting the Windows volume.

    Every partition on these disks must be treated as system: wiping any
    of them writes to the whole physical disk, i.e. destroys the Windows
    installation. Returns None when the mapping cannot be determined, so
    callers can fail closed.
    """
    sys_letter = _system_drive_letter().upper()
    try:
        partition_ids = {
            assoc.Antecedent.DeviceID
            for assoc in c.Win32_LogicalDiskToPartition()
            if assoc.Dependent.DeviceID.upper() == sys_letter
        }
        indices = {
            int(assoc.Antecedent.Index)
            for assoc in c.Win32_DiskDriveToDiskPartition()
            if assoc.Dependent.DeviceID in partition_ids
        }
    except Exception as exc:  # noqa: BLE001 — fail closed below
        audit_log(f"System disk lookup failed: {exc}")
        return None
    if not indices:
        return None
    return indices | _storage_system_disks()


def _letters_on_disk(c: wmi.WMI, index: int) -> list[str]:
    """Drive letters currently mounted from partitions of physical disk ``index``."""
    partition_ids = {
        assoc.Dependent.DeviceID
        for assoc in c.Win32_DiskDriveToDiskPartition()
        if int(assoc.Antecedent.Index) == index
    }
    return sorted({
        assoc.Dependent.DeviceID.upper()
        for assoc in c.Win32_LogicalDiskToPartition()
        if assoc.Antecedent.DeviceID in partition_ids
    })


def _connection_type_from_interface(interface_type: str | None) -> str:
    if not interface_type:
        return "Unknown"
    iface = interface_type.upper()
    if "USB" in iface:
        return "USB"
    if "NVME" in iface or "SCSI" in iface:
        return "NVMe"
    if "IDE" in iface or "SATA" in iface:
        return "SATA"
    return interface_type


def _check_bitlocker(drive_letter: str) -> bool:
    """Check if the volume is BitLocker-encrypted via manage-bde. Best-effort."""
    try:
        result = subprocess.run(
            ["manage-bde", "-status", drive_letter],
            capture_output=True,
            text=True,
            # Console tools print in the OEM code page (cp850 on German
            # Windows); decoding with the ANSI default crashed the reader
            # thread on umlauts and left stdout empty.
            encoding="oem",
            errors="replace",
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = (result.stdout or "").lower()
        if "protection on" in output or "percentage encrypted" in output:
            if "fully decrypted" not in output:
                return True
    except Exception:  # noqa: BLE001 — this is a non-safety-critical probe
        pass
    return False


def _check_active_processes(drive_letter: str) -> bool:
    """Check for open handles on a volume. Best-effort, never raises."""
    if not drive_letter:
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
    except Exception:  # noqa: BLE001 — probe must never break enumeration
        return False


# ── Public API ─────────────────────────────────────────────────────────

def list_devices() -> list[DeviceInfo]:
    """Enumerate removable (and optionally internal non-system) storage devices."""
    devices: list[DeviceInfo] = []
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

    sys_indices = _system_disk_indices(c)
    audit_log(
        f"System disk indices: {sorted(sys_indices)}" if sys_indices
        else "System disk could not be determined — all non-USB disks blocked"
    )

    # Walk logical disks (the ones with drive letters)
    try:
        logical_disks = c.Win32_LogicalDisk()
    except Exception as exc:
        audit_log(f"Failed to enumerate logical disks: {exc}")
        return devices

    for ldisk in logical_disks:
        drive_letter = ldisk.DeviceID  # e.g. "E:"
        try:
            phys_index = _physical_drive_index_for_letter(c, drive_letter)
            if phys_index is None:
                continue
            phys_disk = disk_map.get(phys_index)
            if phys_disk is None:
                continue

            interface_type = getattr(phys_disk, "InterfaceType", None)
            connection = _connection_type_from_interface(interface_type)
            media_type = (getattr(phys_disk, "MediaType", "") or "").lower()

            is_removable = (
                connection == "USB"
                or "removable" in media_type
                or getattr(ldisk, "DriveType", 0) == 2  # DriveType 2 = Removable
            )

            # Block every partition of the Windows disk, not just C:.
            # If the Windows disk is unknown, block every non-USB disk.
            is_system = (
                drive_letter.upper() == sys_letter.upper()
                or (phys_index in sys_indices if sys_indices
                    else connection != "USB")
            )
            is_internal = connection != "USB" and not is_removable

            serial = (getattr(phys_disk, "SerialNumber", "") or "").strip()
            model = (getattr(phys_disk, "Model", "") or "").strip()
            capacity = int(getattr(phys_disk, "Size", 0) or 0)
            filesystem = (getattr(ldisk, "FileSystem", "") or "").strip() or "RAW"

            # Count partitions belonging to this physical drive
            part_count = 0
            try:
                for _ in c.Win32_DiskDriveToDiskPartition():
                    if _.Antecedent.Index == phys_index:
                        part_count += 1
            except Exception:
                pass

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

    letters_by_disk: dict[str, list[str]] = {}
    for d in devices:
        letters_by_disk.setdefault(d.device_id, []).append(d.drive_letter.upper())
    for d in devices:
        d.disk_letters = sorted(letters_by_disk[d.device_id])

    return devices


# ── Raw disk access helpers ────────────────────────────────────────────

def open_physical_drive(device_id: str) -> int:
    """Open a physical drive for raw read/write. Requires admin privileges.

    Returns the Win32 HANDLE as an integer.
    Raises OSError on failure.
    """
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
        err = ctypes.get_last_error()
        raise OSError(f"{label} failed (error {err})")
    audit_log(f"{label} succeeded (handle={handle})")


def lock_volume(handle: int) -> None:
    _ioctl_no_data(handle, FSCTL_LOCK_VOLUME, "FSCTL_LOCK_VOLUME")


def unlock_volume(handle: int) -> None:
    _ioctl_no_data(handle, FSCTL_UNLOCK_VOLUME, "FSCTL_UNLOCK_VOLUME")


def dismount_volume(drive_letter: str) -> int:
    """Lock and dismount a volume (e.g. 'E:') and KEEP it locked.

    Returns the open volume handle; the lock lasts until the handle is
    closed with release_volumes(). Closing it right after the dismount (as
    up to 1.1.1) let Windows remount the file system as soon as anything
    touched the drive, and every further raw write into the volume then
    failed with error 5 (access denied).
    """
    # Open the volume (not the physical drive)
    volume_path = f"\\\\.\\{drive_letter}"
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
        err = ctypes.get_last_error()
        raise OSError(
            f"Cannot open volume {volume_path} for dismount (error {err})"
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
        audit_log(f"Released {len(handles)} volume lock(s)")


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
    if (index in sys_indices) if sys_indices else device.connection_type != "USB":
        raise UnsafeTargetError(
            f"{device.device_id} ist die Systemplatte / is the system disk"
        )

    locks: list[int] = []
    for letter in letters:
        try:
            locks.append(dismount_volume(letter))
        except OSError as exc:
            release_volumes(locks)
            raise UnsafeTargetError(
                f"{letter} konnte nicht ausgehängt werden / "
                f"could not be dismounted: {exc}"
            ) from exc
    audit_log(f"Target verified, dismounted and locked: {device.device_id}")
    return locks
