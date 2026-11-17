"""Tests for wipe.passthrough — ATA pass-through foundation (mocked IOCTL layer).

No hardware is touched: kernel32.DeviceIoControl is monkeypatched with a fake
that fills a 512-byte IDENTIFY buffer / sets output task-file registers and
reports success or failure. These tests pin the public API that
wipe/hidden_areas.py and wipe/secure_erase.py import verbatim.
"""

import ctypes
import struct

import pytest

import wipe.passthrough as pt


# ── Fake IDENTIFY data builders ──────────────────────────────────────────

def _encode_ata_string(text: str, nwords: int) -> list[int]:
    """Encode an ASCII string the way ATA stores it: byte-swapped per word.

    Inverse of the module's parser, so a round-trip proves correct decoding.
    """
    padded = text.ljust(nwords * 2)[: nwords * 2]
    words = []
    for i in range(nwords):
        hi = ord(padded[2 * i])
        lo = ord(padded[2 * i + 1])
        words.append((hi << 8) | lo)
    return words


def make_identify_words(
    *,
    model: str = "ACME SSD 256GB",
    serial: str = "SN0123456789",
    firmware: str = "FW1.00",
    lba48_max: int = 0,
    word82: int = 0,
    word85: int = 0,
    word89: int = 0,
    word128: int = 0,
) -> list[int]:
    """Build a 256-word IDENTIFY image with the fields we parse."""
    words = [0] * 256

    for offset, w in enumerate(_encode_ata_string(serial, 10)):
        words[10 + offset] = w
    for offset, w in enumerate(_encode_ata_string(firmware, 4)):
        words[23 + offset] = w
    for offset, w in enumerate(_encode_ata_string(model, 20)):
        words[27 + offset] = w

    words[82] = word82
    words[85] = word85
    words[89] = word89
    words[100] = lba48_max & 0xFFFF
    words[101] = (lba48_max >> 16) & 0xFFFF
    words[102] = (lba48_max >> 32) & 0xFFFF
    words[103] = (lba48_max >> 48) & 0xFFFF
    words[128] = word128
    return words


def words_to_bytes(words: list[int]) -> bytes:
    return b"".join(struct.pack("<H", w & 0xFFFF) for w in words)


# ── Fake DeviceIoControl factory ─────────────────────────────────────────

def make_fake_dioctl(*, data: bytes | None = None, cur=None, prev=None,
                     ret: int = 1, capture: dict | None = None):
    """Return a stand-in for kernel32.DeviceIoControl.

    The module calls it with integer addresses for the in/out buffers (so the
    fake can rebuild the struct), writes ``data`` into the struct's DataBuffer,
    and copies ``cur``/``prev`` into the output task-file registers.
    """

    def fake(handle, ioctl, in_addr, in_size, out_addr, out_size, bytesret, overlapped):
        if not ret:
            ctypes.set_last_error(5)  # ERROR_ACCESS_DENIED-ish
            return 0
        aptd = pt.ATA_PASS_THROUGH_DIRECT.from_address(in_addr)
        if capture is not None:
            capture["command"] = aptd.CurrentTaskFile[6]
            capture["ata_flags"] = aptd.AtaFlags
            if aptd.DataBuffer:
                capture["data_out"] = ctypes.string_at(
                    aptd.DataBuffer, aptd.DataTransferLength
                )
        if data is not None and aptd.DataBuffer:
            ctypes.memmove(aptd.DataBuffer, bytes(data), len(data))
        if cur is not None:
            for i, v in enumerate(cur):
                aptd.CurrentTaskFile[i] = v
        if prev is not None:
            for i, v in enumerate(prev):
                aptd.PreviousTaskFile[i] = v
        return 1

    return fake


FAKE_HANDLE = 0x1234


# ── ata_identify: parsing ────────────────────────────────────────────────

def test_identify_parses_model_serial_firmware(monkeypatch):
    data = words_to_bytes(make_identify_words(
        model="Samsung SSD 870", serial="S5Y2NJ0R", firmware="SVT02B6Q"))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert info is not None
    assert info.model == "Samsung SSD 870"
    assert info.serial == "S5Y2NJ0R"
    assert info.firmware == "SVT02B6Q"


