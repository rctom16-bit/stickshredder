"""Tests for the 2.0 safety fixes in wipe.device: the BitLocker probe
(Explorer's property first, one WMI query per scan as administrator) and
prepare_target refusals."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import wipe.device as device_mod
from wipe.device import DeviceInfo, UnsafeTargetError, _check_bitlocker, prepare_target


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(device_mod, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(device_mod, "_bitlocker_wmi_denied", False)


# ── BitLocker: Explorer's property first ───────────────────────────────

@pytest.fixture
def shell(monkeypatch):
    """Explorer's BitLocker property per letter; records the letters asked."""
    state = SimpleNamespace(values={}, asked=[])

    def value(letter):
        state.asked.append(letter)
        result = state.values.get(letter)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(device_mod, "_shell_bitlocker_value", value)
    return state


@pytest.fixture
def wmi_volumes(monkeypatch):
    """Fake BitLocker WMI namespace; ``state.connects`` counts connections."""
    state = SimpleNamespace(volumes=[], connects=0, error=None)

    def connect():
        state.connects += 1
        if state.error is not None:
            raise state.error
        return SimpleNamespace(Win32_EncryptableVolume=lambda: list(state.volumes))

    monkeypatch.setattr(device_mod, "_get_bitlocker_wmi_connection", connect)
    return state


def _admin(monkeypatch, admin: bool) -> None:
    monkeypatch.setattr(device_mod, "_is_admin", lambda: admin)


@pytest.mark.parametrize("value", [1, 3, 4, 5, 6, 8, "1"])
def test_bitlocker_shell_encrypted_states_count(monkeypatch, shell, wmi_volumes, value):
    # on, encrypting, decrypting, suspended, locked, waiting for activation
    _admin(monkeypatch, True)
    shell.values["E:"] = value
    assert _check_bitlocker("E:") is True
    assert wmi_volumes.connects == 0


@pytest.mark.parametrize("value", [2, 0, 7])
def test_bitlocker_shell_off_or_not_encryptable_is_false(monkeypatch, shell, wmi_volumes, value):
    _admin(monkeypatch, True)
    shell.values["E:"] = value
    assert _check_bitlocker("E:") is False
    assert wmi_volumes.connects == 0


def test_bitlocker_shell_asks_with_upper_case_letter(monkeypatch, shell):
    _admin(monkeypatch, False)
    shell.values["E:"] = 1
    assert _check_bitlocker("e:") is True
    assert shell.asked == ["E:"]


@pytest.mark.parametrize("answer", [None, "", "garbage", OSError("COM failed")])
def test_bitlocker_without_admin_never_asks_wmi(monkeypatch, shell, wmi_volumes, answer):
    """Without administrator rights WMI and manage-bde are refused after ~5 s
    each — that made the list slow. No answer from Explorer: not encrypted."""
    _admin(monkeypatch, False)
    shell.values["E:"] = answer
    assert _check_bitlocker("E:") is False
    assert wmi_volumes.connects == 0


def _vol(protection: int, conversion: int, letter: str = "E:"):
    return SimpleNamespace(DriveLetter=letter, ProtectionStatus=protection,
                           ConversionStatus=conversion)


@pytest.mark.parametrize("protection,conversion,expected", [
    (1, 1, True),    # protection on
    (0, 0, False),   # fully decrypted
    (0, 1, True),    # suspended, data still encrypted
    (2, 1, True),    # locked
    (0, 2, True),    # encryption in progress
])
def test_bitlocker_wmi_as_admin_when_shell_has_no_answer(
        monkeypatch, shell, wmi_volumes, protection, conversion, expected):
    _admin(monkeypatch, True)
    wmi_volumes.volumes = [_vol(protection, conversion)]
    assert _check_bitlocker("E:") is expected
    assert wmi_volumes.connects == 1


def test_bitlocker_wmi_no_volume_entry_is_false(monkeypatch, shell, wmi_volumes):
    _admin(monkeypatch, True)
    wmi_volumes.volumes = [_vol(1, 1, letter="F:")]
    assert _check_bitlocker("E:") is False


def test_bitlocker_wmi_is_queried_once_per_scan(monkeypatch, shell, wmi_volumes):
    _admin(monkeypatch, True)
    wmi_volumes.volumes = [_vol(1, 1, "E:"), _vol(0, 0, "F:")]
    with device_mod._scan_cache():
        assert _check_bitlocker("E:") is True
        assert _check_bitlocker("F:") is False
    assert wmi_volumes.connects == 1
    # A new scan reads the state again.
    with device_mod._scan_cache():
        _check_bitlocker("E:")
    assert wmi_volumes.connects == 2


def test_bitlocker_wmi_denial_is_tried_once_per_run(monkeypatch, shell, wmi_volumes):
    _admin(monkeypatch, True)
    wmi_volumes.error = PermissionError("Access denied")
    assert _check_bitlocker("E:") is False
    assert _check_bitlocker("F:") is False
    assert wmi_volumes.connects == 1


def test_bitlocker_shell_property_reads_this_pc(monkeypatch):
    calls = []

    class Item:
        def ExtendedProperty(self, name):  # noqa: N802 — COM method name
            calls.append(name)
            return 6

    class Folder:
        def ParseName(self, name):  # noqa: N802 — COM method name
            calls.append(name)
            return Item()

    class Shell:
        def NameSpace(self, what):  # noqa: N802 — COM method name
            calls.append(what)
            return Folder()

    monkeypatch.setattr(device_mod, "_shell_application", Shell)
    assert device_mod._shell_bitlocker_value("E:") == 6
    assert calls == [17, "E:\\", "System.Volume.BitLockerProtection"]


