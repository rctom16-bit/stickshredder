"""Tests for wipe.device — DeviceInfo, list_devices, system drive detection."""

import ctypes
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock ctypes.windll and wmi before importing the module.
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()
ctypes.windll.kernel32 = MagicMock()

# wmi is a Windows-only package; provide a stub for CI.
sys.modules.setdefault("wmi", MagicMock())

from wipe.device import DeviceInfo, list_devices, is_safe_to_wipe, _system_drive_letter, _connection_type_from_interface


# ── DeviceInfo.capacity_gb ────────────────────────────────────────────

def test_capacity_gb_32gb():
    di = _make_device(capacity_bytes=32 * 1024**3)
    assert di.capacity_gb == 32.0


def test_capacity_gb_fraction():
    di = _make_device(capacity_bytes=int(15.5 * 1024**3))
    assert di.capacity_gb == 15.5


def test_capacity_gb_zero():
    di = _make_device(capacity_bytes=0)
    assert di.capacity_gb == 0.0


# ── DeviceInfo.safe_to_wipe ──────────────────────────────────────────

def test_safe_to_wipe_true():
    di = _make_device(is_system_drive=False)
    assert di.safe_to_wipe is True


def test_safe_to_wipe_false_system():
    di = _make_device(is_system_drive=True)
    assert di.safe_to_wipe is False


# ── _system_drive_letter ─────────────────────────────────────────────

def test_system_drive_letter_from_api(monkeypatch):
    """_system_drive_letter reads the boot volume via GetWindowsDirectoryW."""
    import wipe.device as device_mod

    def fake_get_windows_dir(buf, size):
        # Write "D:\Windows" into the caller-provided buffer and return
        # the character count (mirroring the real Win32 API contract).
        path = "D:\\Windows"
        for i, ch in enumerate(path):
            buf[i] = ch
        buf[len(path)] = "\x00"
        return len(path)

    monkeypatch.setattr(
        device_mod.kernel32, "GetWindowsDirectoryW", fake_get_windows_dir
    )
    assert _system_drive_letter() == "D:"


def test_system_drive_letter_fallback_on_api_failure(monkeypatch):
    """If GetWindowsDirectoryW raises, _system_drive_letter falls back to 'C:'."""
    import wipe.device as device_mod

    def raising(buf, size):
        raise OSError("simulated API failure")

    monkeypatch.setattr(
        device_mod.kernel32, "GetWindowsDirectoryW", raising
    )
    assert _system_drive_letter() == "C:"


def test_system_drive_letter_real_call():
    """Sanity check against the real API: returns a two-char drive like 'C:'."""
    result = _system_drive_letter()
    assert len(result) == 2 and result[1] == ":"


# ── _connection_type_from_interface ──────────────────────────────────

def test_connection_usb():
    assert _connection_type_from_interface("USB") == "USB"


def test_connection_nvme():
    assert _connection_type_from_interface("SCSI") == "NVMe"


def test_connection_sata():
    assert _connection_type_from_interface("IDE") == "SATA"


def test_connection_none():
    assert _connection_type_from_interface(None) == "Unknown"


# ── list_devices with mocked WMI ─────────────────────────────────────

@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._get_wmi_connection")
def test_list_devices_returns_usb_device(
    mock_wmi_conn, mock_sysletter, mock_bl, mock_ap, mock_log
):

    # Build a fake WMI graph
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    # Physical disk
    phys_disk = MagicMock()
    phys_disk.Index = 1
    phys_disk.InterfaceType = "USB"
    phys_disk.MediaType = "Removable Media"
    phys_disk.SerialNumber = "SN-FAKE-123"
    phys_disk.Model = "FakeDisk 32GB"
    phys_disk.Size = str(32 * 1024**3)
    mock_c.Win32_DiskDrive.return_value = [phys_disk]

    # Logical disk
    ldisk = MagicMock()
    ldisk.DeviceID = "E:"
    ldisk.DriveType = 2
    ldisk.FileSystem = "FAT32"
    mock_c.Win32_LogicalDisk.return_value = [ldisk]

    # Associations
    assoc_ld = MagicMock()
    assoc_ld.Dependent.DeviceID = "E:"
    assoc_ld.Antecedent.DeviceID = "Disk #1, Partition #0"
    mock_c.Win32_LogicalDiskToPartition.return_value = [assoc_ld]

    assoc_dd = MagicMock()
    assoc_dd.Dependent.DeviceID = "Disk #1, Partition #0"
    assoc_dd.Antecedent.DeviceID = r"\\.\PhysicalDrive1"
    assoc_dd.Antecedent.Index = 1
    mock_c.Win32_DiskDriveToDiskPartition.return_value = [assoc_dd]

    devices = list_devices()
    assert len(devices) == 1
    dev = devices[0]
    assert dev.drive_letter == "E:"
    assert dev.connection_type == "USB"
    assert dev.is_removable is True
    assert dev.safe_to_wipe is True


