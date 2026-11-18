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


def _fake_volume_ioctls(monkeypatch, lock_results, dismount_ok: bool = True):
    """Fake volume IOCTLs. ``lock_results``: bool or list of bools (one per lock call).

    Returns a dict recording the call sequence and closed handles.
    """
    import wipe.device as device_mod
    record = {"calls": [], "closed": []}
    locks = list(lock_results) if isinstance(lock_results, (list, tuple)) else None
    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod.kernel32, "CreateFileW", lambda *a: 42)
    monkeypatch.setattr(device_mod.kernel32, "CloseHandle",
                        lambda h: record["closed"].append(h) or True)

    def ioctl(handle, code, *rest):
        if code == device_mod.FSCTL_LOCK_VOLUME:
            record["calls"].append("lock")
            return locks.pop(0) if locks is not None else lock_results
        if code == device_mod.FSCTL_DISMOUNT_VOLUME:
            record["calls"].append("dismount")
            return dismount_ok
        return True

    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl", ioctl)
    return record


def test_dismount_keeps_the_volume_locked_and_open(monkeypatch):
    """1.1.2: the lock must last for the whole wipe, or Windows remounts the
    volume mid-wipe and every further write fails with error 5."""
    from wipe.device import dismount_volume
    rec = _fake_volume_ioctls(monkeypatch, lock_results=True)
    assert dismount_volume("E:") == 42
    assert rec["calls"] == ["lock", "dismount"]
    assert rec["closed"] == []


def test_dismount_relocks_after_forced_dismount(monkeypatch):
    """Lock blocked by an open handle (e.g. Explorer): force the dismount,
    which invalidates that handle, then take the lock."""
    from wipe.device import dismount_volume
    rec = _fake_volume_ioctls(monkeypatch, lock_results=[False, True])
    assert dismount_volume("E:") == 42
    assert rec["calls"] == ["lock", "dismount", "lock"]
    assert rec["closed"] == []


def test_dismount_fails_when_the_volume_cannot_be_locked(monkeypatch):
    from wipe.device import dismount_volume
    rec = _fake_volume_ioctls(monkeypatch, lock_results=[False, False])
    with pytest.raises(OSError):
        dismount_volume("E:")
    assert rec["closed"] == [42]


def test_dismount_raises_when_dismount_fails(monkeypatch):
    from wipe.device import dismount_volume
    rec = _fake_volume_ioctls(monkeypatch, lock_results=True, dismount_ok=False)
    with pytest.raises(OSError):
        dismount_volume("E:")
    assert rec["closed"] == [42]


def test_prepare_target_returns_the_volume_locks(gate):
    from wipe.device import prepare_target
    gate.side_effect = lambda letter: {"E:": 101, "G:": 102}[letter]
    assert prepare_target(_usb_target()) == [101, 102]


def test_prepare_target_releases_locks_when_a_later_volume_fails(gate, monkeypatch):
    import wipe.device as device_mod
    from wipe.device import prepare_target, UnsafeTargetError
    released = []
    monkeypatch.setattr(device_mod, "release_volumes", lambda hs: released.extend(hs))

    def dismount(letter):
        if letter == "G:":
            raise OSError("busy")
        return 101

    gate.side_effect = dismount
    with pytest.raises(UnsafeTargetError, match="G:"):
        prepare_target(_usb_target())
    assert released == [101]


def test_release_volumes_closes_every_handle(monkeypatch):
    import wipe.device as device_mod
    closed = []
    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod.kernel32, "CloseHandle", lambda h: closed.append(h) or True)
    device_mod.release_volumes([101, 102])
    assert closed == [101, 102]


def test_unique_disks_keeps_first_per_device_id():
    from wipe.device import unique_disks
    e = _make_device(drive_letter="E:", device_id=r"\\.\PhysicalDrive1")
    g = _make_device(drive_letter="G:", device_id=r"\\.\PhysicalDrive1")
    h = _make_device(drive_letter="H:", device_id=r"\\.\PhysicalDrive2")
    assert unique_disks([e, g, h]) == [e, h]


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


def test_release_volumes_survives_a_failing_log(monkeypatch):
    """Review 1.1.2: a failing audit log must not stop the release."""
    import wipe.device as device_mod
    closed = []

    def broken_log(*_a, **_k):
        raise OSError("log locked")

    monkeypatch.setattr(device_mod, "audit_log", broken_log)
    monkeypatch.setattr(device_mod.kernel32, "CloseHandle", lambda h: closed.append(h) or True)
    device_mod.release_volumes([101, 102])  # must not raise
    assert closed == [101, 102]


def test_prepare_target_releases_locks_on_any_error(gate, monkeypatch):
    """Review 1.1.2: not only OSError — any failure releases taken locks."""
    import wipe.device as device_mod
    from wipe.device import prepare_target
    released = []
    monkeypatch.setattr(device_mod, "release_volumes", lambda hs: released.extend(hs))

    def dismount(letter):
        if letter == "G:":
            raise ValueError("ctypes argument error")
        return 101

    gate.side_effect = dismount
    with pytest.raises(ValueError):
        prepare_target(_usb_target())
    assert released == [101]