def test_bitlocker_shell_unknown_drive_has_no_answer(monkeypatch):
    shell = SimpleNamespace(NameSpace=lambda _w: SimpleNamespace(ParseName=lambda _n: None))
    monkeypatch.setattr(device_mod, "_shell_application", lambda: shell)
    assert device_mod._shell_bitlocker_value("Q:") is None


def test_bitlocker_wmi_namespace_initialises_com(monkeypatch):
    calls = []
    monkeypatch.setattr(device_mod.pythoncom, "CoInitialize", lambda: calls.append("com"))
    monkeypatch.setattr(device_mod.wmi, "WMI",
                        lambda **kw: calls.append(kw.get("namespace")) or "conn")
    assert device_mod._get_bitlocker_wmi_connection() == "conn"
    assert calls == ["com", r"root\CIMV2\Security\MicrosoftVolumeEncryption"]


# ── handle.exe probe: skipped where it can't succeed ───────────────────

def test_open_handle_probe_skipped_without_admin(monkeypatch):
    _admin(monkeypatch, False)
    monkeypatch.setattr(device_mod, "_handle_exe_missing", False)
    monkeypatch.setattr(device_mod.subprocess, "run",
                        MagicMock(side_effect=AssertionError("must not run")))
    assert device_mod._check_active_processes("E:") is False


def test_open_handle_probe_remembers_a_missing_tool(monkeypatch):
    _admin(monkeypatch, True)
    monkeypatch.setattr(device_mod, "_handle_exe_missing", False)
    run = MagicMock(side_effect=FileNotFoundError("handle.exe"))
    monkeypatch.setattr(device_mod.subprocess, "run", run)
    assert device_mod._check_active_processes("E:") is False
    assert device_mod._check_active_processes("F:") is False
    assert run.call_count == 1


# ── prepare_target ─────────────────────────────────────────────────────

def _target(**overrides) -> DeviceInfo:
    base = dict(
        drive_letter="E:", device_id=r"\\.\PhysicalDrive1", model="Disk1",
        serial_number="SN1", capacity_bytes=64 * 1024**3, filesystem="FAT32",
        connection_type="USB", is_removable=True, is_system_drive=False,
        disk_letters=["E:"],
    )
    base.update(overrides)
    return DeviceInfo(**base)


@pytest.fixture
def gate(monkeypatch):
    disk = SimpleNamespace(Index=1, SerialNumber="SN1", Model="Disk1", Size=64 * 1024**3)
    conn = SimpleNamespace(Win32_DiskDrive=lambda: [disk])
    monkeypatch.setattr(device_mod, "_get_wmi_connection", lambda: conn)
    monkeypatch.setattr(device_mod, "_system_disk_indices", lambda c: {0})
    monkeypatch.setattr(device_mod, "_letters_on_disk", lambda c, i: ["E:"])
    dismount = MagicMock()
    monkeypatch.setattr(device_mod, "dismount_volume", dismount)
    return dismount


def test_prepare_target_refuses_flagged_system_drive(gate):
    with pytest.raises(UnsafeTargetError, match="Systemplatte"):
        prepare_target(_target(is_system_drive=True))
    gate.assert_not_called()


def test_prepare_target_refuses_flagged_system_drive_before_wmi(monkeypatch, gate):
    def boom():
        raise AssertionError("must not even re-read WMI")
    monkeypatch.setattr(device_mod, "_get_wmi_connection", boom)
    with pytest.raises(UnsafeTargetError, match="system"):
        prepare_target(_target(is_system_drive=True))


def test_prepare_target_access_denied_names_admin_rights(gate):
    err = OSError("Cannot open volume \\\\.\\E: for dismount (error 5)")
    err.winerror = 5
    gate.side_effect = err
    with pytest.raises(UnsafeTargetError) as info:
        prepare_target(_target())
    msg = str(info.value)
    assert "Administratorrechte erforderlich" in msg
    assert "administrator rights required" in msg
    assert "E:" in msg


def test_prepare_target_other_dismount_error_has_no_admin_hint(gate):
    err = OSError("FSCTL_DISMOUNT_VOLUME failed (error 32)")
    err.winerror = 32
    gate.side_effect = err
    with pytest.raises(UnsafeTargetError) as info:
        prepare_target(_target())
    assert "Administratorrechte" not in str(info.value)


def test_prepare_target_happy_path_still_dismounts(gate):
    prepare_target(_target())
    gate.assert_called_once_with("E:")


# ── dismount_volume carries the Win32 error code ───────────────────────

def test_dismount_open_failure_carries_winerror(monkeypatch):
    monkeypatch.setattr(device_mod.kernel32, "CreateFileW", lambda *a: -1)
    monkeypatch.setattr(device_mod.ctypes, "get_last_error", lambda: 5)
    with pytest.raises(OSError) as info:
        device_mod.dismount_volume("E:")
    assert info.value.winerror == 5


def test_dismount_ioctl_failure_carries_winerror(monkeypatch):
    monkeypatch.setattr(device_mod.kernel32, "CreateFileW", lambda *a: 42)
    monkeypatch.setattr(device_mod.kernel32, "CloseHandle", lambda h: True)
    monkeypatch.setattr(device_mod.kernel32, "DeviceIoControl", lambda *a: False)
    monkeypatch.setattr(device_mod.ctypes, "get_last_error", lambda: 5)
    with pytest.raises(OSError) as info:
        device_mod.dismount_volume("E:")
    assert info.value.winerror == 5
