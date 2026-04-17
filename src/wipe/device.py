"""Device detection and raw disk access for Windows removable storage."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import subprocess
from dataclasses import dataclass, field
from typing import Optional

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

    @property
    def capacity_gb(self) -> float:
        return round(self.capacity_bytes / (1024 ** 3), 2)

    @property
    def safe_to_wipe(self) -> bool:
        return not self.is_system_drive


# ── WMI helpers ────────────────────────────────────────────────────────

def _get_wmi_connection() -> wmi.WMI:
    return wmi.WMI()


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
    """Check if the volume is BitLocker-encrypted via manage-bde."""
    try:
        result = subprocess.run(
            ["manage-bde", "-status", drive_letter],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout.lower()
        # If protection is on or encryption is in progress, flag it
        if "protection on" in output or "percentage encrypted" in output:
            if "fully decrypted" not in output:
                return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return False


def _check_active_processes(drive_letter: str) -> bool:
    """Rough check: see if any handles are open on the volume via openfiles or handle count."""
    try:
        result = subprocess.run(
            ["handle.exe", "-accepteula", drive_letter + "\\"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # If handle.exe finds results it returns lines with PIDs
        lines = [
            ln for ln in result.stdout.strip().splitlines()
            if drive_letter.upper() in ln.upper() and "pid:" in ln.lower()
        ]
        return len(lines) > 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # handle.exe not available — fall back to silent
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

            is_system = drive_letter.upper() == sys_letter.upper()
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
            audit_log(f"Error processing drive {drive_letter}: {exc}")
            continue

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


def dismount_volume(drive_letter: str) -> None:
    """Dismount a volume by its drive letter (e.g. 'E:') so raw writes can proceed."""
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
        _ioctl_no_data(handle, FSCTL_LOCK_VOLUME, "Lock before dismount")
        _ioctl_no_data(handle, FSCTL_DISMOUNT_VOLUME, "FSCTL_DISMOUNT_VOLUME")
        audit_log(f"Dismounted volume {drive_letter}")
    finally:
        kernel32.CloseHandle(handle)