def test_identify_parses_lba48_max(monkeypatch):
    data = words_to_bytes(make_identify_words(lba48_max=0x1D1C5970))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert info.lba48_max_sectors == 0x1D1C5970


def test_identify_lba48_max_full_64bit(monkeypatch):
    data = words_to_bytes(make_identify_words(lba48_max=0x0000_5566_7788_99AA))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert info.lba48_max_sectors == 0x0000_5566_7788_99AA


def test_identify_security_supported_and_enabled(monkeypatch):
    data = words_to_bytes(make_identify_words(
        word82=0x0002, word85=0x0002, word128=0x0003))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert info.security_supported is True
    assert info.security_enabled is True
    assert info.security_locked is False
    assert info.security_frozen is False


def test_identify_security_locked_and_frozen(monkeypatch):
    # word128: supported|enabled|locked|frozen = 0x0F
    data = words_to_bytes(make_identify_words(word82=0x0002, word128=0x000F))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert info.security_locked is True
    assert info.security_frozen is True


def test_identify_enhanced_erase_supported(monkeypatch):
    data = words_to_bytes(make_identify_words(word82=0x0002, word128=0x0021))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert info.enhanced_erase_supported is True


def test_identify_enhanced_erase_defaults_false(monkeypatch):
    data = words_to_bytes(make_identify_words(word82=0x0002, word128=0x0001))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert info.enhanced_erase_supported is False


def test_identify_erase_unit_minutes(monkeypatch):
    data = words_to_bytes(make_identify_words(word89=15))
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert info.erase_unit_minutes == 30


def test_identify_raw_words_length(monkeypatch):
    data = words_to_bytes(make_identify_words())
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=data))

    info = pt.ata_identify(FAKE_HANDLE)

    assert isinstance(info.raw_words, tuple)
    assert len(info.raw_words) == 256


# ── ata_identify: error paths (USB bridges reject 0xEC → None) ────────────

def test_identify_returns_none_on_ioctl_failure(monkeypatch):
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(ret=0))

    assert pt.ata_identify(FAKE_HANDLE) is None


def test_identify_returns_none_on_exception(monkeypatch):
    def boom(*a, **k):
        raise OSError("simulated driver explosion")

    monkeypatch.setattr(pt.kernel32, "DeviceIoControl", boom)

    assert pt.ata_identify(FAKE_HANDLE) is None


# ── send_ata_command ─────────────────────────────────────────────────────

def test_send_ata_command_success_no_error(monkeypatch):
    # status 0x50 = DRDY|DSC, ERR bit clear
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(cur=[0, 0, 0, 0, 0, 0, 0x50, 0]))

    res = pt.send_ata_command(FAKE_HANDLE, command=0xEC)

    assert res is not None
    assert res.success is True
    assert res.ata_status == 0x50
    assert res.ata_error == 0


def test_send_ata_command_reports_ata_error(monkeypatch):
    # status 0x51 = DRDY|ERR ; error 0x04 = ABRT
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(cur=[0x04, 0, 0, 0, 0, 0, 0x51, 0]))

    res = pt.send_ata_command(FAKE_HANDLE, command=0xEC)

    assert res is not None
    assert res.success is False
    assert res.ata_status == 0x51
    assert res.ata_error == 0x04


def test_send_ata_command_data_in(monkeypatch):
    payload = bytes(range(256)) * 2  # 512 bytes
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=payload,
                                         cur=[0, 0, 0, 0, 0, 0, 0x50, 0]))

    res = pt.send_ata_command(FAKE_HANDLE, command=0xEC, data_in_len=512)

    assert res.success is True
    assert res.data == payload


