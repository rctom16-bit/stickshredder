"""Tests for wipe.sat — ATA commands tunnelled through a USB bridge (SAT).

No hardware is touched: ``kernel32.DeviceIoControl`` is replaced with a fake
that inspects the SCSI_PASS_THROUGH_DIRECT request the module builds, fills
the data buffer and sets the SCSI status. The CDB layout is checked byte for
byte, and every non-read command must be refused before any IOCTL is sent.
"""

from __future__ import annotations

import ctypes
import struct

import pytest

import wipe.passthrough as pt
import wipe.sat as sat

FAKE_HANDLE = 0x5A7


@pytest.fixture(autouse=True)
def _silence_audit_log(monkeypatch):
    """Keep test runs out of the user's real audit log."""
    monkeypatch.setattr(sat, "audit_log", lambda msg: None)
    monkeypatch.setattr(pt, "audit_log", lambda msg: None)


# ── IDENTIFY image builders (shared with test_drive_probe) ───────────────

def _ata_string_words(text: str, nwords: int) -> list[int]:
    padded = text.ljust(nwords * 2)[: nwords * 2]
    return [(ord(padded[2 * i]) << 8) | ord(padded[2 * i + 1]) for i in range(nwords)]


def make_identify(
    *,
    model: str = "CT500MX500SSD1",
    serial: str = "2001E2A1B2C3",
    firmware: str = "M3CR023",
    lba48_sectors: int = 976773168,
    lba28_sectors: int = 0x0FFFFFFF,
    word59: int = 0,
    word82: int = 0x0002,
    word85: int = 0,
    word128: int = 0x0001,
    checksum: bool = True,
) -> bytes:
    """Build a 512-byte IDENTIFY DEVICE image with the fields we parse."""
    words = [0] * 256
    words[0] = 0x0040  # fixed device, ATA (bit 15 clear)
    for i, w in enumerate(_ata_string_words(serial, 10)):
        words[10 + i] = w
    for i, w in enumerate(_ata_string_words(firmware, 4)):
        words[23 + i] = w
    for i, w in enumerate(_ata_string_words(model, 20)):
        words[27 + i] = w
    words[59] = word59
    words[60] = lba28_sectors & 0xFFFF
    words[61] = (lba28_sectors >> 16) & 0xFFFF
    words[82] = word82
    words[85] = word85
    for i in range(4):
        words[100 + i] = (lba48_sectors >> (16 * i)) & 0xFFFF
    words[128] = word128
    raw = bytearray(struct.pack("<256H", *words))
    if checksum:
        raw[510] = 0xA5
        raw[511] = (-sum(raw[:511])) & 0xFF
    return bytes(raw)


def corrupt_checksum(identify: bytes) -> bytes:
    raw = bytearray(identify)
    raw[511] = (raw[511] + 1) & 0xFF
    return bytes(raw)


# ── Fake DeviceIoControl for SCSI pass-through ───────────────────────────

def make_fake_sptd(
    *,
    data: bytes = b"",
    ok: bool = True,
    scsi_status: int = 0,
    transferred: int | None = None,
    last_error: int = 1,
    capture: list | None = None,
):
    """Return a stand-in for ``kernel32.DeviceIoControl`` (SCSI pass-through)."""

    def fake(handle, ioctl, in_addr, in_size, out_addr, out_size, bytesret, overlapped):
        sptd = sat.SCSI_PASS_THROUGH_DIRECT.from_address(in_addr)
        if capture is not None:
            capture.append({
                "handle": handle,
                "ioctl": ioctl,
                "in_size": in_size,
                "out_size": out_size,
                "same_buffer": in_addr == out_addr,
                "length": sptd.Length,
                "cdb_length": sptd.CdbLength,
                "cdb": bytes(sptd.Cdb[: sptd.CdbLength]),
                "data_in": sptd.DataIn,
                "dtl": sptd.DataTransferLength,
                "timeout": sptd.TimeOutValue,
                "sense_len": sptd.SenseInfoLength,
                "sense_off": sptd.SenseInfoOffset,
                "has_buffer": bool(sptd.DataBuffer),
            })
        if not ok:
            ctypes.set_last_error(last_error)
            return 0
        if data and sptd.DataBuffer:
            ctypes.memmove(sptd.DataBuffer, data, min(len(data), sptd.DataTransferLength))
        sptd.ScsiStatus = scsi_status
        if transferred is not None:
            sptd.DataTransferLength = transferred
        return 1

    return fake


# ── Structure layout ─────────────────────────────────────────────────────

