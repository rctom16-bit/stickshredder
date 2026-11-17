"""NVMe queries: Identify Controller and the SMART / health log. READ-ONLY.

Windows exposes NVMe admin data through ``IOCTL_STORAGE_QUERY_PROPERTY`` with
the protocol-specific properties (Windows 10+, inbox ``stornvme``), each
request built exactly like Microsoft's "Working with NVMe drives" samples:

  * Identify Controller (CNS 1)  StorageAdapterProtocolSpecificProperty
                                 → model, serial, firmware, SANICAP, FNA
  * Get Log Page, LID 2          StorageDeviceProtocolSpecificProperty
                                 → percentage used, media errors

There is no fallback to the other property: a refusal is final. Only call
these readers for a disk ``wipe.transport`` chose ``"nvme"`` for (NVMe bus on
Microsoft's stornvme driver, not a system disk) — on 17.11.2026 the same
queries sent by trial and error to a USB stick and to a system NVMe behind
Intel RST VMD crashed Windows twice.

That IOCTL is defined with FILE_ANY_ACCESS, so it works on a handle opened
with desired access 0 (query-only). It is a query: nothing here can change
the drive. Sanitize / Format commands are not part of this module.

The public readers never raise.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from dataclasses import dataclass

from core.log import audit_log
from wipe.smart import SmartSummary, smart_unavailable
from wipe.transport import (
    IOCTL_STORAGE_QUERY_PROPERTY,
    STORAGE_PROPERTY_QUERY,
    PropertyStandardQuery,
    StorageAdapterProtocolSpecificProperty,
    StorageDeviceProtocolSpecificProperty,
)

# ── Constants (Windows SDK; pinned by tests/test_struct_layout.py) ────────

# STORAGE_PROTOCOL_TYPE
ProtocolTypeNvme = 3
# STORAGE_PROTOCOL_NVME_DATA_TYPE
NVMeDataTypeIdentify = 1
NVMeDataTypeLogPage = 2

NVME_IDENTIFY_CNS_CONTROLLER = 1
NVME_LOG_PAGE_HEALTH_INFO = 2

NVME_IDENTIFY_SIZE = 4096
NVME_HEALTH_LOG_SIZE = 512


class STORAGE_PROTOCOL_SPECIFIC_DATA(ctypes.Structure):
    _fields_ = [
        ("ProtocolType", ctypes.c_uint32),
        ("DataType", ctypes.c_uint32),
        ("ProtocolDataRequestValue", ctypes.c_uint32),
        ("ProtocolDataRequestSubValue", ctypes.c_uint32),
        ("ProtocolDataOffset", ctypes.c_uint32),
        ("ProtocolDataLength", ctypes.c_uint32),
        ("FixedProtocolReturnData", ctypes.c_uint32),
        ("ProtocolDataRequestSubValue2", ctypes.c_uint32),
        ("ProtocolDataRequestSubValue3", ctypes.c_uint32),
        ("ProtocolDataRequestSubValue4", ctypes.c_uint32),
    ]


class STORAGE_PROTOCOL_DATA_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("Size", ctypes.c_uint32),
        ("ProtocolSpecificData", STORAGE_PROTOCOL_SPECIFIC_DATA),
    ]


# The request is STORAGE_PROPERTY_QUERY with STORAGE_PROTOCOL_SPECIFIC_DATA in
# its AdditionalParameters, followed by room for the data. The driver answers
# in the same buffer with STORAGE_PROTOCOL_DATA_DESCRIPTOR (an equally long
# 8-byte header) and the data at ProtocolDataOffset, counted from the start
# of the protocol-specific data.
_PROTOCOL_DATA_AT = STORAGE_PROPERTY_QUERY.AdditionalParameters.offset
_ANSWER_DATA_BASE = STORAGE_PROTOCOL_DATA_DESCRIPTOR.ProtocolSpecificData.offset


def request_size(data_len: int) -> int:
    """Bytes of the in/out buffer for a query returning ``data_len`` bytes."""
    return _PROTOCOL_DATA_AT + ctypes.sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA) + data_len


# Identify Controller offsets (NVMe base spec; pinned by test_struct_layout)
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
        size = request_size(data_len)
        buf = (ctypes.c_ubyte * size)()
        query = STORAGE_PROPERTY_QUERY.from_buffer(buf)
        query.PropertyId = property_id
        query.QueryType = PropertyStandardQuery
        proto = STORAGE_PROTOCOL_SPECIFIC_DATA.from_buffer(buf, _PROTOCOL_DATA_AT)
        proto.ProtocolType = ProtocolTypeNvme
        proto.DataType = data_type
        proto.ProtocolDataRequestValue = request_value
        proto.ProtocolDataRequestSubValue = 0
        proto.ProtocolDataOffset = ctypes.sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA)
        proto.ProtocolDataLength = data_len

        bytes_returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle,
            IOCTL_STORAGE_QUERY_PROPERTY,
            ctypes.addressof(buf),
            ctypes.sizeof(buf),
            ctypes.addressof(buf),
            ctypes.sizeof(buf),
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

        answer = STORAGE_PROTOCOL_DATA_DESCRIPTOR.from_buffer_copy(buf)
        expected = ctypes.sizeof(STORAGE_PROTOCOL_DATA_DESCRIPTOR)
        if answer.Version != expected or answer.Size != expected:
            return None
        data = answer.ProtocolSpecificData
        if (data.ProtocolDataOffset < ctypes.sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA)
                or data.ProtocolDataLength < data_len):
            return None
        start = _ANSWER_DATA_BASE + data.ProtocolDataOffset
        if start + data_len > min(bytes_returned.value, size):
            return None
        return ctypes.string_at(ctypes.addressof(buf) + start, data_len)
    except Exception as exc:  # noqa: BLE001 — never raise
        audit_log(f"NVMe query raised: {exc}")
        return None


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
    """Identify Controller via the adapter property (no other property is tried)."""
    try:
        data = _query_protocol_data(
            handle,
            StorageAdapterProtocolSpecificProperty,
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
    """SMART / health log via the device property (no other property is tried)."""
    try:
        data = _query_protocol_data(
            handle,
            StorageDeviceProtocolSpecificProperty,
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