@patch("wipe.device.audit_log")
@patch("wipe.device._get_wmi_connection", side_effect=Exception("WMI unavailable"))
def test_list_devices_wmi_failure(mock_wmi_conn, mock_log):
    devices = list_devices()
    assert devices == []


# ── System physical drive protection ─────────────────────────────────
#
# Regression tests for a dangerous class of bug: the system-drive check
# used to be per-drive-letter, but wipes target whole physical disks.
# A data partition (e.g. D:) that shares a physical disk with C:
# (Windows) must be flagged is_system_drive=True — otherwise a wipe on
# D: would destroy Windows.


@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._get_wmi_connection")
def test_sibling_partition_on_system_physical_disk_is_flagged(
    mock_wmi_conn, mock_sysletter, mock_bl, mock_ap, mock_log
):
    """D: partition sharing PhysicalDrive0 with C: must be is_system_drive=True."""
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    # Single physical disk: PhysicalDrive0 (the Windows disk)
    phys_disk = MagicMock()
    phys_disk.Index = 0
    phys_disk.InterfaceType = "SATA"
    phys_disk.MediaType = "Fixed hard disk media"
    phys_disk.SerialNumber = "WIN-DISK-SN"
    phys_disk.Model = "Samsung SSD 980"
    phys_disk.Size = str(500 * 1024**3)
    mock_c.Win32_DiskDrive.return_value = [phys_disk]

    # Two logical disks both on PhysicalDrive0: C: (Windows) + D: (data)
    c_ldisk = MagicMock()
    c_ldisk.DeviceID = "C:"
    c_ldisk.DriveType = 3  # Fixed
    c_ldisk.FileSystem = "NTFS"
    d_ldisk = MagicMock()
    d_ldisk.DeviceID = "D:"
    d_ldisk.DriveType = 3
    d_ldisk.FileSystem = "NTFS"
    mock_c.Win32_LogicalDisk.return_value = [c_ldisk, d_ldisk]

    # Associations: both letters map to PhysicalDrive0
    c_ld = MagicMock()
    c_ld.Dependent.DeviceID = "C:"
    c_ld.Antecedent.DeviceID = "Disk #0, Partition #1"
    d_ld = MagicMock()
    d_ld.Dependent.DeviceID = "D:"
    d_ld.Antecedent.DeviceID = "Disk #0, Partition #2"
    mock_c.Win32_LogicalDiskToPartition.return_value = [c_ld, d_ld]

    c_dd = MagicMock()
    c_dd.Dependent.DeviceID = "Disk #0, Partition #1"
    c_dd.Antecedent.Index = 0
    d_dd = MagicMock()
    d_dd.Dependent.DeviceID = "Disk #0, Partition #2"
    d_dd.Antecedent.Index = 0
    mock_c.Win32_DiskDriveToDiskPartition.return_value = [c_dd, d_dd]

    devices = list_devices()
    by_letter = {d.drive_letter: d for d in devices}

    assert by_letter["C:"].is_system_drive is True
    assert by_letter["D:"].is_system_drive is True, (
        "D: shares PhysicalDrive0 with Windows — wiping it would destroy "
        "the system disk, so it MUST be flagged as a system drive."
    )
    assert by_letter["C:"].safe_to_wipe is False
    assert by_letter["D:"].safe_to_wipe is False


