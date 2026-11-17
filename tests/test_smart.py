"""Tests for wipe.smart — SMART health summary (ATA direct and via SAT bridge).

Parsers are checked against handcrafted SMART attribute tables; the readers
are checked with a mocked ``DeviceIoControl`` that inspects the exact ATA
command sent (SMART READ DATA only).
"""

from __future__ import annotations

import ctypes

import pytest

import wipe.passthrough as pt
import wipe.sat as sat
import wipe.smart as smart
from tests.test_sat import make_fake_sptd

FAKE_HANDLE = 0x5A8


@pytest.fixture(autouse=True)
def _silence_audit_log(monkeypatch):
    for mod in (smart, sat, pt):
        monkeypatch.setattr(mod, "audit_log", lambda msg: None)


# ── SMART table builder ──────────────────────────────────────────────────

def make_smart_table(attrs: dict[int, int], *, checksum: bool = True) -> bytes:
    """512-byte SMART READ DATA image: revision word + 30 x 12-byte entries.

    ``attrs`` maps attribute id → 48-bit raw value.
    """
    raw = bytearray(512)
    raw[0:2] = (0x0010).to_bytes(2, "little")
    for slot, (attr_id, value) in enumerate(attrs.items()):
        off = 2 + slot * 12
        raw[off] = attr_id
        raw[off + 1:off + 3] = (0x0032).to_bytes(2, "little")  # flags
        raw[off + 3] = 100  # current
        raw[off + 4] = 100  # worst
        raw[off + 5:off + 11] = value.to_bytes(6, "little")
    if checksum:
        raw[511] = (-sum(raw[:511])) & 0xFF
    return bytes(raw)


# ── parse_ata_smart ──────────────────────────────────────────────────────

def test_parse_extracts_reallocated_pending_uncorrectable():
    data = make_smart_table({1: 0, 5: 12, 9: 4000, 197: 3, 198: 2, 194: 35})

    s = smart.parse_ata_smart(data)

    assert s.available is True
    assert s.reallocated == 12
    assert s.pending == 3
    assert s.uncorrectable == 2
    assert s.media_errors is None
    assert s.percentage_used is None
    assert s.source == "ata"


def test_parse_uses_attribute_187_when_198_missing():
    s = smart.parse_ata_smart(make_smart_table({5: 0, 187: 7}))

    assert s.uncorrectable == 7


def test_parse_takes_the_worse_of_187_and_198():
    s = smart.parse_ata_smart(make_smart_table({187: 9, 198: 4}))

    assert s.uncorrectable == 9


def test_parse_missing_attributes_are_none():
    s = smart.parse_ata_smart(make_smart_table({9: 1234, 194: 30}))

    assert s.available is True
    assert (s.reallocated, s.pending, s.uncorrectable) == (None, None, None)


def test_parse_reallocated_reads_low_16_bits_like_smartctl():
    # Some vendors pack extra counters into the upper raw bytes of attr 5.
    s = smart.parse_ata_smart(make_smart_table({5: 0x0003_0001_0004, 197: 0x0001_0000_0000}))

    assert s.reallocated == 4
    assert s.pending == 0x0001_0000_0000  # 197 is a plain 48-bit counter


@pytest.mark.parametrize("data", [
    b"",
    None,
    bytes(100),
    bytes(512),                     # bridge returned nothing useful
])
def test_parse_unusable_data_is_unavailable(data):
    s = smart.parse_ata_smart(data)

    assert s.available is False
    assert s.reallocated is None and s.pending is None and s.uncorrectable is None


# ── read_ata_smart: direct ATA ───────────────────────────────────────────

def _fake_ata(*, data: bytes | None = None, status: int = 0x50, ok: bool = True,
              seen: list | None = None):
    def fake(handle, ioctl, in_addr, in_size, out_addr, out_size, bytesret, overlapped):
        aptd = pt.ATA_PASS_THROUGH_DIRECT.from_address(in_addr)
        if seen is not None:
            seen.append({
                "ioctl": ioctl,
                "tf": bytes(aptd.CurrentTaskFile),
                "flags": aptd.AtaFlags,
                "len": aptd.DataTransferLength,
            })
        if not ok:
            ctypes.set_last_error(1)
            return 0
        if data is not None and aptd.DataBuffer:
            ctypes.memmove(aptd.DataBuffer, data, len(data))
        aptd.CurrentTaskFile[6] = status
        return 1

    return fake


def test_read_ata_smart_sends_smart_read_data_via_ata_pass_through(monkeypatch):
    seen = []
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        _fake_ata(data=make_smart_table({5: 1, 197: 0, 198: 0}), seen=seen))

    s = smart.read_ata_smart(FAKE_HANDLE, "ata")

    assert len(seen) == 1
    tf = seen[0]["tf"]
    assert seen[0]["ioctl"] == pt.IOCTL_ATA_PASS_THROUGH_DIRECT
    assert tf[0] == 0xD0          # features: SMART READ DATA
    assert tf[3] == 0x4F          # LBA mid
    assert tf[4] == 0xC2          # LBA high
    assert tf[6] == 0xB0          # SMART
    assert seen[0]["flags"] & pt.ATA_FLAGS_DATA_IN
    assert not seen[0]["flags"] & pt.ATA_FLAGS_DATA_OUT
    assert seen[0]["len"] == 512
    assert s.available is True
    assert s.reallocated == 1
    assert s.source == "ata"


def test_read_ata_smart_drive_abort_is_unavailable(monkeypatch):
    # SMART disabled → drive aborts with ERR set in the status register.
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        _fake_ata(data=make_smart_table({5: 1}), status=0x51))

    s = smart.read_ata_smart(FAKE_HANDLE, "ata")

    assert s.available is False
    assert s.source == "ata"


def test_read_ata_smart_ioctl_failure_is_unavailable(monkeypatch):
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl", _fake_ata(ok=False))

    assert smart.read_ata_smart(FAKE_HANDLE, "ata").available is False


# ── read_ata_smart: SAT bridge ───────────────────────────────────────────

def test_read_ata_smart_via_sat_sends_smart_read_data_cdb(monkeypatch):
    calls = []
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl",
                        make_fake_sptd(data=make_smart_table({5: 0, 197: 8}), capture=calls))

    s = smart.read_ata_smart(FAKE_HANDLE, "sat")

    cdb = calls[0]["cdb"]
    assert (cdb[0], cdb[4], cdb[10], cdb[12], cdb[14]) == (0x85, 0xD0, 0x4F, 0xC2, 0xB0)
    assert s.available is True
    assert s.pending == 8
    assert s.source == "sat"


def test_read_ata_smart_via_sat_rejected_is_unavailable(monkeypatch):
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl", make_fake_sptd(scsi_status=0x02))

    s = smart.read_ata_smart(FAKE_HANDLE, "sat")

    assert s.available is False
    assert s.source == "sat"


def test_read_ata_smart_never_raises(monkeypatch):
    def boom(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(pt.kernel32, "DeviceIoControl", boom)
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl", boom)

    assert smart.read_ata_smart(FAKE_HANDLE, "ata").available is False
    assert smart.read_ata_smart(FAKE_HANDLE, "sat").available is False


def test_read_ata_smart_unknown_transport_sends_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(sat.kernel32, "DeviceIoControl", make_fake_sptd(capture=calls))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl", _fake_ata(seen=calls))

    s = smart.read_ata_smart(FAKE_HANDLE, "nvme")

    assert s.available is False
    assert calls == []
