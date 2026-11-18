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
    assert _connection_type_from_interface("NVMe") == "NVMe"


def test_connection_scsi_is_not_guessed_as_nvme():
    # UAS USB enclosures report "SCSI" too; only MSFT_Disk.BusType knows.
    assert _connection_type_from_interface("SCSI") == "SCSI"


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


# ── System guard per physical disk (1.1.1) ───────────────────────────

def _fake_wmi(layout: dict) -> MagicMock:
    """Build a fake WMI graph from {disk_index: (interface, [letters])}."""
    c = MagicMock()
    disks, ldisks, l2p, d2p = [], [], [], []
    for idx, (iface, letters) in layout.items():
        d = MagicMock()
        d.Index = idx
        d.InterfaceType = iface
        d.MediaType = "Removable Media" if iface == "USB" else "Fixed hard disk media"
        d.SerialNumber = f"SN{idx}"
        d.Model = f"Disk{idx}"
        d.Size = str(64 * 1024**3)
        disks.append(d)
        for p, letter in enumerate(letters):
            part = f"Disk #{idx}, Partition #{p}"
            ld = MagicMock()
            ld.DeviceID = letter
            ld.DriveType = 2 if iface == "USB" else 3
            ld.FileSystem = "NTFS"
            ldisks.append(ld)
            a = MagicMock()
            a.Dependent.DeviceID = letter
            a.Antecedent.DeviceID = part
            l2p.append(a)
            b = MagicMock()
            b.Dependent.DeviceID = part
            b.Antecedent.Index = idx
            d2p.append(b)
    c.Win32_DiskDrive.return_value = disks
    c.Win32_LogicalDisk.return_value = ldisks
    c.Win32_LogicalDiskToPartition.return_value = l2p
    c.Win32_DiskDriveToDiskPartition.return_value = d2p
    return c


@pytest.fixture
def fake_env(monkeypatch):
    """Patch WMI + probes; returns a function(layout, sys_letter) -> {letter: DeviceInfo}."""
    import wipe.device as device_mod

    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod, "_check_bitlocker", lambda _l: False)
    monkeypatch.setattr(device_mod, "_check_active_processes", lambda _l: False)

    def run(layout: dict, sys_letter: str = "C:", storage_system: set | None = None) -> dict:
        c = _fake_wmi(layout)
        monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: c)
        monkeypatch.setattr(device_mod, "_system_drive_letter", lambda: sys_letter)
        monkeypatch.setattr(device_mod, "_storage_system_disks",
                            lambda: set(storage_system or ()))
        return {d.drive_letter: d for d in list_devices()}

    return run


def test_disk_with_the_active_boot_partition_is_blocked(fake_env):
    # Windows on Disk 0, but the firmware booted from an EFI partition on
    # internal Disk 1 (Windows storage stack reports IsSystem for Disk 1).
    devs = fake_env({0: ("SCSI", ["C:"]), 1: ("IDE", ["D:"])}, storage_system={1})
    assert devs["D:"].is_system_drive is True


def test_wmi_connection_initialises_com_for_worker_threads(monkeypatch):
    """The GUI runs prepare_target in a QThread; WMI needs COM there."""
    import wipe.device as device_mod
    calls: list[str] = []
    monkeypatch.setattr(device_mod.pythoncom, "CoInitialize", lambda: calls.append("com"))
    monkeypatch.setattr(device_mod.wmi, "WMI",
                        lambda *a, **k: calls.append("wmi") or MagicMock())
    device_mod._get_wmi_connection()
    assert calls == ["com", "wmi"]


def test_second_partition_on_system_disk_is_blocked(fake_env):
    devs = fake_env({0: ("SCSI", ["C:", "D:"]), 1: ("USB", ["E:"])})
    assert devs["C:"].is_system_drive is True
    assert devs["D:"].is_system_drive is True
    assert devs["E:"].is_system_drive is False


def test_unknown_system_disk_blocks_all_non_usb(fake_env):
    devs = fake_env({0: ("IDE", ["D:"]), 1: ("USB", ["E:"])}, sys_letter="Z:")
    assert devs["D:"].is_system_drive is True
    assert devs["E:"].is_system_drive is False


def test_internal_disk_off_the_system_disk_stays_wipeable(fake_env):
    devs = fake_env({0: ("SCSI", ["C:"]), 2: ("IDE", ["H:"])})
    assert devs["H:"].is_system_drive is False