@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="D:")
@patch("wipe.device._get_wmi_connection")
def test_windows_on_d_drive_protects_d(
    mock_wmi_conn, mock_sysletter, mock_bl, mock_ap, mock_log
):
    """If Windows is installed on D:, then D: must be is_system_drive=True."""
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    # PhysicalDrive0 holds Windows on D:
    sys_disk = MagicMock()
    sys_disk.Index = 0
    sys_disk.InterfaceType = "SATA"
    sys_disk.MediaType = "Fixed hard disk media"
    sys_disk.SerialNumber = "SYS-SN"
    sys_disk.Model = "System SSD"
    sys_disk.Size = str(500 * 1024**3)

    # PhysicalDrive1 is a USB stick on E:
    usb_disk = MagicMock()
    usb_disk.Index = 1
    usb_disk.InterfaceType = "USB"
    usb_disk.MediaType = "Removable Media"
    usb_disk.SerialNumber = "USB-SN"
    usb_disk.Model = "Kingston DataTraveler"
    usb_disk.Size = str(32 * 1024**3)

    mock_c.Win32_DiskDrive.return_value = [sys_disk, usb_disk]

    d_ldisk = MagicMock()
    d_ldisk.DeviceID = "D:"
    d_ldisk.DriveType = 3
    d_ldisk.FileSystem = "NTFS"
    e_ldisk = MagicMock()
    e_ldisk.DeviceID = "E:"
    e_ldisk.DriveType = 2  # Removable
    e_ldisk.FileSystem = "FAT32"
    mock_c.Win32_LogicalDisk.return_value = [d_ldisk, e_ldisk]

    d_ld = MagicMock()
    d_ld.Dependent.DeviceID = "D:"
    d_ld.Antecedent.DeviceID = "Disk #0, Partition #1"
    e_ld = MagicMock()
    e_ld.Dependent.DeviceID = "E:"
    e_ld.Antecedent.DeviceID = "Disk #1, Partition #0"
    mock_c.Win32_LogicalDiskToPartition.return_value = [d_ld, e_ld]

    d_dd = MagicMock()
    d_dd.Dependent.DeviceID = "Disk #0, Partition #1"
    d_dd.Antecedent.Index = 0
    e_dd = MagicMock()
    e_dd.Dependent.DeviceID = "Disk #1, Partition #0"
    e_dd.Antecedent.Index = 1
    mock_c.Win32_DiskDriveToDiskPartition.return_value = [d_dd, e_dd]

    devices = list_devices()
    by_letter = {d.drive_letter: d for d in devices}

    assert by_letter["D:"].is_system_drive is True
    assert by_letter["E:"].is_system_drive is False
    assert by_letter["E:"].safe_to_wipe is True


# ── open_physical_drive wipe-time safeguard ──────────────────────────


def test_open_physical_drive_refuses_system_disk(monkeypatch):
    """Last-line-of-defense: refuse to open the system PhysicalDrive even
    if the caller didn't check is_system_drive first.
    """
    import wipe.device as device_mod

    # Pretend the system sits on PhysicalDrive0
    monkeypatch.setattr(
        device_mod, "_resolve_system_physical_drive_index", lambda: 0
    )

    # CreateFileW must not be reached — any actual open would be a bug.
    def must_not_be_called(*args, **kwargs):
        raise AssertionError(
            "CreateFileW was called for the system disk — safeguard failed!"
        )

    monkeypatch.setattr(device_mod.kernel32, "CreateFileW", must_not_be_called)

    with pytest.raises(PermissionError, match=r"system.*drive|PhysicalDrive0"):
        device_mod.open_physical_drive(r"\\.\PhysicalDrive0")


def test_open_physical_drive_allows_non_system_disk(monkeypatch):
    """Non-system physical drives must still open normally."""
    import wipe.device as device_mod

    monkeypatch.setattr(
        device_mod, "_resolve_system_physical_drive_index", lambda: 0
    )

    # Stub CreateFileW to return a fake-but-valid handle (anything != -1/0).
    monkeypatch.setattr(
        device_mod.kernel32, "CreateFileW", lambda *a, **kw: 1234
    )

    handle = device_mod.open_physical_drive(r"\\.\PhysicalDrive1")
    assert handle == 1234


