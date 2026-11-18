"""Removing the partition table right before the first write.

A disk made with Rufus carries a small "UEFI_NTFS" partition whose GPT
attributes say read-only. Windows refuses every raw write into it with error 1,
even with all volumes locked. Deleting the drive layout on the open physical
handle (IOCTL_DISK_DELETE_DRIVE_LAYOUT, then IOCTL_DISK_UPDATE_PROPERTIES)
lifts that block. Only fakes: no IOCTL reaches a real drive.
"""

from __future__ import annotations

import ctypes

import pytest

import wipe.device as device_mod
from wipe.methods import ZeroFill

HANDLE = 4321
DELETE_DRIVE_LAYOUT = 0x0007C100
UPDATE_PROPERTIES = 0x00070140


@pytest.fixture()
def ioctl(monkeypatch):
    """Fake DeviceIoControl on wipe.device; ``fail`` maps a code to a Win32 error."""
    state = {"calls": [], "fail": {}, "logs": []}

    def fake(handle, code, in_buf, in_len, out_buf, out_len, returned, overlapped):
        state["calls"].append((handle, code, in_buf, in_len, out_buf, out_len))
        if code in state["fail"]:
            ctypes.set_last_error(state["fail"][code])
            return 0
        return 1

    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl", fake)
    monkeypatch.setattr(device_mod, "audit_log", state["logs"].append)
    return state


def test_unit_tests_never_reach_the_real_device_io_control_of_wipe_device():
    assert not isinstance(device_mod.kernel32.DeviceIoControl, ctypes._CFuncPtr)


def test_clear_deletes_the_layout_then_tells_windows_to_reread_it(ioctl):
    assert device_mod.clear_partition_table(HANDLE) is True

    assert [c[:2] for c in ioctl["calls"]] == [
        (HANDLE, DELETE_DRIVE_LAYOUT), (HANDLE, UPDATE_PROPERTIES),
    ]
    for _h, _code, in_buf, in_len, out_buf, out_len in ioctl["calls"]:
        assert (in_buf, in_len, out_buf, out_len) == (None, 0, None, 0)
    assert any("partition table" in line.lower() for line in ioctl["logs"])


def test_clear_failure_is_logged_and_never_raises(ioctl):
    ioctl["fail"][DELETE_DRIVE_LAYOUT] = 19

    assert device_mod.clear_partition_table(HANDLE) is False

    assert [c[1] for c in ioctl["calls"]] == [DELETE_DRIVE_LAYOUT]
    assert any("error 19" in line for line in ioctl["logs"])


def test_failed_reread_is_logged_and_never_raises(ioctl):
    ioctl["fail"][UPDATE_PROPERTIES] = 1

    assert device_mod.clear_partition_table(HANDLE) is False

    assert [c[1] for c in ioctl["calls"]] == [DELETE_DRIVE_LAYOUT, UPDATE_PROPERTIES]
    assert any("error 1" in line for line in ioctl["logs"])


def test_clear_survives_a_crashing_ioctl_and_a_broken_log(monkeypatch):
    def boom(*_args):
        raise ctypes.ArgumentError("bad handle")

    def broken_log(_msg):
        raise OSError("log locked")

    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl", boom)
    monkeypatch.setattr(device_mod, "audit_log", broken_log)

    assert device_mod.clear_partition_table(HANDLE) is False


def test_clear_with_a_broken_log_still_reports_success(monkeypatch):
    def broken_log(_msg):
        raise OSError("log locked")

    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl", lambda *a: 1)
    monkeypatch.setattr(device_mod, "audit_log", broken_log)

    assert device_mod.clear_partition_table(HANDLE) is True


def test_releasing_locks_of_vanished_volumes_stays_harmless(monkeypatch):
    """Once the layout is gone the locked volumes vanish; closing their
    handles may fail or raise and must not break the cleanup."""
    closed = []

    def close(handle):
        closed.append(handle)
        if handle == 102:
            raise OSError("volume gone")
        return 0  # CloseHandle reports failure

    monkeypatch.setattr(device_mod, "audit_log", lambda *_a: None)
    monkeypatch.setattr(device_mod.kernel32, "CloseHandle", close)

    device_mod.release_volumes([101, 102, 103])  # must not raise
    assert closed == [101, 102, 103]


# ── A write Windows blocks gets a clear message ─────────────────────────


@pytest.mark.parametrize("code", [1, 19])
def test_write_blocked_by_windows_names_the_read_only_partition(monkeypatch, code):
    import wipe.methods as methods_mod

    def blocked(handle, data):
        exc = OSError(f"WriteFile failed with error code {code}")
        exc.winerror = code
        raise exc

    monkeypatch.setattr(methods_mod, "_write_block", blocked)
    monkeypatch.setattr(methods_mod, "_set_file_pointer", lambda h, p: True)
    monkeypatch.setattr(methods_mod, "audit_log", lambda *_a: None)

    result = ZeroFill().execute(handle=1, drive_size=100 * 1024, block_size=1024)

    assert result.success is False
    assert result.bad_sector_count == 0
    assert "Windows blockiert das Schreiben in eine schreibgeschützte Partition" in (
        result.error_message
    )
    assert f"error {code}" in result.error_message
