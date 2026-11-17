"""Tests for wipe.nvme — NVMe Identify Controller and SMART/health log.

The queries go through ``IOCTL_STORAGE_QUERY_PROPERTY`` (protocol-specific
properties), which Windows answers on a query-only handle. A fake
``DeviceIoControl`` decodes the request and writes a
STORAGE_PROTOCOL_DATA_DESCRIPTOR followed by the data, like stornvme does.
"""

from __future__ import annotations

import ctypes
import struct

import pytest

import wipe.nvme as nvme

FAKE_HANDLE = 0x5A9


@pytest.fixture(autouse=True)
def _silence_audit_log(monkeypatch):
    monkeypatch.setattr(nvme, "audit_log", lambda msg: None)


# ── Data builders ────────────────────────────────────────────────────────

def make_identify_controller(
    *,
    model: str = "SKHynix_HFS001TEJ9X115N",
    serial: str = "ASD7N123412340P2D",
    firmware: str = "51000A20",
    sanicap: int = 0,
    fna: int = 0,
) -> bytes:
    raw = bytearray(4096)
    raw[0:2] = (0x1C5C).to_bytes(2, "little")  # PCI vendor id
    raw[4:24] = serial.encode("ascii").ljust(20)
    raw[24:64] = model.encode("ascii").ljust(40)
    raw[64:72] = firmware.encode("ascii").ljust(8)
    raw[328:332] = sanicap.to_bytes(4, "little")
    raw[524] = fna
    return bytes(raw)


def make_health_log(*, percentage_used: int = 3, media_errors: int = 0) -> bytes:
    raw = bytearray(512)
    raw[1:3] = (310).to_bytes(2, "little")  # composite temperature, Kelvin
    raw[3] = 100                             # available spare
    raw[5] = percentage_used
    raw[160:176] = media_errors.to_bytes(16, "little")
    return bytes(raw)


# ── Fake IOCTL_STORAGE_QUERY_PROPERTY ────────────────────────────────────

def make_fake_query(responses: dict, *, calls: list | None = None,
                    descriptor_version: int | None = None,
                    returned: int | None = None, data_offset: int = 40):
    """``responses`` maps (property_id, data_type, request_value) → bytes.

    ``returned`` overrides the byte count reported back; ``data_offset`` the
    ProtocolDataOffset the "driver" writes into its answer.
    """

    def fake(handle, ioctl, in_addr, in_size, out_addr, out_size, bytesret, overlapped):
        prop_id, query_type = struct.unpack("<II", ctypes.string_at(in_addr, 8))
        fields = struct.unpack("<10I", ctypes.string_at(in_addr + 8, 40))
        (ptype, dtype, req_value, req_sub, data_off, data_len, *_rest) = fields
        if calls is not None:
            calls.append({
                "handle": handle,
                "ioctl": ioctl,
                "property_id": prop_id,
                "query_type": query_type,
                "protocol_type": ptype,
                "data_type": dtype,
                "request_value": req_value,
                "request_sub_value": req_sub,
                "data_offset": data_off,
                "data_length": data_len,
                "in_size": in_size,
                "out_size": out_size,
            })
        payload = responses.get((prop_id, dtype, req_value))
        if ioctl != nvme.IOCTL_STORAGE_QUERY_PROPERTY or payload is None:
            ctypes.set_last_error(50)  # ERROR_NOT_SUPPORTED
            return 0
        version = 48 if descriptor_version is None else descriptor_version
        header = struct.pack("<II", version, 48)
        protocol = struct.pack("<10I", ptype, dtype, req_value, req_sub, data_offset,
                               len(payload), 0, 0, 0, 0)
        out = header + protocol + payload
        ctypes.memmove(out_addr, out, min(len(out), out_size))
        bytesret._obj.value = min(len(out), out_size) if returned is None else returned
        return 1

    return fake


ADAPTER, DEVICE = 49, 50
IDENTIFY, LOG_PAGE = 1, 2


# ── parse_identify_controller ────────────────────────────────────────────

def test_parse_identify_controller_strings():
    info = nvme.parse_identify_controller(make_identify_controller())

    assert info.model == "SKHynix_HFS001TEJ9X115N"
    assert info.serial == "ASD7N123412340P2D"
    assert info.firmware == "51000A20"


@pytest.mark.parametrize("sanicap, crypto, block, overwrite", [
    (0b000, False, False, False),
    (0b001, True, False, False),
    (0b010, False, True, False),
    (0b100, False, False, True),
    (0b011, True, True, False),
])
def test_parse_identify_controller_sanicap_bits(sanicap, crypto, block, overwrite):
    info = nvme.parse_identify_controller(make_identify_controller(sanicap=sanicap))

    assert info.sanitize_crypto is crypto
    assert info.sanitize_block is block
    assert info.sanitize_overwrite is overwrite


def test_parse_identify_controller_reads_sanicap_as_little_endian_dword():
    # Upper SANICAP bits (e.g. NDI, bit 29) must not leak into the low bits.
    info = nvme.parse_identify_controller(make_identify_controller(sanicap=0x2000_0002))

    assert (info.sanitize_crypto, info.sanitize_block) == (False, True)


@pytest.mark.parametrize("fna, expected", [(0x00, False), (0x04, True), (0x03, False), (0x07, True)])
def test_parse_identify_controller_crypto_format_from_fna_bit2(fna, expected):
    assert nvme.parse_identify_controller(make_identify_controller(fna=fna)).format_crypto is expected


def test_parse_identify_controller_rejects_short_buffer():
    with pytest.raises(ValueError):
        nvme.parse_identify_controller(bytes(200))