# ── Resolution-failure fallback / extra list_devices scenarios ───────


@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._resolve_system_physical_drive_index", return_value=None)
@patch("wipe.device._get_wmi_connection")
def test_physical_drive_index_resolution_failure_falls_back_to_letter_check(
    mock_wmi_conn, mock_resolve, mock_sysletter, mock_bl, mock_ap, mock_log
):
    """When _resolve_system_physical_drive_index returns None the system
    drive must still be flagged via the letter fallback so that resolution
    failures cannot silently remove all protection.
    """
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    phys_disk = MagicMock()
    phys_disk.Index = 0
    phys_disk.InterfaceType = "SATA"
    phys_disk.MediaType = "Fixed hard disk media"
    phys_disk.SerialNumber = "SYS-SN"
    phys_disk.Model = "System SSD"
    phys_disk.Size = str(500 * 1024**3)
    mock_c.Win32_DiskDrive.return_value = [phys_disk]

    c_ldisk = MagicMock()
    c_ldisk.DeviceID = "C:"
    c_ldisk.DriveType = 3
    c_ldisk.FileSystem = "NTFS"
    mock_c.Win32_LogicalDisk.return_value = [c_ldisk]

    c_ld = MagicMock()
    c_ld.Dependent.DeviceID = "C:"
    c_ld.Antecedent.DeviceID = "Disk #0, Partition #0"
    mock_c.Win32_LogicalDiskToPartition.return_value = [c_ld]

    c_dd = MagicMock()
    c_dd.Dependent.DeviceID = "Disk #0, Partition #0"
    c_dd.Antecedent.Index = 0
    mock_c.Win32_DiskDriveToDiskPartition.return_value = [c_dd]

    devices = list_devices()
    by_letter = {d.drive_letter: d for d in devices}

    assert by_letter["C:"].is_system_drive is True, (
        "Letter fallback must flag C: as system when physical-index resolution returns None."
    )
    assert by_letter["C:"].safe_to_wipe is False


@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._get_wmi_connection")
def test_usb_stick_on_different_physical_disk_is_safe(
    mock_wmi_conn, mock_sysletter, mock_bl, mock_ap, mock_log
):
    """PhysicalDrive0=C: (system), PhysicalDrive1=E: (USB). The USB must
    be is_system_drive=False and safe_to_wipe=True.
    """
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    sys_disk = MagicMock()
    sys_disk.Index = 0
    sys_disk.InterfaceType = "SATA"
    sys_disk.MediaType = "Fixed hard disk media"
    sys_disk.SerialNumber = "SYS-SN"
    sys_disk.Model = "System SSD"
    sys_disk.Size = str(500 * 1024**3)

    usb_disk = MagicMock()
    usb_disk.Index = 1
    usb_disk.InterfaceType = "USB"
    usb_disk.MediaType = "Removable Media"
    usb_disk.SerialNumber = "USB-SN"
    usb_disk.Model = "SanDisk Cruzer"
    usb_disk.Size = str(64 * 1024**3)

    mock_c.Win32_DiskDrive.return_value = [sys_disk, usb_disk]

    c_ldisk = MagicMock()
    c_ldisk.DeviceID = "C:"
    c_ldisk.DriveType = 3
    c_ldisk.FileSystem = "NTFS"
    e_ldisk = MagicMock()
    e_ldisk.DeviceID = "E:"
    e_ldisk.DriveType = 2
    e_ldisk.FileSystem = "FAT32"
    mock_c.Win32_LogicalDisk.return_value = [c_ldisk, e_ldisk]

    c_ld = MagicMock()
    c_ld.Dependent.DeviceID = "C:"
    c_ld.Antecedent.DeviceID = "Disk #0, Partition #0"
    e_ld = MagicMock()
    e_ld.Dependent.DeviceID = "E:"
    e_ld.Antecedent.DeviceID = "Disk #1, Partition #0"
    mock_c.Win32_LogicalDiskToPartition.return_value = [c_ld, e_ld]

    c_dd = MagicMock()
    c_dd.Dependent.DeviceID = "Disk #0, Partition #0"
    c_dd.Antecedent.Index = 0
    e_dd = MagicMock()
    e_dd.Dependent.DeviceID = "Disk #1, Partition #0"
    e_dd.Antecedent.Index = 1
    mock_c.Win32_DiskDriveToDiskPartition.return_value = [c_dd, e_dd]

    devices = list_devices()
    by_letter = {d.drive_letter: d for d in devices}

    assert by_letter["C:"].is_system_drive is True
    assert by_letter["E:"].is_system_drive is False, (
        "USB stick on a different physical disk must NOT be flagged as system."
    )
    assert by_letter["E:"].safe_to_wipe is True


