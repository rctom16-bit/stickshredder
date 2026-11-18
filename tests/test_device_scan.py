"""The device scan reads the partition layout once (2.0 polish).

The list used to walk both WMI association classes again for every drive
letter, following every reference (one WMI round trip each), and asked
BitLocker per drive in a way that took ~5 s without administrator rights.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import wipe.device as device_mod
from wipe.device import list_devices

from tests.test_device import _fake_wmi


@pytest.fixture
def scan(monkeypatch):
    """list_devices() on a fake layout; returns (devices by letter, fake WMI)."""
    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod, "_check_bitlocker", lambda _l: False)
    monkeypatch.setattr(device_mod, "_check_active_processes", lambda _l: False)
    monkeypatch.setattr(device_mod, "_system_drive_letter", lambda: "C:")
    monkeypatch.setattr(device_mod, "_storage_system_disks", lambda: set())

    def run(layout: dict, c=None):
        c = c or _fake_wmi(layout)
        monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: c)
        return {d.drive_letter or d.device_id: d for d in list_devices()}, c

    return run


LAYOUT = {
    0: ("SCSI", ["C:"]),
    1: ("USB", ["D:", "E:"]),
    2: ("USB", ["F:"]),
    3: ("USB", ["G:", "H:"]),
}


def test_scan_reads_each_association_class_once(scan):
    devices, c = scan(LAYOUT)
    assert set(devices) == {"C:", "D:", "E:", "F:", "G:", "H:"}
    assert c.Win32_DiskDriveToDiskPartition.call_count == 1
    assert c.Win32_LogicalDiskToPartition.call_count == 1


def test_scan_counts_partitions_per_disk(scan):
    devices, _ = scan(LAYOUT)
    assert devices["C:"].partition_count == 1
    assert devices["D:"].partition_count == 2
    assert devices["E:"].partition_count == 2
    assert devices["G:"].partition_count == 2


def test_unreadable_layout_lists_disks_letterless_and_blocks_internal(scan):
    c = _fake_wmi({0: ("IDE", ["C:"]), 1: ("USB", ["E:"])})
    c.Win32_DiskDriveToDiskPartition.side_effect = RuntimeError("WMI broke")
    devices, _ = scan({}, c)
    assert set(devices) == {r"\\.\PhysicalDrive0", r"\\.\PhysicalDrive1"}
    # System disk unknown: every non-USB disk is blocked (fail closed).
    assert devices[r"\\.\PhysicalDrive0"].is_system_drive is True
    assert devices[r"\\.\PhysicalDrive1"].is_system_drive is False


# ── Keys read from the object paths, references not followed ──────────

HOST = r"\\PC\root\cimv2"


class _Assoc:
    """Association instance whose references must not be followed."""

    def __init__(self, antecedent: str, dependent: str) -> None:
        self._paths = {"Antecedent": antecedent, "Dependent": dependent}

    def wmi_property(self, role):
        return SimpleNamespace(value=self._paths[role])

    def __getattr__(self, name):
        raise AssertionError(f"followed the {name} reference (one WMI round trip)")


def _disk_path(n: int) -> str:
    return HOST + r':Win32_DiskDrive.DeviceID="\\\\.\\PHYSICALDRIVE' + f'{n}"'


def _part_path(disk: int, part: int) -> str:
    return HOST + f':Win32_DiskPartition.DeviceID="Disk #{disk}, Partition #{part}"'


def _letter_path(letter: str) -> str:
    return HOST + f':Win32_LogicalDisk.DeviceID="{letter}"'


def test_layout_reads_keys_from_the_object_paths():
    disks = [SimpleNamespace(DeviceID=f"\\\\.\\PHYSICALDRIVE{n}", Index=n) for n in (0, 1)]
    c = MagicMock()
    c.Win32_DiskDriveToDiskPartition.return_value = [
        _Assoc(_disk_path(0), _part_path(0, 0)),
        _Assoc(_disk_path(0), _part_path(0, 1)),
        _Assoc(_disk_path(1), _part_path(1, 0)),
    ]
    c.Win32_LogicalDiskToPartition.return_value = [
        _Assoc(_part_path(0, 1), _letter_path("C:")),
        _Assoc(_part_path(1, 0), _letter_path("e:")),
    ]
    layout = device_mod._disk_layout(c, disks)
    assert layout.letter_disk == {"C:": 0, "E:": 1}
    assert layout.partitions == {0: 2, 1: 1}


def test_ref_device_id_unescapes_the_path():
    assoc = _Assoc(_disk_path(12), _letter_path("E:"))
    assert device_mod._ref_device_id(assoc, "Antecedent") == r"\\.\PHYSICALDRIVE12"
    assert device_mod._ref_device_id(assoc, "Dependent") == "E:"


@pytest.mark.parametrize("value", [None, 42, "no key here"])
def test_ref_device_id_without_a_readable_path_is_none(value):
    assoc = SimpleNamespace(wmi_property=lambda _role: SimpleNamespace(value=value))
    assert device_mod._ref_device_id(assoc, "Antecedent") is None


def test_scan_keeps_the_bitlocker_cache_only_for_the_scan(scan, monkeypatch):
    seen = []

    def probe(letter):
        seen.append(getattr(device_mod._scan, "cache", None) is not None)
        return False

    monkeypatch.setattr(device_mod, "_check_bitlocker", probe)
    scan({1: ("USB", ["E:"]), 2: ("USB", ["F:"])})
    assert seen == [True, True]
    assert getattr(device_mod._scan, "cache", None) is None
