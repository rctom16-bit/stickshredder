"""Connection type from MSFT_Disk.BusType (storage namespace).

Real case (18.11.2026): a Realtek RTL9210B USB enclosure (UAS) reports
InterfaceType "SCSI" and MediaType "External hard disk media" in
Win32_DiskDrive and was shown as internal NVMe. The storage stack knows
better: MSFT_Disk.BusType = 7 (USB).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import wipe.device as device_mod
from wipe.device import list_devices


def _win32(layout: dict) -> MagicMock:
    """Fake root\\cimv2 from {index: (interface, media_type, [letters])}."""
    c = MagicMock()
    disks, ldisks, l2p, d2p = [], [], [], []
    for idx, (iface, media, letters) in layout.items():
        disks.append(SimpleNamespace(
            Index=idx, InterfaceType=iface, MediaType=media,
            SerialNumber=f"SN{idx}", Model=f"Disk{idx}", Size=str(64 * 1024**3),
        ))
        for p, letter in enumerate(letters):
            part = f"Disk #{idx}, Partition #{p}"
            ldisks.append(SimpleNamespace(DeviceID=letter, DriveType=3, FileSystem="NTFS"))
            l2p.append(SimpleNamespace(
                Dependent=SimpleNamespace(DeviceID=letter),
                Antecedent=SimpleNamespace(DeviceID=part)))
            d2p.append(SimpleNamespace(
                Dependent=SimpleNamespace(DeviceID=part),
                Antecedent=SimpleNamespace(Index=idx)))
    c.Win32_DiskDrive.return_value = disks
    c.Win32_LogicalDisk.return_value = ldisks
    c.Win32_LogicalDiskToPartition.return_value = l2p
    c.Win32_DiskDriveToDiskPartition.return_value = d2p
    return c


class _Storage:
    """Fake root\\Microsoft\\Windows\\Storage from {number: bus_type}."""

    def __init__(self, buses: dict, system: set = frozenset({0})):
        self._disks = [
            SimpleNamespace(Number=n, BusType=b, IsBoot=n in system, IsSystem=n in system)
            for n, b in buses.items()
        ]

    def MSFT_Disk(self):  # noqa: N802 — WMI class name
        return list(self._disks)


@pytest.fixture
def env(monkeypatch):
    """run(layout, storage) -> {letter or device_id: DeviceInfo}.

    ``storage`` is a _Storage, or an Exception the storage query raises.
    Windows lives on C: of disk 0.
    """
    com_calls: list[str] = []
    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod, "_check_bitlocker", lambda _l: False)
    monkeypatch.setattr(device_mod, "_check_active_processes", lambda _l: False)
    monkeypatch.setattr(device_mod, "_system_drive_letter", lambda: "C:")
    monkeypatch.setattr(device_mod.pythoncom, "CoInitialize",
                        lambda: com_calls.append("com"))

    def run(layout: dict, storage) -> dict:
        c = _win32(layout)

        def fake_wmi(*_a, namespace=None, **_k):
            if namespace is None:
                return c
            if isinstance(storage, Exception):
                raise storage
            return storage

        monkeypatch.setattr(device_mod.wmi, "WMI", fake_wmi)
        monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: c)
        return {d.drive_letter or d.device_id: d for d in list_devices()}

    run.com_calls = com_calls
    return run


_SYSTEM = (0, ("SCSI", "Fixed hard disk media", ["C:"]))
EXTERNAL = "External hard disk media"


# ── the real case ──────────────────────────────────────────────────────

def test_uas_enclosure_reporting_scsi_is_usb_and_external(env):
    devs = env({0: _SYSTEM[1], 2: ("SCSI", EXTERNAL, ["F:"])},
               _Storage({0: 17, 2: 7}))
    dev = devs["F:"]
    assert dev.connection_type == "USB"
    assert dev.is_removable is True
    assert dev.is_internal is False
    assert dev.is_system_drive is False


def test_letterless_uas_enclosure_is_usb_and_external(env):
    devs = env({0: _SYSTEM[1], 2: ("SCSI", EXTERNAL, [])},
               _Storage({0: 17, 2: 7}))
    dev = devs[r"\\.\PhysicalDrive2"]
    assert dev.connection_type == "USB"
    assert dev.is_removable is True
    assert dev.is_internal is False


def test_system_nvme_reporting_scsi_stays_internal_nvme(env):
    devs = env({0: _SYSTEM[1]}, _Storage({0: 17}))
    assert devs["C:"].connection_type == "NVMe"
    assert devs["C:"].is_internal is True
    assert devs["C:"].is_system_drive is True


def test_storage_query_initialises_com(env):
    env({0: _SYSTEM[1]}, _Storage({0: 17}))
    assert env.com_calls  # the storage helper runs CoInitialize first


# ── the BusType table ──────────────────────────────────────────────────

@pytest.mark.parametrize("bus, expected", [
    (7, "USB"), (17, "NVMe"), (11, "SATA"), (3, "ATA"), (12, "SD"),
    (13, "MMC"), (8, "RAID"), (1, "SCSI"), (10, "SAS"),
])
def test_bus_type_names(env, bus, expected):
    devs = env({0: _SYSTEM[1], 1: ("SCSI", "Fixed hard disk media", ["D:"])},
               _Storage({0: 17, 1: bus}))
    assert devs["D:"].connection_type == expected


def test_internal_sata_disk_stays_internal(env):
    devs = env({0: _SYSTEM[1], 1: ("IDE", "Fixed hard disk media", ["D:"])},
               _Storage({0: 17, 1: 11}))
    assert devs["D:"].connection_type == "SATA"
    assert devs["D:"].is_internal is True
    assert devs["D:"].is_removable is False


@pytest.mark.parametrize("bus", [12, 13])
def test_sd_and_mmc_cards_are_removable(env, bus):
    devs = env({0: _SYSTEM[1], 1: ("SCSI", "Fixed hard disk media", ["D:"])},
               _Storage({0: 17, 1: bus}))
    assert devs["D:"].is_removable is True
    assert devs["D:"].is_internal is False


# ── fallback to Win32_DiskDrive.InterfaceType ──────────────────────────

def test_storage_query_failure_falls_back_to_interface_type(env):
    devs = env({0: _SYSTEM[1], 1: ("USB", "Removable Media", ["E:"]),
                2: ("IDE", "Fixed hard disk media", ["D:"])},
               RuntimeError("storage stack exploded"))
    assert devs["E:"].connection_type == "USB"
    assert devs["D:"].connection_type == "SATA"


def test_fallback_never_calls_scsi_nvme(env):
    devs = env({0: _SYSTEM[1]}, RuntimeError("storage stack exploded"))
    assert devs["C:"].connection_type == "SCSI"


def test_disk_missing_from_storage_falls_back_to_interface_type(env):
    devs = env({0: _SYSTEM[1], 1: ("USB", "Removable Media", ["E:"])},
               _Storage({0: 17}))
    assert devs["E:"].connection_type == "USB"


def test_unknown_bus_value_falls_back_to_interface_type(env):
    devs = env({0: _SYSTEM[1], 1: ("IDE", "Fixed hard disk media", ["D:"])},
               _Storage({0: 17, 1: 0}))
    assert devs["D:"].connection_type == "SATA"


def test_external_media_type_alone_counts_as_external(env):
    # Storage query failed, InterfaceType says SCSI, but Windows calls the
    # medium "External hard disk media": not an internal disk.
    devs = env({0: _SYSTEM[1], 2: ("SCSI", EXTERNAL, ["F:"])},
               RuntimeError("storage stack exploded"))
    assert devs["F:"].is_removable is True
    assert devs["F:"].is_internal is False