@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._get_wmi_connection")
def test_three_partitions_on_system_disk_all_flagged(
    mock_wmi_conn, mock_sysletter, mock_bl, mock_ap, mock_log
):
    """PhysicalDrive0 hosts C:, D:, and R:. All three must be is_system_drive=True."""
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    phys_disk = MagicMock()
    phys_disk.Index = 0
    phys_disk.InterfaceType = "SATA"
    phys_disk.MediaType = "Fixed hard disk media"
    phys_disk.SerialNumber = "SYS-SN"
    phys_disk.Model = "System SSD"
    phys_disk.Size = str(1000 * 1024**3)
    mock_c.Win32_DiskDrive.return_value = [phys_disk]

    letters = ["C:", "D:", "R:"]
    ldisks = []
    for letter in letters:
        ld = MagicMock()
        ld.DeviceID = letter
        ld.DriveType = 3
        ld.FileSystem = "NTFS"
        ldisks.append(ld)
    mock_c.Win32_LogicalDisk.return_value = ldisks

    ld_assocs = []
    dd_assocs = []
    for i, letter in enumerate(letters):
        part_id = f"Disk #0, Partition #{i}"
        ld_a = MagicMock()
        ld_a.Dependent.DeviceID = letter
        ld_a.Antecedent.DeviceID = part_id
        ld_assocs.append(ld_a)

        dd_a = MagicMock()
        dd_a.Dependent.DeviceID = part_id
        dd_a.Antecedent.Index = 0
        dd_assocs.append(dd_a)

    mock_c.Win32_LogicalDiskToPartition.return_value = ld_assocs
    mock_c.Win32_DiskDriveToDiskPartition.return_value = dd_assocs

    devices = list_devices()
    by_letter = {d.drive_letter: d for d in devices}

    for letter in letters:
        assert by_letter[letter].is_system_drive is True, (
            f"{letter} shares PhysicalDrive0 with Windows and must be flagged as system."
        )
        assert by_letter[letter].safe_to_wipe is False


# ── open_physical_drive parametrized / malformed-id tests ────────────


@pytest.mark.parametrize("device_id,sys_index,expect_error", [
    (r"\\.\PhysicalDrive0",  0,  True),
    (r"\\.\PhysicalDrive10", 10, True),
    (r"\\.\PhysicalDrive99", 99, True),
    (r"\\.\PhysicalDrive1",  0,  False),   # sys=0, opening 1 → allowed
])
def test_open_physical_drive_parses_device_id_correctly(
    monkeypatch, device_id, sys_index, expect_error
):
    """Trailing integer is parsed correctly for both block and allow paths."""
    import wipe.device as device_mod

    monkeypatch.setattr(
        device_mod, "_resolve_system_physical_drive_index", lambda: sys_index
    )
    monkeypatch.setattr(
        device_mod.kernel32, "CreateFileW", lambda *a, **kw: 4242
    )

    if expect_error:
        with pytest.raises(PermissionError):
            device_mod.open_physical_drive(device_id)
    else:
        handle = device_mod.open_physical_drive(device_id)
        assert handle == 4242


