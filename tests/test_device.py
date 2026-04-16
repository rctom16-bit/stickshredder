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

from wipe.device import DeviceInfo, list_devices, _system_drive_letter, _connection_type_from_interface


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

def test_system_drive_letter_from_env(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"D:\Windows")
    assert _system_drive_letter() == "D:"


def test_system_drive_letter_default(monkeypatch):
    monkeypatch.delenv("SystemRoot", raising=False)
    # Falls back to C:\Windows -> "C:"
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
@patch("wipe.device._get_wmi_connection")
def test_list_devices_returns_usb_device(mock_wmi_conn, mock_bl, mock_ap, mock_log, monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

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
