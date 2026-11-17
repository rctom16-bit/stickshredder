"""NVMe queries: Identify Controller and the SMART / health log. READ-ONLY.

Windows exposes NVMe admin data through ``IOCTL_STORAGE_QUERY_PROPERTY`` with
the protocol-specific properties (Windows 10+, inbox ``stornvme``):

  * Identify Controller (CNS 1)   → model, serial, firmware, SANICAP, FNA
  * Get Log Page, LID 2           → percentage used, media errors

That IOCTL is defined with FILE_ANY_ACCESS, so it works on a handle opened
with desired access 0 (query-only). It is a query: nothing here can change
the drive. Sanitize / Format commands are not part of this module.

NVMe drives behind USB bridges are not reachable this way (the bridge shows
up as a SCSI device); those calls simply return ``None``.

The public readers never raise.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import struct
from dataclasses import dataclass

from core.log import audit_log
from wipe.smart import SmartSummary, smart_unavailable

# ── Constants ────────────────────────────────────────────────────────────

IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400

# STORAGE_PROPERTY_ID
StorageAdapterProtocolSpecificProperty = 49
StorageDeviceProtocolSpecificProperty = 50
# STORAGE_QUERY_TYPE
PropertyStandardQuery = 0
# STORAGE_PROTOCOL_TYPE
ProtocolTypeNvme = 3
# STORAGE_PROTOCOL_NVME_DATA_TYPE
NVMeDataTypeIdentify = 1
NVMeDataTypeLogPage = 2

NVME_IDENTIFY_CNS_CONTROLLER = 1
NVME_LOG_PAGE_HEALTH_INFO = 2

NVME_IDENTIFY_SIZE = 4096
NVME_HEALTH_LOG_SIZE = 512

_QUERY_HEADER = struct.Struct("<II")        # PropertyId, QueryType
_PROTOCOL_DATA = struct.Struct("<10I")      # STORAGE_PROTOCOL_SPECIFIC_DATA (40 bytes)
_DESCRIPTOR_SIZE = 8 + _PROTOCOL_DATA.size  # STORAGE_PROTOCOL_DATA_DESCRIPTOR (48 bytes)

# Identify Controller offsets (NVMe base spec)
_IDC_SN = slice(4, 24)
_IDC_MN = slice(24, 64)
_IDC_FR = slice(64, 72)
_IDC_SANICAP = slice(328, 332)
_IDC_FNA = 524

# Health log offsets
_HEALTH_PERCENTAGE_USED = 5
_HEALTH_MEDIA_ERRORS = slice(160, 176)

# ── kernel32 with explicit prototypes ────────────────────────────────────

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
kernel32.DeviceIoControl.restype = wintypes.BOOL


@dataclass
class NvmeInfo:
    model: str
    serial: str
    firmware: str
    sanitize_crypto: bool
    sanitize_block: bool
    sanitize_overwrite: bool
    format_crypto: bool


# ── Low-level query ──────────────────────────────────────────────────────

def _query_protocol_data(
    handle,
    property_id: int,
    data_type: int,
    request_value: int,
    data_len: int,
) -> bytes | None:
    """One protocol-specific property query. Returns the data or ``None``."""
    try:
        request = _QUERY_HEADER.pack(property_id, PropertyStandardQuery) + _PROTOCOL_DATA.pack(
            ProtocolTypeNvme,
            data_type,
            request_value,
            0,                    # ProtocolDataRequestSubValue
            _PROTOCOL_DATA.size,  # ProtocolDataOffset (from this struct's start)
            data_len,             # ProtocolDataLength
            0, 0, 0, 0,
        )
        size = _QUERY_HEADER.size + _PROTOCOL_DATA.size + data_len
        buf = ctypes.create_string_buffer(request, size)
        bytes_returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle,
            IOCTL_STORAGE_QUERY_PROPERTY,
            ctypes.addressof(buf),
            size,
            ctypes.addressof(buf),
            size,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok:
            err = ctypes.get_last_error()
            audit_log(
                f"NVMe query (property {property_id}, type {data_type}, "
                f"value {request_value}) failed (DeviceIoControl error {err})"
            )
            return None

        raw = buf.raw
        version, desc_size = _QUERY_HEADER.unpack_from(raw, 0)
        if version != _DESCRIPTOR_SIZE or desc_size != _DESCRIPTOR_SIZE:
            return None
        fields = _PROTOCOL_DATA.unpack_from(raw, 8)
        data_offset, returned_len = fields[4], fields[5]
        if data_offset < _PROTOCOL_DATA.size or returned_len < data_len:
            return None
        start = 8 + data_offset
        if start + data_len > len(raw):
            return None
        return raw[start:start + data_len]
    except Exception as exc:  # noqa: BLE001 — never raise
        audit_log(f"NVMe query raised: {exc}")
        return None


def _query_either(handle, first: int, second: int, data_type: int,
                  request_value: int, data_len: int) -> bytes | None:
    data = _query_protocol_data(handle, first, data_type, request_value, data_len)
    if data is None:
        data = _query_protocol_data(handle, second, data_type, request_value, data_len)
    return data


# ── Parsers ──────────────────────────────────────────────────────────────

def _ascii_field(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").replace("\x00", " ").strip()


def parse_identify_controller(data: bytes) -> NvmeInfo:
    """Parse an Identify Controller data structure. Raises ``ValueError``
    on a buffer too short to hold the fields we read."""
    if data is None or len(data) <= _IDC_FNA:
        raise ValueError("Identify Controller data too short")
    sanicap = int.from_bytes(data[_IDC_SANICAP], "little")
    fna = data[_IDC_FNA]
    return NvmeInfo(
        model=_ascii_field(data[_IDC_MN]),
        serial=_ascii_field(data[_IDC_SN]),
        firmware=_ascii_field(data[_IDC_FR]),
        sanitize_crypto=bool(sanicap & 0x1),
        sanitize_block=bool(sanicap & 0x2),
        sanitize_overwrite=bool(sanicap & 0x4),
        format_crypto=bool(fna & 0x4),
    )


def parse_nvme_health(data: bytes) -> SmartSummary:
    """Summarise the SMART / Health Information log (LID 2)."""
    if data is None or len(data) < _HEALTH_MEDIA_ERRORS.stop:
        return smart_unavailable("nvme")
    return SmartSummary(
        available=True,
        reallocated=None,
        pending=None,
        uncorrectable=None,
        media_errors=int.from_bytes(data[_HEALTH_MEDIA_ERRORS], "little"),
        percentage_used=data[_HEALTH_PERCENTAGE_USED],
        source="nvme",
    )


# ── Public readers ───────────────────────────────────────────────────────

def read_nvme_identify(handle) -> NvmeInfo | None:
    """Identify Controller via the adapter property (device property as fallback)."""
    try:
        data = _query_either(
            handle,
            StorageAdapterProtocolSpecificProperty,
            StorageDeviceProtocolSpecificProperty,
            NVMeDataTypeIdentify,
            NVME_IDENTIFY_CNS_CONTROLLER,
            NVME_IDENTIFY_SIZE,
        )
        if data is None:
            return None
        info = parse_identify_controller(data)
        if not info.model and not info.serial:
            return None
        audit_log(
            f"NVMe IDENTIFY: model={info.model!r} serial={info.serial!r} "
            f"fw={info.firmware!r} sanitize(crypto={info.sanitize_crypto},"
            f"block={info.sanitize_block},overwrite={info.sanitize_overwrite}) "
            f"format_crypto={info.format_crypto}"
        )
        return info
    except Exception as exc:  # noqa: BLE001 — never raise
        audit_log(f"read_nvme_identify raised: {exc}")
        return None


def read_nvme_health(handle) -> SmartSummary:
    """SMART / health log via the device property (adapter property as fallback)."""
    try:
        data = _query_either(
            handle,
            StorageDeviceProtocolSpecificProperty,
            StorageAdapterProtocolSpecificProperty,
            NVMeDataTypeLogPage,
            NVME_LOG_PAGE_HEALTH_INFO,
            NVME_HEALTH_LOG_SIZE,
        )
        summary = parse_nvme_health(data)
        if summary.available:
            audit_log(
                f"NVMe health: percentage_used={summary.percentage_used} "
                f"media_errors={summary.media_errors}"
            )
        return summary
    except Exception as exc:  # noqa: BLE001 — never raise
        audit_log(f"read_nvme_health raised: {exc}")
        return smart_unavailable("nvme")