@pytest.mark.parametrize("device_id", [
    "",
    "not a device",
    r"\\.\PhysicalDriveABC",
])
def test_open_physical_drive_malformed_device_id_does_not_crash(
    monkeypatch, device_id
):
    """Malformed device IDs must not leak AttributeError / ValueError /
    IndexError / TypeError from the safeguard's parsing code.  The only
    acceptable outcomes are PermissionError, OSError, or a clean fall-through
    that reaches CreateFileW (which we stub to return INVALID_HANDLE_VALUE so
    open_physical_drive then raises OSError as normal).
    """
    import wipe.device as device_mod

    monkeypatch.setattr(
        device_mod, "_resolve_system_physical_drive_index", lambda: 0
    )
    # Stub CreateFileW to return INVALID_HANDLE_VALUE (-1) so the function
    # raises OSError rather than succeeding with a fake handle.
    monkeypatch.setattr(
        device_mod.kernel32, "CreateFileW", lambda *a, **kw: -1
    )

    try:
        device_mod.open_physical_drive(device_id)
    except (PermissionError, OSError):
        # Both are acceptable outcomes.
        pass
    except Exception as exc:
        raise AssertionError(
            f"open_physical_drive({device_id!r}) raised unexpected "
            f"{type(exc).__name__}: {exc}"
        ) from exc


# ── Helper ────────────────────────────────────────────────────────────

def _make_device(**overrides) -> DeviceInfo:
    defaults = dict(
        drive_letter="E:",
        device_id=r"\\.\PhysicalDrive1",
        model="TestDisk",
        serial_number="SN123",
        capacity_bytes=32 * 1024**3,
        filesystem="FAT32",
        connection_type="USB",
        is_removable=True,
        is_system_drive=False,
    )
    defaults.update(overrides)
    return DeviceInfo(**defaults)


# ── Letterless / internal disk enumeration (v1.2) ─────────────────────

@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._get_wmi_connection")
def test_letterless_internal_disk_is_enumerated(
    mock_wmi_conn, mock_sysletter, mock_bl, mock_ap, mock_log
):
    """An internal SATA disk with no drive letter must still appear (as RAW)."""
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    phys = MagicMock()
    phys.Index = 2
    phys.InterfaceType = "SATA"
    phys.MediaType = "Fixed hard disk media"
    phys.SerialNumber = "DATA-DISK-SN"
    phys.Model = "Seagate Barracuda 2TB"
    phys.Size = str(2 * 1024**4)
    mock_c.Win32_DiskDrive.return_value = [phys]

    # No lettered volumes at all.
    mock_c.Win32_LogicalDisk.return_value = []
    mock_c.Win32_LogicalDiskToPartition.return_value = []
    mock_c.Win32_DiskDriveToDiskPartition.return_value = []

    devices = list_devices()
    assert len(devices) == 1
    dev = devices[0]
    assert dev.drive_letter == ""
    assert dev.device_id == r"\\.\PhysicalDrive2"
    assert dev.is_internal is True
    assert dev.is_removable is False
    assert dev.filesystem == "RAW"
    assert dev.is_system_drive is False


@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._get_wmi_connection")
def test_lettered_disk_not_duplicated_as_letterless(
    mock_wmi_conn, mock_sysletter, mock_bl, mock_ap, mock_log
):
    """A USB disk with a letter must appear once, not also as a letterless entry."""
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    phys = MagicMock()
    phys.Index = 1
    phys.InterfaceType = "USB"
    phys.MediaType = "Removable Media"
    phys.SerialNumber = "USB-SN"
    phys.Model = "Kingston 64GB"
    phys.Size = str(64 * 1024**3)
    mock_c.Win32_DiskDrive.return_value = [phys]

    ldisk = MagicMock()
    ldisk.DeviceID = "E:"
    ldisk.DriveType = 2
    ldisk.FileSystem = "exFAT"
    mock_c.Win32_LogicalDisk.return_value = [ldisk]

    assoc_ld = MagicMock()
    assoc_ld.Dependent.DeviceID = "E:"
    assoc_ld.Antecedent.DeviceID = "Disk #1, Partition #0"
    mock_c.Win32_LogicalDiskToPartition.return_value = [assoc_ld]

    assoc_dd = MagicMock()
    assoc_dd.Dependent.DeviceID = "Disk #1, Partition #0"
    assoc_dd.Antecedent.Index = 1
    mock_c.Win32_DiskDriveToDiskPartition.return_value = [assoc_dd]

    devices = list_devices()
    assert len(devices) == 1
    assert devices[0].drive_letter == "E:"