@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="64-bit layout")
def test_scsi_pass_through_direct_matches_win64_layout():
    s = sat.SCSI_PASS_THROUGH_DIRECT
    assert ctypes.sizeof(s) == 56
    assert s.DataTransferLength.offset == 12
    assert s.TimeOutValue.offset == 16
    assert s.DataBuffer.offset == 24
    assert s.SenseInfoOffset.offset == 32
    assert s.Cdb.offset == 36


# ── CDB layout (byte-exact) ──────────────────────────────────────────────

def test_identify_cdb_is_byte_exact(monkeypatch):
    calls = []
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=make_identify(), capture=calls))

    sat.sat_identify(FAKE_HANDLE)

    assert len(calls) == 1
    assert calls[0]["cdb"] == bytes([
        0x85,        # ATA PASS-THROUGH (16)
        0x08,        # PROTOCOL=4 (PIO data-in), EXTEND=0
        0x0E,        # T_DIR=1, BYT_BLOK=1, T_LENGTH=2 (sector count field)
        0x00, 0x00,  # FEATURES
        0x00, 0x01,  # SECTOR_COUNT = 1 block (512 bytes)
        0x00, 0x00,  # LBA low
        0x00, 0x00,  # LBA mid
        0x00, 0x00,  # LBA high
        0x00,        # DEVICE
        0xEC,        # IDENTIFY DEVICE
        0x00,        # CONTROL
    ])


def test_smart_read_data_cdb_is_byte_exact(monkeypatch):
    calls = []
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=bytes(512), capture=calls))

    sat.sat_ata_command(FAKE_HANDLE, 0xB0, features=0xD0, sector_count=1, lba=0xC24F00)

    assert calls[0]["cdb"] == bytes([
        0x85, 0x08, 0x0E,
        0x00, 0xD0,  # FEATURES = SMART READ DATA
        0x00, 0x01,
        0x00, 0x00,  # LBA low
        0x00, 0x4F,  # LBA mid = 0x4F
        0x00, 0xC2,  # LBA high = 0xC2
        0x00,
        0xB0,        # SMART
        0x00,
    ])


def test_request_is_data_in_with_sense_buffer(monkeypatch):
    calls = []
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=make_identify(), capture=calls))

    sat.sat_ata_command(FAKE_HANDLE, 0xEC, timeout_s=7)

    c = calls[0]
    assert c["ioctl"] == sat.IOCTL_SCSI_PASS_THROUGH_DIRECT == 0x4D014
    assert c["handle"] == FAKE_HANDLE
    assert c["same_buffer"]
    assert c["length"] == ctypes.sizeof(sat.SCSI_PASS_THROUGH_DIRECT)
    assert c["cdb_length"] == 16
    assert c["data_in"] == sat.SCSI_IOCTL_DATA_IN == 1
    assert c["dtl"] == 512
    assert c["timeout"] == 7
    assert c["has_buffer"]
    assert c["sense_len"] > 0
    # The sense area must sit inside the buffer handed to the driver.
    assert c["sense_off"] + c["sense_len"] <= c["in_size"] == c["out_size"]


# ── Success / failure handling ───────────────────────────────────────────

def test_returns_data_on_success(monkeypatch):
    payload = bytes(range(256)) * 2
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl", make_fake_sptd(data=payload))

    assert sat.sat_ata_command(FAKE_HANDLE, 0xEC) == payload


def test_returns_none_when_ioctl_fails(monkeypatch):
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(ok=False, last_error=5))

    assert sat.sat_ata_command(FAKE_HANDLE, 0xEC) is None


def test_returns_none_on_check_condition(monkeypatch):
    # Bridges without SAT answer CHECK CONDITION / ILLEGAL REQUEST.
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=make_identify(), scsi_status=0x02))

    assert sat.sat_ata_command(FAKE_HANDLE, 0xEC) is None


def test_returns_none_on_short_transfer(monkeypatch):
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=make_identify(), transferred=100))

    assert sat.sat_ata_command(FAKE_HANDLE, 0xEC) is None


def test_returns_none_when_device_io_control_raises(monkeypatch):
    def boom(*args):
        raise OSError("driver exploded")

    monkeypatch.setattr(sat.kernel32, "DeviceIoControl", boom)

    assert sat.sat_ata_command(FAKE_HANDLE, 0xEC) is None
    assert sat.sat_identify(FAKE_HANDLE) is None


# ── Read-only guard ──────────────────────────────────────────────────────

