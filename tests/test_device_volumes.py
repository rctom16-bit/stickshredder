"""Volumes without a drive letter on the target disk (2.0 polish).

Win32_LogicalDisk only lists volumes with a letter. A partition without one,
or one mounted in a folder, stayed mounted during the wipe. prepare_target()
now finds every volume of the disk through the volume manager
(FindFirstVolumeW + IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS) and dismounts and
locks those too. All Windows calls here are fakes.
"""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import wipe.device as device_mod
from wipe.device import UnsafeTargetError, prepare_target

from tests.test_device import _fake_wmi, _make_device

VOL_E = "\\\\?\\Volume{e0000000-0000-0000-0000-000000000001}\\"
VOL_G = "\\\\?\\Volume{90000000-0000-0000-0000-000000000002}\\"
VOL_HIDDEN = "\\\\?\\Volume{a0000000-0000-0000-0000-000000000003}\\"
VOL_FOLDER = "\\\\?\\Volume{b0000000-0000-0000-0000-000000000004}\\"
VOL_C = "\\\\?\\Volume{c0000000-0000-0000-0000-000000000005}\\"
VOL_CD = "\\\\?\\Volume{d0000000-0000-0000-0000-000000000006}\\"
VOL_UEFI = "\\\\?\\Volume{f0000000-0000-0000-0000-000000000007}\\"
VOL_SPAN = "\\\\?\\Volume{50000000-0000-0000-0000-000000000008}\\"


@pytest.fixture
def volumes(monkeypatch):
    """Disk 0 = Windows (C:), disk 1 = target (E:, G:, a hidden and a
    folder-mounted volume), a CD drive without extents."""
    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod, "_system_drive_letter", lambda: "C:")
    monkeypatch.setattr(device_mod, "_storage_system_disks", lambda: set())
    c = _fake_wmi({0: ("SCSI", ["C:"]), 1: ("USB", ["E:", "G:"])})
    monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: c)

    state = SimpleNamespace(
        names=[VOL_C, VOL_E, VOL_HIDDEN, VOL_G, VOL_FOLDER, VOL_CD],
        disks={VOL_C: {0}, VOL_E: {1}, VOL_G: {1}, VOL_HIDDEN: {1}, VOL_FOLDER: {1},
               VOL_CD: None},
        mounts={"C:\\": VOL_C, "E:\\": VOL_E, "G:\\": VOL_G},
        dismounted=[], released=[], fail=set(), wmi=c,
    )

    def names():
        if isinstance(state.names, Exception):
            raise state.names
        return list(state.names)

    def dismount(volume):
        if volume in state.fail:
            err = OSError(f"FSCTL_DISMOUNT_VOLUME failed for {volume} (error 32)")
            err.winerror = 32
            raise err
        state.dismounted.append(volume)
        return 100 + len(state.dismounted)

    monkeypatch.setattr(device_mod, "_volume_names", names)
    monkeypatch.setattr(device_mod, "_volume_disks", lambda v: state.disks[v])
    monkeypatch.setattr(device_mod, "_volume_at", lambda m: state.mounts.get(m))
    monkeypatch.setattr(device_mod, "dismount_volume", dismount)
    monkeypatch.setattr(device_mod, "release_volumes", state.released.extend)
    return state


def _target(**overrides):
    base = dict(device_id=r"\\.\PhysicalDrive1", model="Disk1", serial_number="SN1",
                capacity_bytes=64 * 1024**3, disk_letters=["E:", "G:"])
    base.update(overrides)
    return _make_device(**base)


def test_volumes_without_a_letter_are_dismounted_and_kept_locked(volumes):
    locks = prepare_target(_target())
    # Letters first (as before), then the hidden and the folder-mounted volume.
    assert volumes.dismounted == ["E:", "G:", VOL_HIDDEN, VOL_FOLDER]
    assert locks == [101, 102, 103, 104]
    assert volumes.released == []


def test_volume_of_a_letter_is_not_locked_twice(volumes):
    volumes.names = [VOL_E, VOL_G]
    prepare_target(_target())
    assert volumes.dismounted == ["E:", "G:"]


def test_volume_matching_ignores_case(volumes):
    volumes.mounts["E:\\"] = VOL_E.upper()
    volumes.names = [VOL_E, VOL_G]
    prepare_target(_target())
    assert volumes.dismounted == ["E:", "G:"]


def test_letterless_disk_with_only_hidden_volumes(volumes, monkeypatch):
    c = _fake_wmi({0: ("SCSI", ["C:"]), 1: ("USB", [])})
    monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: c)
    volumes.names = [VOL_C, VOL_HIDDEN]
    prepare_target(_make_device(drive_letter="", device_id=r"\\.\PhysicalDrive1",
                                model="Disk1", serial_number="SN1",
                                capacity_bytes=64 * 1024**3, disk_letters=[]))
    assert volumes.dismounted == [VOL_HIDDEN]