# ── is_safe_to_wipe gate (v1.2) ───────────────────────────────────────

def test_is_safe_to_wipe_refuses_system_drive():
    dev = _make_device(is_system_drive=True)
    safe, reason = is_safe_to_wipe(dev, allow_internal=True)
    assert safe is False
    assert "system" in reason.lower()


def test_is_safe_to_wipe_allows_usb():
    dev = _make_device(is_removable=True, is_internal=False)
    safe, reason = is_safe_to_wipe(dev)
    assert safe is True
    assert reason == ""


def test_is_safe_to_wipe_refuses_internal_without_flag():
    dev = _make_device(is_removable=False, is_internal=True, is_system_drive=False)
    safe, reason = is_safe_to_wipe(dev, allow_internal=False)
    assert safe is False
    assert "internal" in reason.lower()


@patch("wipe.device._boot_system_partition_disk_indices", return_value=set())
@patch("wipe.device._resolve_system_physical_drive_index", return_value=0)
def test_is_safe_to_wipe_allows_internal_when_system_known(mock_idx, mock_boot):
    dev = _make_device(
        is_removable=False, is_internal=True, is_system_drive=False,
        device_id=r"\\.\PhysicalDrive3",
    )
    safe, reason = is_safe_to_wipe(dev, allow_internal=True)
    assert safe is True
    assert reason == ""


@patch("wipe.device._boot_system_partition_disk_indices", return_value=None)
@patch("wipe.device._resolve_system_physical_drive_index", return_value=0)
def test_is_safe_to_wipe_failsafe_refuses_internal_when_boot_layout_unknown(
    mock_idx, mock_boot
):
    """Even when the Windows disk is known, an unreadable boot/EFI layout must
    block internal wipes (the boot partition could be on a separate disk)."""
    dev = _make_device(is_removable=False, is_internal=True, is_system_drive=False)
    safe, reason = is_safe_to_wipe(dev, allow_internal=True)
    assert safe is False
    assert "boot" in reason.lower()


@patch("wipe.device._boot_system_partition_disk_indices", return_value={0})
@patch("wipe.device._resolve_system_physical_drive_index", return_value=1)
def test_system_indices_union_includes_boot_disk(mock_win, mock_boot):
    """_system_physical_drive_indices unions the Windows disk and boot disks."""
    from wipe.device import _system_physical_drive_indices
    assert _system_physical_drive_indices() == {0, 1}


@patch("wipe.device.audit_log")
@patch("wipe.device._boot_system_partition_disk_indices", return_value={0})
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._get_wmi_connection")
def test_separate_boot_disk_is_flagged_system(
    mock_wmi_conn, mock_sysletter, mock_bl, mock_ap, mock_boot, mock_log
):
    """A disk that only carries the boot/EFI partition (Windows files on another
    disk) must still be flagged is_system_drive — wiping it would break booting."""
    mock_c = MagicMock()
    mock_wmi_conn.return_value = mock_c

    phys = MagicMock()
    phys.Index = 0
    phys.InterfaceType = "SATA"
    phys.MediaType = "Fixed hard disk media"
    phys.SerialNumber = "BOOT-DISK"
    phys.Model = "Boot SSD 256GB"
    phys.Size = str(256 * 1024**3)
    mock_c.Win32_DiskDrive.return_value = [phys]
    mock_c.Win32_LogicalDisk.return_value = []
    mock_c.Win32_LogicalDiskToPartition.return_value = []
    mock_c.Win32_DiskDriveToDiskPartition.return_value = []

    devices = list_devices()
    assert len(devices) == 1
    assert devices[0].is_system_drive is True
    assert devices[0].safe_to_wipe is False


@patch("wipe.device._resolve_system_physical_drive_index", return_value=None)
def test_is_safe_to_wipe_failsafe_refuses_internal_when_system_unknown(mock_idx):
    """Fail-safe: refuse an internal wipe when the Windows disk can't be identified."""
    dev = _make_device(is_removable=False, is_internal=True, is_system_drive=False)
    safe, reason = is_safe_to_wipe(dev, allow_internal=True)
    assert safe is False
    assert "system" in reason.lower()