def test_disk_letters_lists_all_letters_of_the_disk(fake_env):
    devs = fake_env({0: ("SCSI", ["C:"]), 1: ("USB", ["G:", "E:"])})
    assert devs["E:"].disk_letters == ["E:", "G:"]
    assert devs["G:"].disk_letters == ["E:", "G:"]
    assert devs["C:"].disk_letters == ["C:"]


# ── 2.0: storage-stack flags, MBR "active" alone never blocks ─────────
#
# Bootable USB installer sticks carry an MBR partition marked active
# (MSFT_Partition.IsActive). That flag says nothing about the running
# Windows, so it must not make a disk unwipeable. Only the disk holding
# the Windows folder and disks the storage stack reports as IsBoot /
# IsSystem (MSFT_Disk) are system disks.

def _storage_ns(msft_disks: dict, active_disks: set = frozenset()) -> MagicMock:
    """Fake root\\Microsoft\\Windows\\Storage: {disk: (IsBoot, IsSystem)}."""
    from types import SimpleNamespace

    ns = MagicMock()
    ns.MSFT_Disk.return_value = [
        SimpleNamespace(Number=n, IsBoot=boot, IsSystem=system)
        for n, (boot, system) in msft_disks.items()
    ]
    partitions = []
    for n, (boot, system) in msft_disks.items():
        if system:
            partitions.append(SimpleNamespace(
                DiskNumber=n, IsBoot=False, IsSystem=True, IsActive=False))
        if boot:
            partitions.append(SimpleNamespace(
                DiskNumber=n, IsBoot=True, IsSystem=False, IsActive=False))
    for n in active_disks:
        partitions.append(SimpleNamespace(
            DiskNumber=n, IsBoot=False, IsSystem=False, IsActive=True))
    ns.MSFT_Partition.return_value = partitions
    return ns


@pytest.fixture
def storage_env(monkeypatch):
    """Fake Win32 layout + fake storage namespace behind wmi.WMI.

    Returns run(layout, storage) -> {letter or device_id: DeviceInfo}.
    ``storage`` may be an Exception instance to make the query fail.
    """
    import wipe.device as device_mod

    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod, "_check_bitlocker", lambda _l: False)
    monkeypatch.setattr(device_mod, "_check_active_processes", lambda _l: False)
    monkeypatch.setattr(device_mod, "_system_drive_letter", lambda: "C:")
    monkeypatch.setattr(device_mod.pythoncom, "CoInitialize", lambda: None)

    def run(layout: dict, storage) -> dict:
        c = _fake_wmi(layout)

        def fake_wmi(*_a, namespace=None, **_k):
            if namespace is None:
                return c
            if isinstance(storage, Exception):
                raise storage
            return storage

        monkeypatch.setattr(device_mod.wmi, "WMI", fake_wmi)
        monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: c)
        return {d.drive_letter or d.device_id: d for d in list_devices()}

    return run


@pytest.mark.parametrize("stick_letters", [["F:"], []], ids=["letter", "no-letter"])
def test_usb_stick_with_active_partition_stays_wipeable(storage_env, stick_letters):
    # Robin's test stick: a Windows installer stick, MBR partition active.
    storage = _storage_ns({0: (True, True), 1: (False, False)}, active_disks={1})
    devs = storage_env({0: ("SCSI", ["C:"]), 1: ("USB", stick_letters)}, storage)
    stick = devs["F:" if stick_letters else r"\\.\PhysicalDrive1"]

    assert devs["C:"].is_system_drive is True
    assert stick.is_system_drive is False
    assert is_safe_to_wipe(stick) == (True, "")


def test_internal_disk_with_active_partition_not_blocked_by_flag_alone(storage_env):
    storage = _storage_ns({0: (True, True), 2: (False, False)}, active_disks={2})
    devs = storage_env({0: ("SCSI", ["C:"]), 2: ("IDE", ["H:"])}, storage)

    assert devs["H:"].is_system_drive is False
    # Still an internal disk: refused without the explicit opt-in ...
    assert is_safe_to_wipe(devs["H:"], allow_internal=False)[0] is False
    # ... and allowed with it.
    assert is_safe_to_wipe(devs["H:"], allow_internal=True) == (True, "")


def test_storage_boot_disk_without_windows_folder_is_blocked(storage_env):
    # Windows on Disk 0, firmware booted from the EFI partition on Disk 1
    # (no letter): the storage stack reports Disk 1 as IsSystem.
    storage = _storage_ns({0: (True, False), 1: (False, True)})
    devs = storage_env({0: ("SCSI", ["C:"]), 1: ("IDE", [])}, storage)
    assert devs[r"\\.\PhysicalDrive1"].is_system_drive is True