def test_refuses_when_the_volume_list_cannot_be_read(volumes):
    volumes.names = OSError("FindFirstVolumeW failed (error 5)")
    with pytest.raises(UnsafeTargetError, match="Volumes"):
        prepare_target(_target())
    assert volumes.dismounted == []


def test_refuses_when_the_windows_volume_lies_on_the_disk(volumes):
    volumes.disks[VOL_C] = {0, 1}   # e.g. a volume spanning both disks
    with pytest.raises(UnsafeTargetError, match="Windows"):
        prepare_target(_target())
    assert volumes.dismounted == []


def test_failed_dismount_of_a_hidden_volume_refuses_and_releases(volumes):
    volumes.fail = {VOL_HIDDEN}
    with pytest.raises(UnsafeTargetError, match="ohne Laufwerksbuchstaben") as info:
        prepare_target(_target())
    assert VOL_HIDDEN in str(info.value)
    assert volumes.dismounted == ["E:", "G:"]
    assert volumes.released == [101, 102]


def test_unreadable_volume_is_skipped(volumes):
    volumes.names = [VOL_CD, VOL_E, VOL_G]
    prepare_target(_target())
    assert volumes.dismounted == ["E:", "G:"]


def test_system_disk_is_refused_before_the_volume_scan(volumes):
    volumes.names = OSError("must not be read")
    with pytest.raises(UnsafeTargetError, match="Systemplatte"):
        prepare_target(_make_device(is_system_drive=True))


# ── Review fixes: fail closed before anything is locked ────────────────

def test_rufus_gpt_stick_uefi_ntfs_volume_is_dismounted_and_locked(volumes, monkeypatch):
    """Real case: Rufus writes a GPT stick with the data partition (E:) and a
    letterless 1 MiB FAT partition "UEFI_NTFS" at the very end of the disk."""
    c = _fake_wmi({0: ("SCSI", ["C:"]), 1: ("USB", ["E:"])})
    monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: c)
    volumes.names = [VOL_C, VOL_E, VOL_UEFI]
    volumes.disks[VOL_UEFI] = {1}
    locks = prepare_target(_target(disk_letters=["E:"]))
    assert volumes.dismounted == ["E:", VOL_UEFI]
    assert locks == [101, 102]
    assert volumes.released == []


def test_refuses_a_volume_that_also_lies_on_another_disk(volumes):
    """A spanned volume with one extent on the target: dismounting it and
    wiping one of its disks would destroy the data on the other disk."""
    volumes.names = [VOL_C, VOL_E, VOL_G, VOL_SPAN]
    volumes.disks[VOL_SPAN] = {1, 3}
    with pytest.raises(UnsafeTargetError) as info:
        prepare_target(_target())
    msg = str(info.value)
    assert VOL_SPAN in msg
    assert "PhysicalDrive3" in msg
    assert volumes.dismounted == []
    assert volumes.released == []


def test_refuses_a_lettered_volume_that_also_lies_on_other_disks(volumes):
    volumes.disks[VOL_G] = {4, 1, 2}
    with pytest.raises(UnsafeTargetError) as info:
        prepare_target(_target())
    msg = str(info.value)
    assert "G:" in msg
    assert "PhysicalDrive2, PhysicalDrive4" in msg
    assert volumes.dismounted == []


def test_refuses_when_the_windows_volume_cannot_be_found(volumes):
    """The Windows-volume guard must not be skipped silently (fail closed)."""
    del volumes.mounts["C:\\"]
    with pytest.raises(UnsafeTargetError, match="Windows") as info:
        prepare_target(_target())
    assert "C:\\" in str(info.value)
    assert volumes.dismounted == []


def test_refuses_when_a_letter_cannot_be_matched_to_its_volume(volumes):
    """Unmatched, G:'s volume would be dismounted a second time by its name —
    while our own lock from dismounting G: holds it."""
    del volumes.mounts["G:\\"]
    with pytest.raises(UnsafeTargetError) as info:
        prepare_target(_target())
    msg = str(info.value)
    assert "G:" in msg
    assert "Volume" in msg
    assert volumes.dismounted == []


# ── Review fixes: the disk is checked again once its volumes are locked ─

def _after_lock(volumes, **changes):
    """From the first dismount on, WMI reports disk 1 with ``changes``."""
    before = list(volumes.wmi.Win32_DiskDrive.return_value)

    def changed(d):
        if int(d.Index) != 1:
            return d
        fields = dict(Index=1, SerialNumber=d.SerialNumber, Model=d.Model,
                      Size=d.Size, InterfaceType=d.InterfaceType,
                      MediaType=d.MediaType)
        fields.update(changes)
        return SimpleNamespace(**fields)

    after = [changed(d) for d in before]
    if changes.get("gone"):
        after = [d for d in before if int(d.Index) != 1]
    volumes.wmi.Win32_DiskDrive.side_effect = (
        lambda: after if volumes.dismounted else before
    )


