"""Tests for the 2.0 safety fixes in wipe.device: BitLocker via WMI with a
German/English manage-bde fallback, and prepare_target refusals."""

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


def test_bitlocker_wmi_denial_is_tried_once_per_run(monkeypatch, bde):
    tries = []

    def denied():
        tries.append(1)
        raise PermissionError("Access denied")
    monkeypatch.setattr(device_mod, "_get_bitlocker_wmi_connection", denied)
    bde.stdout = ENGLISH_ON
    assert _check_bitlocker("E:") is True
    assert _check_bitlocker("F:") is True
    assert len(tries) == 1
    assert bde.calls == 2


# ── BitLocker: WMI first ───────────────────────────────────────────────

def _vol(protection: int, conversion: int, letter: str = "E:"):
    return SimpleNamespace(DriveLetter=letter, ProtectionStatus=protection,
                           ConversionStatus=conversion)


@pytest.fixture
def bde(monkeypatch):
    """Stub manage-bde; records whether it was called."""
    state = SimpleNamespace(stdout="", calls=0)

    def run(*a, **k):
        state.calls += 1
        return SimpleNamespace(stdout=state.stdout, stderr="", returncode=0)

    monkeypatch.setattr(device_mod.subprocess, "run", run)
    return state


def _use_volumes(monkeypatch, *vols):
    conn = SimpleNamespace(Win32_EncryptableVolume=lambda **kw: [
        v for v in vols if v.DriveLetter == kw.get("DriveLetter", v.DriveLetter)
    ])
    monkeypatch.setattr(device_mod, "_get_bitlocker_wmi_connection", lambda: conn)


def test_bitlocker_wmi_protection_on(monkeypatch, bde):
    _use_volumes(monkeypatch, _vol(1, 1))
    assert _check_bitlocker("E:") is True
    assert bde.calls == 0


def test_bitlocker_wmi_fully_decrypted_off(monkeypatch, bde):
    _use_volumes(monkeypatch, _vol(0, 0))
    assert _check_bitlocker("E:") is False
    assert bde.calls == 0


def test_bitlocker_wmi_suspended_but_encrypted_counts(monkeypatch, bde):
    # Protection suspended (0) but data still encrypted (ConversionStatus 1).
    _use_volumes(monkeypatch, _vol(0, 1))
    assert _check_bitlocker("E:") is True


def test_bitlocker_wmi_locked_volume_counts(monkeypatch, bde):
    _use_volumes(monkeypatch, _vol(2, 1))
    assert _check_bitlocker("E:") is True


def test_bitlocker_wmi_encryption_in_progress_counts(monkeypatch, bde):
    _use_volumes(monkeypatch, _vol(0, 2))
    assert _check_bitlocker("E:") is True


def test_bitlocker_wmi_no_volume_entry_is_false(monkeypatch, bde):
    _use_volumes(monkeypatch, _vol(1, 1, letter="F:"))
    assert _check_bitlocker("E:") is False
    assert bde.calls == 0


def test_bitlocker_wmi_namespace_initialises_com(monkeypatch):
    calls = []
    monkeypatch.setattr(device_mod.pythoncom, "CoInitialize", lambda: calls.append("com"))
    monkeypatch.setattr(device_mod.wmi, "WMI",
                        lambda **kw: calls.append(kw.get("namespace")) or "conn")
    assert device_mod._get_bitlocker_wmi_connection() == "conn"
    assert calls == ["com", r"root\CIMV2\Security\MicrosoftVolumeEncryption"]


# ── BitLocker: manage-bde fallback when WMI is denied ──────────────────

def _deny_wmi(monkeypatch):
    def denied():
        raise PermissionError("Access denied (non-admin)")
    monkeypatch.setattr(device_mod, "_get_bitlocker_wmi_connection", denied)


GERMAN_ON = """\
BitLocker-Laufwerkverschlüsselung: Konfigurationstool, Version 10.0.26100
Volume "E:" [STICK]
[Datenvolume]

    Größe:                        58,59 GB
    BitLocker-Version:            2.0
    Konvertierungsstatus:         Nur verwendeter Speicherplatz verschlüsselt
    Verschlüsselt (Prozent):      100,0 %
    Verschlüsselungsmethode:      XTS-AES 128
    Schutzstatus:                 Der Schutz ist aktiviert.
    Sperrungsstatus:              Entsperrt
"""

GERMAN_OFF = """\
Volume "E:" [STICK]
    Konvertierungsstatus:         Vollständig entschlüsselt
    Verschlüsselt (Prozent):      0,0 %
    Schutzstatus:                 Der Schutz ist deaktiviert.
    Sperrungsstatus:              Entsperrt
"""

GERMAN_SUSPENDED = """\
    Konvertierungsstatus:         Vollständig verschlüsselt
    Verschlüsselt (Prozent):      100,0 %
    Schutzstatus:                 Der Schutz ist deaktiviert.
"""

ENGLISH_ON = """\
Volume E: [STICK]
    Conversion Status:    Fully Encrypted
    Percentage Encrypted: 100.0%
    Protection Status:    Protection On
    Lock Status:          Unlocked
"""

ENGLISH_OFF = """\
    Conversion Status:    Fully Decrypted
    Percentage Encrypted: 0.0%
    Protection Status:    Protection Off
    Lock Status:          Unlocked
"""

ENGLISH_PARTIAL = """\
    Conversion Status:    Encryption in Progress
    Percentage Encrypted: 12.5%
    Protection Status:    Protection Off
"""

GERMAN_DENIED = """\
FEHLER: Fehler beim Versuch, auf eine erforderliche Ressource zuzugreifen.
Zugriff verweigert
"""


@pytest.mark.parametrize("text,expected", [
    (GERMAN_ON, True),
    (GERMAN_OFF, False),
    (GERMAN_SUSPENDED, True),
    (ENGLISH_ON, True),
    (ENGLISH_OFF, False),
    (ENGLISH_PARTIAL, True),
    (GERMAN_DENIED, False),
    ("", False),
])
def test_bitlocker_manage_bde_fallback(monkeypatch, bde, text, expected):
    _deny_wmi(monkeypatch)
    bde.stdout = text
    assert _check_bitlocker("E:") is expected
    assert bde.calls == 1


def test_bitlocker_fallback_never_raises(monkeypatch):
    _deny_wmi(monkeypatch)

    def run(*a, **k):
        raise FileNotFoundError("manage-bde")
    monkeypatch.setattr(device_mod.subprocess, "run", run)
    assert _check_bitlocker("E:") is False


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