def test_unreadable_boot_layout_blocks_internal_disks_but_not_usb(storage_env):
    """Fail-safe: if the storage stack can't be read, a separate boot disk
    can't be ruled out, so every internal disk is refused."""
    devs = storage_env(
        {0: ("SCSI", ["C:"]), 1: ("USB", ["F:"]), 2: ("IDE", ["H:"])},
        RuntimeError("provider unavailable"),
    )
    assert devs["H:"].is_system_drive is True
    assert devs["F:"].is_system_drive is False


def test_letterless_disk_has_empty_disk_letters(storage_env):
    storage = _storage_ns({0: (True, True)})
    devs = storage_env({0: ("SCSI", ["C:"]), 3: ("USB", [])}, storage)
    blank = devs[r"\\.\PhysicalDrive3"]
    assert blank.drive_letter == ""
    assert blank.disk_letters == []
    assert devs["C:"].disk_letters == ["C:"]


def test_storage_wmi_connection_initialises_com(monkeypatch):
    """Every WMI connection, including the storage namespace, inits COM first."""
    import wipe.device as device_mod
    calls: list = []
    monkeypatch.setattr(device_mod.pythoncom, "CoInitialize", lambda: calls.append("com"))
    monkeypatch.setattr(device_mod.wmi, "WMI",
                        lambda *a, **k: calls.append(k.get("namespace")) or MagicMock())
    device_mod._get_storage_wmi_connection()
    assert calls[0] == "com"
    assert "storage" in calls[1].lower()


# ── prepare_target: identity re-check + dismount every volume (1.1.1) ─

USB_LAYOUT = {0: ("SCSI", ["C:"]), 1: ("USB", ["E:", "G:"])}


@pytest.fixture
def gate(monkeypatch):
    """Patch WMI/system letter/dismount for prepare_target; returns the dismount mock."""
    import wipe.device as device_mod

    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod, "_system_drive_letter", lambda: "C:")
    monkeypatch.setattr(device_mod, "_storage_system_disks", lambda: set())
    c = _fake_wmi(USB_LAYOUT)
    monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: c)
    dismount = MagicMock()
    monkeypatch.setattr(device_mod, "dismount_volume", dismount)

    def use_layout(layout: dict) -> None:
        new_c = _fake_wmi(layout)
        monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: new_c)

    dismount.use_layout = use_layout
    return dismount


def test_prepare_target_dismounts_letters_present_now(gate):
    """A letter that appeared after listing (G:) is dismounted too."""
    from wipe.device import prepare_target
    prepare_target(_usb_target(disk_letters=["E:"]))
    assert [c.args[0] for c in gate.call_args_list] == ["E:", "G:"]


def test_prepare_target_skips_letter_now_on_another_disk(gate):
    """A stale listed letter (H:, now on Disk 2) must not be force-dismounted."""
    from wipe.device import prepare_target
    gate.use_layout({0: ("SCSI", ["C:"]), 1: ("USB", ["E:"]), 2: ("USB", ["H:"])})
    prepare_target(_usb_target(disk_letters=["E:", "H:"]))
    assert [c.args[0] for c in gate.call_args_list] == ["E:"]


def _usb_target(**overrides) -> DeviceInfo:
    base = dict(
        device_id=r"\\.\PhysicalDrive1", model="Disk1", serial_number="SN1",
        capacity_bytes=64 * 1024**3, disk_letters=["E:", "G:"],
    )
    base.update(overrides)
    return _make_device(**base)


def test_prepare_target_dismounts_every_letter(gate):
    from wipe.device import prepare_target
    prepare_target(_usb_target())
    assert [c.args[0] for c in gate.call_args_list] == ["E:", "G:"]


def test_prepare_target_rejects_changed_serial(gate):
    from wipe.device import prepare_target, UnsafeTargetError
    with pytest.raises(UnsafeTargetError):
        prepare_target(_usb_target(serial_number="OTHER"))
    gate.assert_not_called()


def test_prepare_target_rejects_changed_size(gate):
    from wipe.device import prepare_target, UnsafeTargetError
    with pytest.raises(UnsafeTargetError):
        prepare_target(_usb_target(capacity_bytes=32 * 1024**3))
    gate.assert_not_called()


def test_prepare_target_rejects_system_disk(gate):
    from wipe.device import prepare_target, UnsafeTargetError
    with pytest.raises(UnsafeTargetError):
        prepare_target(_usb_target(
            device_id=r"\\.\PhysicalDrive0", model="Disk0", serial_number="SN0",
            disk_letters=["C:"],
        ))
    gate.assert_not_called()