def test_swap_while_locking_is_refused_and_every_lock_released(volumes):
    _after_lock(volumes, SerialNumber="OTHER")
    with pytest.raises(UnsafeTargetError, match="nicht mehr das ausgewählte"):
        prepare_target(_target())
    assert volumes.dismounted == ["E:", "G:", VOL_HIDDEN, VOL_FOLDER]
    assert volumes.released == [101, 102, 103, 104]


@pytest.mark.parametrize("changes", [
    {"Model": "Other stick"}, {"Size": str(32 * 1024**3)},
])
def test_model_or_size_change_while_locking_is_refused(volumes, changes):
    _after_lock(volumes, **changes)
    with pytest.raises(UnsafeTargetError):
        prepare_target(_target())
    assert volumes.released == [101, 102, 103, 104]


def test_disk_gone_after_locking_is_refused(volumes):
    _after_lock(volumes, gone=True)
    with pytest.raises(UnsafeTargetError, match="nicht mehr vorhanden"):
        prepare_target(_target())
    assert volumes.released == [101, 102, 103, 104]


def test_disk_reported_as_system_disk_after_locking_is_refused(volumes, monkeypatch):
    monkeypatch.setattr(device_mod, "_storage_system_disks",
                        lambda: {1} if volumes.dismounted else set())
    with pytest.raises(UnsafeTargetError, match="Systemplatte"):
        prepare_target(_target())
    assert volumes.released == [101, 102, 103, 104]


def test_wmi_failure_after_locking_refuses_and_releases(volumes):
    def disks():
        if volumes.dismounted:
            raise RuntimeError("RPC server unavailable")
        return volumes.wmi.Win32_DiskDrive.return_value
    volumes.wmi.Win32_DiskDrive.side_effect = disks
    with pytest.raises(UnsafeTargetError, match="could not re-check"):
        prepare_target(_target())
    assert volumes.released == [101, 102, 103, 104]


def test_all_zero_serial_of_a_usb_enclosure_passes_both_checks(volumes):
    """Some USB enclosures report 0000000000000000: still the same disk."""
    disk1 = next(d for d in volumes.wmi.Win32_DiskDrive.return_value if d.Index == 1)
    disk1.SerialNumber = "0000000000000000"
    locks = prepare_target(_target(serial_number="0000000000000000"))
    assert locks == [101, 102, 103, 104]
    assert volumes.released == []


def test_all_zero_serial_still_catches_another_model_after_locking(volumes):
    disk1 = next(d for d in volumes.wmi.Win32_DiskDrive.return_value if d.Index == 1)
    disk1.SerialNumber = "0000000000000000"
    _after_lock(volumes, Model="Other enclosure")
    with pytest.raises(UnsafeTargetError, match="Other enclosure"):
        prepare_target(_target(serial_number="0000000000000000"))
    assert volumes.released == [101, 102, 103, 104]


# ── Review fixes: a failing audit log never loses a lock ──────────────

def test_failing_log_after_locking_still_returns_the_locks(volumes, monkeypatch):
    def log(message, *_a, **_k):
        if message.startswith("Target verified"):
            raise OSError("log file locked")
    monkeypatch.setattr(device_mod, "audit_log", log)
    locks = prepare_target(_target())
    assert locks == [101, 102, 103, 104]
    assert volumes.released == []


def test_close_drive_survives_a_failing_log(monkeypatch):
    closed = []

    def broken_log(*_a, **_k):
        raise OSError("log file locked")

    monkeypatch.setattr(device_mod, "audit_log", broken_log)
    monkeypatch.setattr(device_mod.kernel32, "CloseHandle",
                        lambda h: closed.append(h) or True)
    device_mod.close_drive(42)  # must not raise
    assert closed == [42]


# ── The Windows calls behind it (faked kernel32) ───────────────────────

def test_volume_device_path():
    assert device_mod._volume_device_path("E:") == "\\\\.\\E:"
    assert device_mod._volume_device_path(VOL_HIDDEN) == (
        "\\\\.\\Volume{a0000000-0000-0000-0000-000000000003}")


def test_dismount_opens_a_volume_without_letter_by_its_name(monkeypatch):
    opened = []
    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod.kernel32, "CreateFileW",
                        lambda path, *rest: opened.append(path) or 42)
    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl", lambda *a: True)
    assert device_mod.dismount_volume(VOL_HIDDEN) == 42
    assert opened == ["\\\\.\\Volume{a0000000-0000-0000-0000-000000000003}"]