# ── read_nvme_identify ───────────────────────────────────────────────────

def test_read_nvme_identify_builds_adapter_protocol_query(monkeypatch):
    calls = []
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(ADAPTER, IDENTIFY, 1): make_identify_controller(sanicap=0b011, fna=0x04)},
        calls=calls))

    info = nvme.read_nvme_identify(FAKE_HANDLE)

    assert info is not None
    assert info.model == "SKHynix_HFS001TEJ9X115N"
    assert info.sanitize_crypto and info.sanitize_block and info.format_crypto
    c = calls[0]
    assert c["ioctl"] == nvme.IOCTL_STORAGE_QUERY_PROPERTY == 0x2D1400
    assert c["handle"] == FAKE_HANDLE
    assert c["property_id"] == ADAPTER
    assert c["query_type"] == 0           # PropertyStandardQuery
    assert c["protocol_type"] == 3        # ProtocolTypeNvme
    assert c["data_type"] == IDENTIFY     # NVMeDataTypeIdentify
    assert c["request_value"] == 1        # CNS 1 = controller
    assert c["request_sub_value"] == 0
    assert c["data_offset"] == 40         # sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA)
    assert c["data_length"] == 4096
    # Microsoft's sample size exactly: query header + protocol data + data.
    assert c["in_size"] == c["out_size"] == 8 + 40 + 4096
    assert len(calls) == 1


def test_read_nvme_identify_never_retries_with_another_property(monkeypatch):
    # No trial and error: Identify Controller goes to the adapter property
    # only; a refusal is final.
    calls = []
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(DEVICE, IDENTIFY, 1): make_identify_controller()}, calls=calls))

    assert nvme.read_nvme_identify(FAKE_HANDLE) is None
    assert [c["property_id"] for c in calls] == [ADAPTER]


def test_read_nvme_identify_none_when_fewer_bytes_than_the_data_arrive(monkeypatch):
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(ADAPTER, IDENTIFY, 1): make_identify_controller()}, returned=48 + 100))

    assert nvme.read_nvme_identify(FAKE_HANDLE) is None


@pytest.mark.parametrize("offset", [0, 39, 41, 0xFFFF_FFF0])
def test_read_nvme_identify_none_when_answer_puts_data_elsewhere(monkeypatch, offset):
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(ADAPTER, IDENTIFY, 1): make_identify_controller()}, data_offset=offset))

    assert nvme.read_nvme_identify(FAKE_HANDLE) is None


def test_read_nvme_identify_none_when_not_nvme(monkeypatch):
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query({}))

    assert nvme.read_nvme_identify(FAKE_HANDLE) is None


def test_read_nvme_identify_none_on_bad_descriptor(monkeypatch):
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(ADAPTER, IDENTIFY, 1): make_identify_controller(),
         (DEVICE, IDENTIFY, 1): make_identify_controller()},
        descriptor_version=12))

    assert nvme.read_nvme_identify(FAKE_HANDLE) is None


def test_read_nvme_identify_none_on_empty_identify(monkeypatch):
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(ADAPTER, IDENTIFY, 1): bytes(4096), (DEVICE, IDENTIFY, 1): bytes(4096)}))

    assert nvme.read_nvme_identify(FAKE_HANDLE) is None


def test_read_nvme_identify_never_raises(monkeypatch):
    def boom(*args):
        raise OSError("boom")

    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", boom)

    assert nvme.read_nvme_identify(FAKE_HANDLE) is None


# ── Health log ───────────────────────────────────────────────────────────

def test_parse_nvme_health():
    s = nvme.parse_nvme_health(make_health_log(percentage_used=7, media_errors=2**70 + 5))

    assert s.available is True
    assert s.percentage_used == 7
    assert s.media_errors == 2**70 + 5
    assert (s.reallocated, s.pending, s.uncorrectable) == (None, None, None)
    assert s.source == "nvme"


def test_parse_nvme_health_short_buffer_is_unavailable():
    assert nvme.parse_nvme_health(bytes(100)).available is False


def test_read_nvme_health_builds_device_log_page_query(monkeypatch):
    calls = []
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(DEVICE, LOG_PAGE, 2): make_health_log(percentage_used=4, media_errors=1)},
        calls=calls))

    s = nvme.read_nvme_health(FAKE_HANDLE)

    assert s.available is True
    assert (s.percentage_used, s.media_errors) == (4, 1)
    c = calls[0]
    assert c["property_id"] == DEVICE
    assert c["protocol_type"] == 3
    assert c["data_type"] == LOG_PAGE      # NVMeDataTypeLogPage
    assert c["request_value"] == 2         # LID 2 = SMART / health information
    assert c["data_length"] == 512


def test_read_nvme_health_never_retries_with_another_property(monkeypatch):
    calls = []
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(ADAPTER, LOG_PAGE, 2): make_health_log()}, calls=calls))

    assert nvme.read_nvme_health(FAKE_HANDLE).available is False
    assert [c["property_id"] for c in calls] == [DEVICE]


def test_read_nvme_health_request_has_exact_size(monkeypatch):
    calls = []
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query(
        {(DEVICE, LOG_PAGE, 2): make_health_log()}, calls=calls))

    nvme.read_nvme_health(FAKE_HANDLE)

    assert calls[0]["in_size"] == calls[0]["out_size"] == 8 + 40 + 512
    assert calls[0]["data_offset"] == 40


def test_read_nvme_health_unavailable_when_not_supported(monkeypatch):
    monkeypatch.setattr(nvme.kernel32, "DeviceIoControl", make_fake_query({}))

    s = nvme.read_nvme_health(FAKE_HANDLE)

    assert s.available is False
    assert s.source == "nvme"