def test_prepare_target_accepts_letterless_disk_with_nothing_to_dismount(gate):
    """A freshly wiped stick has no letter: nothing to dismount, still allowed."""
    from wipe.device import prepare_target
    gate.use_layout({0: ("SCSI", ["C:"]), 3: ("USB", [])})
    prepare_target(_usb_target(
        drive_letter="", device_id=r"\\.\PhysicalDrive3", model="Disk3",
        serial_number="SN3", disk_letters=[],
    ))
    gate.assert_not_called()


def test_prepare_target_refuses_internal_disk_when_boot_layout_unknown(gate, monkeypatch):
    import wipe.device as device_mod
    from wipe.device import prepare_target, UnsafeTargetError
    gate.use_layout({0: ("SCSI", ["C:"]), 2: ("IDE", ["H:"])})
    monkeypatch.setattr(device_mod, "_storage_system_disks", lambda: None)
    with pytest.raises(UnsafeTargetError):
        prepare_target(_usb_target(
            device_id=r"\\.\PhysicalDrive2", model="Disk2", serial_number="SN2",
            connection_type="SATA", is_removable=False, is_internal=True,
            disk_letters=["H:"],
        ))
    gate.assert_not_called()


def test_prepare_target_fails_closed_on_wmi_error(gate, monkeypatch):
    import wipe.device as device_mod
    from wipe.device import prepare_target, UnsafeTargetError

    def boom():
        raise Exception("WMI down")

    monkeypatch.setattr(device_mod, "_get_wmi_connection", boom)
    with pytest.raises(UnsafeTargetError):
        prepare_target(_usb_target())
    gate.assert_not_called()


def test_prepare_target_fails_when_a_volume_stays_mounted(gate):
    from wipe.device import prepare_target, UnsafeTargetError
    gate.side_effect = lambda letter: (_ for _ in ()).throw(OSError("busy")) if letter == "G:" else None
    with pytest.raises(UnsafeTargetError, match="G:"):
        prepare_target(_usb_target())


def _fake_volume_ioctls(monkeypatch, lock_ok: bool, dismount_ok: bool):
    import wipe.device as device_mod
    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod.kernel32, "CreateFileW", lambda *a: 42)
    monkeypatch.setattr(device_mod.kernel32, "CloseHandle", lambda h: True)

    def ioctl(handle, code, *rest):
        if code == device_mod.FSCTL_LOCK_VOLUME:
            return lock_ok
        if code == device_mod.FSCTL_DISMOUNT_VOLUME:
            return dismount_ok
        return True

    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl", ioctl)


def test_dismount_continues_when_lock_fails(monkeypatch):
    from wipe.device import dismount_volume
    _fake_volume_ioctls(monkeypatch, lock_ok=False, dismount_ok=True)
    dismount_volume("E:")  # must not raise


def test_dismount_raises_when_dismount_fails(monkeypatch):
    from wipe.device import dismount_volume
    _fake_volume_ioctls(monkeypatch, lock_ok=True, dismount_ok=False)
    with pytest.raises(OSError):
        dismount_volume("E:")