def test_send_ata_command_data_out_is_copied(monkeypatch):
    capture: dict = {}
    payload = b"\xAA" * 512
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(cur=[0, 0, 0, 0, 0, 0, 0x50, 0],
                                         capture=capture))

    res = pt.send_ata_command(FAKE_HANDLE, command=0xF1, data_out=payload)

    assert res.success is True
    assert capture["data_out"] == payload
    assert capture["ata_flags"] & pt.ATA_FLAGS_DATA_OUT


def test_send_ata_command_sets_48bit_flag(monkeypatch):
    capture: dict = {}
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(cur=[0, 0, 0, 0, 0, 0, 0x50, 0],
                                         capture=capture))

    pt.send_ata_command(FAKE_HANDLE, command=0x27, use_lba48=True,
                        data_in_len=0)

    assert capture["ata_flags"] & pt.ATA_FLAGS_48BIT
    assert capture["command"] == 0x27


def test_send_ata_command_returns_none_on_ioctl_failure(monkeypatch):
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(ret=0))

    assert pt.send_ata_command(FAKE_HANDLE, command=0xEC) is None


def test_send_ata_command_returns_none_on_exception(monkeypatch):
    def boom(*a, **k):
        raise ValueError("kaboom")

    monkeypatch.setattr(pt.kernel32, "DeviceIoControl", boom)

    assert pt.send_ata_command(FAKE_HANDLE, command=0xEC) is None


# ── read_native_max_address (HPA) ────────────────────────────────────────

def test_read_native_max_address_parses_48bit_lba(monkeypatch):
    # native max LBA = 0x1234567890 → sectors = that + 1
    cur = [0, 0, 0x90, 0x78, 0x56, 0x40, 0x50, 0]   # LBALow/Mid/High bits 0..23
    prev = [0, 0, 0x34, 0x12, 0x00, 0, 0, 0]        # bits 24..47
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(cur=cur, prev=prev))

    native = pt.read_native_max_address(FAKE_HANDLE)

    assert native == 0x1234567890 + 1


def test_read_native_max_address_none_on_ata_error(monkeypatch):
    # ERR bit set in status → command failed
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(cur=[0x04, 0, 0, 0, 0, 0, 0x51, 0]))

    assert pt.read_native_max_address(FAKE_HANDLE) is None


def test_read_native_max_address_none_on_ioctl_failure(monkeypatch):
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(ret=0))

    assert pt.read_native_max_address(FAKE_HANDLE) is None


# ── device_configuration_identify_max (DCO) ──────────────────────────────

def _dco_data(max_sectors: int) -> bytes:
    words = [0] * 256
    words[0] = 0x0002  # revision
    words[3] = max_sectors & 0xFFFF
    words[4] = (max_sectors >> 16) & 0xFFFF
    words[5] = (max_sectors >> 32) & 0xFFFF
    words[6] = (max_sectors >> 48) & 0xFFFF
    return words_to_bytes(words)


def test_dco_identify_max_parses_max_sectors(monkeypatch):
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=_dco_data(0x9876543210),
                                         cur=[0, 0, 0, 0, 0, 0, 0x50, 0]))

    dco = pt.device_configuration_identify_max(FAKE_HANDLE)

    assert dco == 0x9876543210


def test_dco_identify_max_none_when_rejected(monkeypatch):
    # ERR bit → command aborted (common: DCO not supported)
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(data=_dco_data(0),
                                         cur=[0x04, 0, 0, 0, 0, 0, 0x51, 0]))

    assert pt.device_configuration_identify_max(FAKE_HANDLE) is None


def test_dco_identify_max_none_on_ioctl_failure(monkeypatch):
    monkeypatch.setattr(pt.kernel32, "DeviceIoControl",
                        make_fake_dioctl(ret=0))

    assert pt.device_configuration_identify_max(FAKE_HANDLE) is None


# ── struct layout sanity (64-bit pointer alignment) ──────────────────────

def test_ata_pass_through_direct_is_64bit_safe():
    size = ctypes.sizeof(pt.ATA_PASS_THROUGH_DIRECT)
    # On 64-bit Windows the PVOID forces 8-byte alignment → 48-byte struct.
    assert size == 48