@pytest.mark.parametrize("command, features", [
    (0xF4, 0x00),  # SECURITY ERASE UNIT
    (0xF3, 0x00),  # SECURITY ERASE PREPARE
    (0xF1, 0x00),  # SECURITY SET PASSWORD
    (0xB4, 0x11),  # SANITIZE — CRYPTO SCRAMBLE EXT
    (0xB4, 0x12),  # SANITIZE — BLOCK ERASE EXT
    (0xB4, 0x14),  # SANITIZE — OVERWRITE EXT
    (0x37, 0x00),  # SET MAX ADDRESS EXT
    (0xF9, 0x00),  # SET MAX ADDRESS
    (0xB1, 0xC3),  # DEVICE CONFIGURATION SET
    (0xB1, 0xC0),  # DEVICE CONFIGURATION RESTORE
    (0xB0, 0xD6),  # SMART WRITE LOG
    (0xB0, 0xD8),  # SMART ENABLE OPERATIONS
    (0x30, 0x00),  # WRITE SECTORS
    (0x35, 0x00),  # WRITE DMA EXT
    (0xEC, 0x01),  # IDENTIFY with unexpected features — not on the list
])
def test_refuses_non_read_commands_without_sending(monkeypatch, command, features):
    calls = []
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=bytes(512), capture=calls))

    assert sat.sat_ata_command(FAKE_HANDLE, command, features=features) is None
    assert calls == []


@pytest.mark.parametrize("kwargs", [
    {"data_in_len": 0},
    {"data_in_len": 100},
    {"data_in_len": 512, "sector_count": 2},
    {"lba": 0x1000_0000},
])
def test_rejects_malformed_requests_without_sending(monkeypatch, kwargs):
    calls = []
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=bytes(512), capture=calls))

    assert sat.sat_ata_command(FAKE_HANDLE, 0xEC, **kwargs) is None
    assert calls == []


# ── sat_identify ─────────────────────────────────────────────────────────

def test_sat_identify_parses_with_pr1_identify_model(monkeypatch):
    data = make_identify(
        model="Samsung SSD 870 EVO 500GB",
        serial="S6PXNM0T123456",
        firmware="SVT02B6Q",
        lba48_sectors=976773168,
        word128=0x0001 | 0x0008 | 0x0020,  # supported, frozen, enhanced erase
    )
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl", make_fake_sptd(data=data))

    info = sat.sat_identify(FAKE_HANDLE)

    assert isinstance(info, pt.IdentifyData)
    assert info.model == "Samsung SSD 870 EVO 500GB"
    assert info.serial == "S6PXNM0T123456"
    assert info.firmware == "SVT02B6Q"
    assert info.lba48_max_sectors == 976773168
    assert info.security_supported is True
    assert info.security_frozen is True
    assert info.enhanced_erase_supported is True
    assert info.security_locked is False
    assert len(info.raw_words) == 256


def test_sat_identify_accepts_image_without_checksum_signature(monkeypatch):
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=make_identify(checksum=False)))

    assert sat.sat_identify(FAKE_HANDLE) is not None


def test_sat_identify_rejects_all_zero_data(monkeypatch):
    # Some bridges report success but return an empty buffer.
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl", make_fake_sptd(data=bytes(512)))

    assert sat.sat_identify(FAKE_HANDLE) is None


def test_sat_identify_rejects_bad_checksum(monkeypatch):
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=corrupt_checksum(make_identify())))

    assert sat.sat_identify(FAKE_HANDLE) is None


def test_sat_identify_returns_none_when_bridge_rejects(monkeypatch):
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl", make_fake_sptd(ok=False))

    assert sat.sat_identify(FAKE_HANDLE) is None


def test_parse_identify_data_rejects_short_buffer():
    assert sat.parse_identify_data(b"\x01" * 100) is None


# ── sat_dco_max_lba (DEVICE CONFIGURATION IDENTIFY, read-only) ───────────

def _dco_image(max_lba: int) -> bytes:
    words = [0] * 256
    words[0] = 0x0002  # DCO data structure revision
    for i in range(4):
        words[3 + i] = (max_lba >> (16 * i)) & 0xFFFF
    return struct.pack("<256H", *words)


def test_sat_dco_max_lba_sends_dco_identify_and_parses_words_3_to_6(monkeypatch):
    calls = []
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=_dco_image(976773167), capture=calls))

    assert sat.sat_dco_max_lba(FAKE_HANDLE) == 976773167
    cdb = calls[0]["cdb"]
    assert cdb[14] == 0xB1 and cdb[4] == 0xC2


def test_sat_dco_max_lba_none_when_unsupported(monkeypatch):
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(scsi_status=0x02))

    assert sat.sat_dco_max_lba(FAKE_HANDLE) is None