def _fake_find(monkeypatch, names, next_error=device_mod.ERROR_NO_MORE_FILES):
    record = {"closed": []}
    queue = list(names)

    def write(buf, value):
        ctypes.memmove(buf, ctypes.create_unicode_buffer(value), (len(value) + 1) * 2)

    def first(buf, _size):
        if not queue:
            ctypes.set_last_error(device_mod.ERROR_NO_MORE_FILES)
            return -1
        write(buf, queue.pop(0))
        return 77

    def nxt(handle, buf, _size):
        assert handle == 77
        if not queue:
            ctypes.set_last_error(next_error)
            return 0
        write(buf, queue.pop(0))
        return 1

    monkeypatch.setattr(device_mod.kernel32, "FindFirstVolumeW", first)
    monkeypatch.setattr(device_mod.kernel32, "FindNextVolumeW", nxt)
    monkeypatch.setattr(device_mod.kernel32, "FindVolumeClose",
                        lambda h: record["closed"].append(h) or True)
    return record


def test_volume_names_lists_every_volume_and_closes_the_search(monkeypatch):
    rec = _fake_find(monkeypatch, [VOL_C, VOL_E, VOL_HIDDEN])
    assert device_mod._volume_names() == [VOL_C, VOL_E, VOL_HIDDEN]
    assert rec["closed"] == [77]


def test_volume_names_failure_mid_way_raises(monkeypatch):
    rec = _fake_find(monkeypatch, [VOL_C], next_error=5)
    with pytest.raises(OSError):
        device_mod._volume_names()
    assert rec["closed"] == [77]


def test_volume_names_first_call_failure_raises(monkeypatch):
    def first(_buf, _size):
        ctypes.set_last_error(5)
        return -1
    monkeypatch.setattr(device_mod.kernel32, "FindFirstVolumeW", first)
    with pytest.raises(OSError):
        device_mod._volume_names()


def _fake_extents(monkeypatch, disks: list[int] | None, error: int = 0):
    """Fake the extents IOCTL: answers ``disks`` (MORE_DATA if the buffer is short)."""
    record = {"opened": [], "closed": [], "sizes": []}

    def create(path, access, *rest):
        record["opened"].append((path, access))
        return 55

    def ioctl(handle, code, _in, _in_len, out, out_len, _ret, _ov):
        assert code == device_mod.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS
        record["sizes"].append(out_len)
        if disks is None:
            ctypes.set_last_error(error)
            return 0
        room = (out_len - 8) // ctypes.sizeof(device_mod.DISK_EXTENT)
        base = ctypes.cast(out, ctypes.c_void_p).value
        ctypes.c_uint32.from_address(base).value = len(disks)
        if room < len(disks):
            ctypes.set_last_error(device_mod.ERROR_MORE_DATA)
            return 0
        for i, number in enumerate(disks):
            ctypes.c_uint32.from_address(base + 8 + 24 * i).value = number
        return 1

    monkeypatch.setattr(device_mod.kernel32, "CreateFileW", create)
    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl", ioctl)
    monkeypatch.setattr(device_mod.kernel32, "CloseHandle",
                        lambda h: record["closed"].append(h) or True)
    return record


def test_volume_disks_asks_without_read_or_write_access(monkeypatch):
    rec = _fake_extents(monkeypatch, [3])
    assert device_mod._volume_disks(VOL_HIDDEN) == {3}
    assert rec["opened"] == [
        ("\\\\.\\Volume{a0000000-0000-0000-0000-000000000003}", 0)]
    assert rec["closed"] == [55]


def test_volume_disks_spanning_volume_retries_with_room(monkeypatch):
    rec = _fake_extents(monkeypatch, [1, 4, 2])
    assert device_mod._volume_disks(VOL_HIDDEN) == {1, 2, 4}
    assert rec["sizes"] == [32, 8 + 3 * 24]
    assert rec["closed"] == [55]


def test_volume_disks_without_extents_is_none(monkeypatch):
    rec = _fake_extents(monkeypatch, None, error=1)   # CD drive: invalid function
    assert device_mod._volume_disks(VOL_CD) is None
    assert rec["closed"] == [55]


def test_volume_disks_unopenable_volume_is_none(monkeypatch):
    monkeypatch.setattr(device_mod.kernel32, "CreateFileW", lambda *a: -1)
    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl",
                        MagicMock(side_effect=AssertionError("no handle, no IOCTL")))
    assert device_mod._volume_disks(VOL_HIDDEN) is None


def test_unit_tests_never_see_the_hosts_volumes():
    """conftest: the volume scan answers "no volumes" in every unit test."""
    assert device_mod._volume_names() == []
    assert device_mod._volume_at("C:\\") is None
