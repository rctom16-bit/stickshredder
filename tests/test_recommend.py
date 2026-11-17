"""Tests for wipe.recommend — per-device recommendation (spec G6) and media type."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wipe.device import DeviceInfo
from wipe import recommend as rec_mod
from wipe.recommend import (
    MULTI_PASS_NOTE_DE,
    MULTI_PASS_NOTE_EN,
    Recommendation,
    media_type_of,
    recommend,
)


def _dev(**overrides) -> DeviceInfo:
    base = dict(
        drive_letter="E:",
        device_id=r"\\.\PhysicalDrive1",
        model="TestDisk",
        serial_number="SN1",
        capacity_bytes=64 * 1024**3,
        filesystem="NTFS",
        connection_type="SATA",
        is_removable=False,
        is_system_drive=False,
        is_internal=True,
    )
    base.update(overrides)
    return DeviceInfo(**base)


def _usb(**overrides) -> DeviceInfo:
    return _dev(connection_type="USB", is_removable=True, is_internal=False, **overrides)


# ── recommend() rules ──────────────────────────────────────────────────

def test_hdd_gets_one_zero_pass_full_verify_nist_clear():
    r = recommend(_dev(), "hdd", hardware_erase_available=False, schutzklasse=2)
    assert isinstance(r, Recommendation)
    assert (r.method_key, r.verify_mode, r.hardware_erase) == ("zero", "full", None)
    assert r.nist_category == "Clear"
    assert r.reason_de and r.reason_en
    assert r.warnings_de == [] and r.warnings_en == []


def test_hdd_ignores_hardware_erase_flag():
    r = recommend(_dev(), "hdd", hardware_erase_available=True, schutzklasse=3)
    assert r.hardware_erase is None
    assert r.nist_category == "Clear"


def test_sata_ssd_with_hardware_erase_gets_purge():
    r = recommend(_dev(), "ssd", hardware_erase_available=True, schutzklasse=2)
    assert r.hardware_erase == "secure-erase"
    assert (r.method_key, r.verify_mode) == ("zero", "full")
    assert r.nist_category == "Purge"
    assert "Beta" in r.reason_de and "Beta" in r.reason_en


def test_ssd_without_hardware_erase_overwrites_with_warning():
    r = recommend(_dev(), "ssd", hardware_erase_available=False, schutzklasse=2)
    assert r.hardware_erase is None
    assert (r.method_key, r.verify_mode) == ("zero", "full")
    assert r.nist_category == "Clear"
    assert len(r.warnings_de) == 1 and len(r.warnings_en) == 1
    assert "Hardware-Löschen" in r.warnings_de[0]


def test_usb_flash_overwrites_full_verify():
    r = recommend(_usb(), "flash", hardware_erase_available=False, schutzklasse=2)
    assert (r.method_key, r.verify_mode, r.hardware_erase) == ("zero", "full", None)
    assert r.nist_category == "Clear"
    assert not any("physisch vernichten" in w for w in r.warnings_de)


def test_usb_flash_schutzklasse_3_says_destroy_physically():
    r = recommend(_usb(), "flash", hardware_erase_available=False, schutzklasse=3)
    assert any("für Schutzklasse 3 physisch vernichten" in w for w in r.warnings_de)
    assert any("protection class 3" in w and "physically" in w for w in r.warnings_en)


def test_flash_never_gets_hardware_erase():
    r = recommend(_usb(), "flash", hardware_erase_available=True, schutzklasse=1)
    assert r.hardware_erase is None


def test_ssd_without_hardware_erase_schutzklasse_3_also_says_destroy():
    r = recommend(_dev(), "ssd", hardware_erase_available=False, schutzklasse=3)
    assert any("physisch vernichten" in w for w in r.warnings_de)


def test_unknown_media_falls_back_to_overwrite_with_warning():
    r = recommend(_dev(), "unknown", hardware_erase_available=False, schutzklasse=2)
    assert (r.method_key, r.verify_mode, r.hardware_erase) == ("zero", "full", None)
    assert r.nist_category == "Clear"
    assert r.warnings_de and r.warnings_en


@pytest.mark.parametrize("media", ["hdd", "ssd", "flash", "unknown"])
@pytest.mark.parametrize("hw", [True, False])
@pytest.mark.parametrize("klasse", [1, 2, 3])
def test_multi_pass_methods_are_never_recommended(media, hw, klasse):
    r = recommend(_dev(), media, hardware_erase_available=hw, schutzklasse=klasse)
    assert r.method_key == "zero"
    assert r.verify_mode == "full"
    assert len(r.warnings_de) == len(r.warnings_en)


def test_multi_pass_descriptions_carry_the_note():
    from wipe.methods import BsiVsitr, RandomThreePass
    for cls in (RandomThreePass, BsiVsitr):
        assert MULTI_PASS_NOTE_DE in cls.description_de
        assert MULTI_PASS_NOTE_EN in cls.description_en
    assert "nur wenn eine Vorgabe es verlangt" in MULTI_PASS_NOTE_DE
    assert "keinen Sicherheitsgewinn" in MULTI_PASS_NOTE_DE


# ── media_type_of() ────────────────────────────────────────────────────

def _storage(*disks):
    ns = SimpleNamespace(MSFT_PhysicalDisk=lambda: list(disks))
    return ns


def _pd(device_id: str, media: int, bus: int = 11):
    return SimpleNamespace(DeviceId=device_id, MediaType=media, BusType=bus)


@pytest.fixture
def storage(monkeypatch):
    def use(*disks):
        conn = _storage(*disks)
        monkeypatch.setattr(rec_mod, "_get_storage_wmi_connection", lambda: conn)
    return use


def test_media_hdd(storage):
    storage(_pd("0", 4), _pd("1", 3))
    assert media_type_of(_dev()) == "hdd"


def test_media_ssd(storage):
    storage(_pd("1", 4))
    assert media_type_of(_dev()) == "ssd"


def test_media_usb_stick_unspecified_is_flash(storage):
    storage(_pd("1", 0, bus=7))
    assert media_type_of(_usb()) == "flash"


def test_media_sd_card_is_flash(storage):
    storage(_pd("1", 0, bus=12))
    assert media_type_of(_dev(connection_type="SD")) == "flash"


def test_media_usb_hdd_stays_hdd(storage):
    storage(_pd("1", 3, bus=7))
    assert media_type_of(_usb()) == "hdd"


def test_media_internal_unspecified_is_unknown(storage):
    storage(_pd("1", 0))
    assert media_type_of(_dev()) == "unknown"


def test_media_disk_not_found_is_unknown(storage):
    storage(_pd("0", 4))
    assert media_type_of(_dev()) == "unknown"


def test_media_wmi_failure_is_unknown(monkeypatch):
    def boom():
        raise RuntimeError("WMI down")
    monkeypatch.setattr(rec_mod, "_get_storage_wmi_connection", boom)
    assert media_type_of(_usb()) == "unknown"


def test_media_bad_device_id_is_unknown(storage):
    storage(_pd("1", 4))
    assert media_type_of(_dev(device_id="garbage")) == "unknown"


def test_media_uses_com_initialising_helper():
    from wipe import device
    assert rec_mod._get_storage_wmi_connection is device._get_storage_wmi_connection