def test_unique_disks_keeps_first_per_device_id():
    from wipe.device import unique_disks
    e = _make_device(drive_letter="E:", device_id=r"\\.\PhysicalDrive1")
    g = _make_device(drive_letter="G:", device_id=r"\\.\PhysicalDrive1")
    h = _make_device(drive_letter="H:", device_id=r"\\.\PhysicalDrive2")
    assert unique_disks([e, g, h]) == [e, h]


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
    monkeypatch.setattr(device_mod, "_current_system_disks", lambda: {0})

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

    monkeypatch.setattr(device_mod, "_current_system_disks", lambda: {0})

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
@patch("wipe.device._system_disk_indices", return_value=None)
@patch("wipe.device._get_wmi_connection")
def test_physical_drive_index_resolution_failure_falls_back_to_letter_check(
    mock_wmi_conn, mock_resolve, mock_sysletter, mock_bl, mock_ap, mock_log
):
    """When the system disk set can't be determined the system drive must
    still be flagged, so resolution failures cannot silently remove all
    protection.
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

    monkeypatch.setattr(device_mod, "_current_system_disks", lambda: {sys_index})
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

    monkeypatch.setattr(device_mod, "_current_system_disks", lambda: {0})
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


# ── German Windows: tool output with umlauts (1.1.1) ──────────────────

def _run_emitting(payload: bytes):
    """subprocess.run stand-in: runs a real child that writes raw bytes."""
    import subprocess as sp

    real_run = sp.run

    def fake_run(_cmd, **kwargs):
        code = f"import sys; sys.stdout.buffer.write({payload!r})"
        return real_run([sys.executable, "-c", code], **kwargs)

    return fake_run


def test_bitlocker_probe_survives_non_ascii_output(monkeypatch):
    import wipe.device as device_mod
    # 0x81 is "ü" in the German OEM code page and undefined in cp1252.
    monkeypatch.setattr(device_mod.subprocess, "run",
                        _run_emitting(b"Verschl\x81sselung\r\nProtection On\r\n"))
    assert device_mod._check_bitlocker("F:") is True


def test_handle_probe_survives_non_ascii_output(monkeypatch):
    import wipe.device as device_mod
    monkeypatch.setattr(device_mod.subprocess, "run",
                        _run_emitting(b"explorer.exe pid: 42 \x81 F:\\Users\r\n"))
    assert device_mod._check_active_processes("F:") is True


# ── Letterless / internal disk enumeration (v1.2) ─────────────────────

@patch("wipe.device.audit_log")
@patch("wipe.device._check_active_processes", return_value=False)
@patch("wipe.device._check_bitlocker", return_value=False)
@patch("wipe.device._system_drive_letter", return_value="C:")
# Windows lives on Disk 0 (not part of this fake layout); an unknown layout
# would block every internal disk (fail-safe).
@patch("wipe.device._system_disk_indices", return_value={0})
@patch("wipe.device._get_wmi_connection")
def test_letterless_internal_disk_is_enumerated(
    mock_wmi_conn, mock_sys, mock_sysletter, mock_bl, mock_ap, mock_log
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


@patch("wipe.device._current_system_disks", return_value={0})
def test_is_safe_to_wipe_allows_internal_when_system_known(mock_sys):
    dev = _make_device(
        is_removable=False, is_internal=True, is_system_drive=False,
        device_id=r"\\.\PhysicalDrive3",
    )
    safe, reason = is_safe_to_wipe(dev, allow_internal=True)
    assert safe is True
    assert reason == ""


@patch("wipe.device._current_system_disks", return_value=None)
def test_is_safe_to_wipe_failsafe_refuses_internal_when_boot_layout_unknown(mock_sys):
    """Even when the Windows disk is known, an unreadable boot/EFI layout must
    block internal wipes (the boot partition could be on a separate disk)."""
    dev = _make_device(is_removable=False, is_internal=True, is_system_drive=False)
    safe, reason = is_safe_to_wipe(dev, allow_internal=True)
    assert safe is False
    assert "boot" in reason.lower()


@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._storage_system_disks", return_value={0})
def test_system_indices_union_includes_boot_disk(mock_storage, mock_letter):
    """_system_disk_indices unions the Windows disk and storage boot disks."""
    from wipe.device import _system_disk_indices
    c = _fake_wmi({0: ("IDE", []), 1: ("SCSI", ["C:"])})
    assert _system_disk_indices(c) == {0, 1}


@patch("wipe.device._system_drive_letter", return_value="C:")
@patch("wipe.device._storage_system_disks", return_value=None)
def test_system_indices_unknown_when_storage_query_fails(mock_storage, mock_letter):
    """Fail closed: an unreadable boot layout makes the whole set unknown."""
    from wipe.device import _system_disk_indices
    c = _fake_wmi({1: ("SCSI", ["C:"])})
    assert _system_disk_indices(c) is None


def test_separate_boot_disk_is_flagged_system(fake_env):
    """A disk that only carries the boot/EFI partition (Windows files on another
    disk) must still be flagged is_system_drive — wiping it would break booting."""
    devs = fake_env({0: ("IDE", []), 1: ("SCSI", ["C:"])}, storage_system={0})
    boot_disk = devs[""]  # letterless
    assert boot_disk.device_id == r"\\.\PhysicalDrive0"
    assert boot_disk.is_system_drive is True
    assert boot_disk.safe_to_wipe is False


@patch("wipe.device._current_system_disks", return_value=None)
def test_is_safe_to_wipe_failsafe_refuses_internal_when_system_unknown(mock_sys):
    """Fail-safe: refuse an internal wipe when the Windows disk can't be identified."""
    dev = _make_device(is_removable=False, is_internal=True, is_system_drive=False)
    safe, reason = is_safe_to_wipe(dev, allow_internal=True)
    assert safe is False
    assert "system" in reason.lower()
