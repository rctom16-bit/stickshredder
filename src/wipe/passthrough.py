"""ATA pass-through foundation for StickShredder.

Thin, defensive wrapper over ``IOCTL_ATA_PASS_THROUGH_DIRECT`` that talks to a
drive's firmware. Two sibling modules build on this:

  * ``wipe/hidden_areas.py`` — HPA/DCO detection (report-only).
  * ``wipe/secure_erase.py`` — ATA Secure Erase (experimental, opt-in).

Design rules (mirrors ``wipe/device.py``):
  * ``kernel32`` is loaded with ``use_last_error=True`` and every function used
    gets explicit ``argtypes``/``restype`` so 64-bit values are never truncated.
  * The public surface NEVER raises. DeviceIoControl + parsing run inside
    try/except; on any failure the functions return ``None``. Many USB-to-SATA
    bridges reject ATA pass-through (``IDENTIFY`` aborts) — that is normal and
    simply yields ``None``, letting callers degrade gracefully.

No real hardware is required to test this module: tests patch
``kernel32.DeviceIoControl`` with a fake that fills the data buffer / output
task-file registers.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import struct
from dataclasses import dataclass

from core.log import audit_log

# ── Win32 / ATA constants ────────────────────────────────────────────────

IOCTL_ATA_PASS_THROUGH_DIRECT = 0x0004D030

# ATA_PASS_THROUGH_DIRECT.AtaFlags
ATA_FLAGS_DRDY_REQUIRED = 0x01
ATA_FLAGS_DATA_IN = 0x02
ATA_FLAGS_DATA_OUT = 0x04
ATA_FLAGS_48BIT = 0x08

# Status register bits
ATA_STATUS_ERR = 0x01

# ATA command opcodes
ATA_CMD_IDENTIFY_DEVICE = 0xEC
ATA_CMD_READ_NATIVE_MAX_ADDRESS_EXT = 0x27
ATA_CMD_DEVICE_CONFIGURATION = 0xB1
ATA_FEATURE_DEVICE_CONFIGURATION_IDENTIFY = 0xC2

# Device register: bit6 selects LBA addressing mode.
ATA_DEVICE_LBA = 0x40

IDENTIFY_BUFFER_LEN = 512  # 256 little-endian USHORT words

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


class ATA_PASS_THROUGH_DIRECT(ctypes.Structure):
    """Mirror of the Win32 ``ATA_PASS_THROUGH_DIRECT`` structure.

    On 64-bit Windows the ``PVOID DataBuffer`` field forces 8-byte alignment,
    so ctypes inserts 4 bytes of padding after ``ReservedAsUlong`` and the
    whole structure is 48 bytes. Do NOT add ``_pack_`` — the natural alignment
    is exactly what the driver expects.
    """

    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("AtaFlags", ctypes.c_ushort),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("ReservedAsUchar", ctypes.c_ubyte),
        ("DataTransferLength", ctypes.c_ulong),
        ("TimeOutValue", ctypes.c_ulong),
        ("ReservedAsUlong", ctypes.c_ulong),
        ("DataBuffer", ctypes.c_void_p),
        ("PreviousTaskFile", ctypes.c_ubyte * 8),
        ("CurrentTaskFile", ctypes.c_ubyte * 8),
    ]


# Task-file register indices.
#   On INPUT:  Features, SectorCount, LBALow, LBAMid, LBAHigh, Device, Command, Reserved
#   On OUTPUT: Error,    SectorCount, LBALow, LBAMid, LBAHigh, Device, Status,  Reserved
_TF_FEATURE = 0
_TF_ERROR = 0
_TF_SECTOR_COUNT = 1
_TF_LBA_LOW = 2
_TF_LBA_MID = 3
_TF_LBA_HIGH = 4
_TF_DEVICE = 5
_TF_COMMAND = 6
_TF_STATUS = 6


# ── Data model ───────────────────────────────────────────────────────────

@dataclass
class IdentifyData:
    model: str
    serial: str
    firmware: str
    lba48_max_sectors: int            # user-accessible max (LBA48), 0 if unknown
    security_supported: bool
    security_enabled: bool
    security_locked: bool
    security_frozen: bool
    enhanced_erase_supported: bool
    erase_unit_minutes: int           # estimated SECURITY ERASE UNIT time, 0 if unknown
    raw_words: tuple                  # the 256 IDENTIFY words (for special cases)


@dataclass
class AtaCommandResult:
    success: bool                     # IOCTL succeeded AND ATA ERR bit clear
    ata_status: int                  # output Status register
    ata_error: int                   # output Error register
    data: bytes                      # data-in payload (empty for non-data commands)


# ── Internal raw execution ───────────────────────────────────────────────

class _RawResult:
    __slots__ = ("status", "error", "current", "previous", "data")

    def __init__(self, status, error, current, previous, data):
        self.status = status
        self.error = error
        self.current = current      # bytes(8) — output CurrentTaskFile
        self.previous = previous    # bytes(8) — output PreviousTaskFile
        self.data = data            # bytes — data-in payload


def _execute_aptd(
    handle: int,
    *,
    command: int,
    feature: int = 0,
    lba: int = 0,
    sector_count: int = 0,
    data_out: bytes | None = None,
    data_in_len: int = 0,
    use_lba48: bool = False,
    timeout_seconds: int = 30,
) -> _RawResult | None:
    """Issue one ATA pass-through command. Returns ``None`` on any failure.

    Returns a ``_RawResult`` exposing the full output task-file registers so
    higher-level helpers (HPA/DCO) can read the returned LBA. The public
    ``send_ata_command`` wraps this into an ``AtaCommandResult``.
    """
    try:
        aptd = ATA_PASS_THROUGH_DIRECT()
        aptd.Length = ctypes.sizeof(ATA_PASS_THROUGH_DIRECT)

        flags = ATA_FLAGS_DRDY_REQUIRED
        if use_lba48:
            flags |= ATA_FLAGS_48BIT

        # Allocate the separate data buffer the struct points at. Keep a Python
        # reference (``data_buf``) alive until after the call returns.
        data_buf = None
        if data_out is not None:
            flags |= ATA_FLAGS_DATA_OUT
            data_buf = (ctypes.c_ubyte * len(data_out)).from_buffer_copy(data_out)
            aptd.DataTransferLength = len(data_out)
        elif data_in_len > 0:
            flags |= ATA_FLAGS_DATA_IN
            data_buf = (ctypes.c_ubyte * data_in_len)()
            aptd.DataTransferLength = data_in_len

        aptd.AtaFlags = flags
        aptd.TimeOutValue = timeout_seconds
        if data_buf is not None:
            aptd.DataBuffer = ctypes.cast(data_buf, ctypes.c_void_p)

        # Input task file.
        ctf = aptd.CurrentTaskFile
        ctf[_TF_FEATURE] = feature & 0xFF
        ctf[_TF_SECTOR_COUNT] = sector_count & 0xFF
        ctf[_TF_LBA_LOW] = lba & 0xFF
        ctf[_TF_LBA_MID] = (lba >> 8) & 0xFF
        ctf[_TF_LBA_HIGH] = (lba >> 16) & 0xFF
        ctf[_TF_COMMAND] = command & 0xFF
        if use_lba48:
            ptf = aptd.PreviousTaskFile
            ptf[_TF_FEATURE] = (feature >> 8) & 0xFF
            ptf[_TF_SECTOR_COUNT] = (sector_count >> 8) & 0xFF
            ptf[_TF_LBA_LOW] = (lba >> 24) & 0xFF
            ptf[_TF_LBA_MID] = (lba >> 32) & 0xFF
            ptf[_TF_LBA_HIGH] = (lba >> 40) & 0xFF
            ctf[_TF_DEVICE] = ATA_DEVICE_LBA
        else:
            ctf[_TF_DEVICE] = ATA_DEVICE_LBA | ((lba >> 24) & 0x0F)

        bytes_returned = wintypes.DWORD(0)
        struct_size = ctypes.sizeof(ATA_PASS_THROUGH_DIRECT)
        ok = kernel32.DeviceIoControl(
            handle,
            IOCTL_ATA_PASS_THROUGH_DIRECT,
            ctypes.addressof(aptd),
            struct_size,
            ctypes.addressof(aptd),
            struct_size,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok:
            err = ctypes.get_last_error()
            audit_log(
                f"ATA pass-through cmd 0x{command:02X} failed "
                f"(DeviceIoControl error {err})"
            )
            return None

        current = bytes(aptd.CurrentTaskFile)
        previous = bytes(aptd.PreviousTaskFile)
        payload = bytes(data_buf) if (data_buf is not None and data_in_len > 0) else b""
        return _RawResult(
            status=current[_TF_STATUS],
            error=current[_TF_ERROR],
            current=current,
            previous=previous,
            data=payload,
        )
    except Exception as exc:  # noqa: BLE001 — public surface must never raise
        audit_log(f"ATA pass-through cmd 0x{command:02X} raised: {exc}")
        return None


# ── IDENTIFY parsing helpers ─────────────────────────────────────────────

def _words_from_identify(data: bytes) -> tuple:
    """Decode 512 bytes into 256 little-endian USHORT words."""
    return struct.unpack("<256H", data[:IDENTIFY_BUFFER_LEN])


def _ata_string(words: tuple, start: int, end_inclusive: int) -> str:
    """Decode an ATA string field (byte-swapped ASCII) from a word range."""
    chars = []
    for i in range(start, end_inclusive + 1):
        w = words[i]
        chars.append(chr((w >> 8) & 0xFF))
        chars.append(chr(w & 0xFF))
    return "".join(chars).strip()


def _erase_unit_minutes(word89: int) -> int:
    """Decode the SECURITY ERASE UNIT time word into minutes (0 if unknown)."""
    if not word89:
        return 0
    if word89 & 0x8000:  # extended (15-bit) format
        return (word89 & 0x7FFF) * 2
    return (word89 & 0x00FF) * 2


# ── Public API ───────────────────────────────────────────────────────────

def ata_identify(handle: int) -> IdentifyData | None:
    """Issue IDENTIFY DEVICE (0xEC) and parse the result.

    Returns ``None`` if the device rejects pass-through (common on USB bridges)
    or anything goes wrong.

    Security parsing note: ``supported``/``enabled`` are taken from the command
    feature-set words (82/85) OR the dedicated security status word (128);
    ``locked``/``frozen``/``enhanced_erase_supported`` come from word 128, which
    is the authoritative SECURITY status word per ATA8-ACS (words 82/85 do not
    carry the lock/freeze/enhanced bits).
    """
    try:
        raw = _execute_aptd(
            handle,
            command=ATA_CMD_IDENTIFY_DEVICE,
            data_in_len=IDENTIFY_BUFFER_LEN,
        )
        if raw is None or len(raw.data) < IDENTIFY_BUFFER_LEN:
            return None

        words = _words_from_identify(raw.data)

        w82 = words[82]
        w85 = words[85]
        w128 = words[128]

        security_supported = bool(w82 & 0x0002) or bool(w128 & 0x0001)
        security_enabled = bool(w85 & 0x0002) or bool(w128 & 0x0002)
        security_locked = bool(w128 & 0x0004)
        security_frozen = bool(w128 & 0x0008)
        enhanced_erase_supported = bool(w128 & 0x0020)

        lba48 = (
            words[100]
            | (words[101] << 16)
            | (words[102] << 32)
            | (words[103] << 48)
        )

        info = IdentifyData(
            model=_ata_string(words, 27, 46),
            serial=_ata_string(words, 10, 19),
            firmware=_ata_string(words, 23, 26),
            lba48_max_sectors=lba48,
            security_supported=security_supported,
            security_enabled=security_enabled,
            security_locked=security_locked,
            security_frozen=security_frozen,
            enhanced_erase_supported=enhanced_erase_supported,
            erase_unit_minutes=_erase_unit_minutes(words[89]),
            raw_words=words,
        )
        audit_log(
            f"IDENTIFY: model={info.model!r} serial={info.serial!r} "
            f"lba48_max={info.lba48_max_sectors} "
            f"security(supp={security_supported},en={security_enabled},"
            f"locked={security_locked},frozen={security_frozen},"
            f"enhanced={enhanced_erase_supported})"
        )
        return info
    except Exception as exc:  # noqa: BLE001 — public surface must never raise
        audit_log(f"ata_identify raised: {exc}")
        return None


def send_ata_command(
    handle: int,
    *,
    command: int,
    feature: int = 0,
    lba: int = 0,
    sector_count: int = 0,
    data_out: bytes | None = None,
    data_in_len: int = 0,
    use_lba48: bool = False,
    timeout_seconds: int = 30,
) -> AtaCommandResult | None:
    """Send an arbitrary ATA command via pass-through.

    Returns ``None`` if the IOCTL itself fails (rejected / no pass-through),
    otherwise an ``AtaCommandResult``. ``success`` is ``True`` only when the
    IOCTL succeeded AND the drive's ERR status bit is clear — a command that
    reaches the drive but is aborted yields ``success=False`` with the
    ``ata_status``/``ata_error`` registers populated.
    """
    raw = _execute_aptd(
        handle,
        command=command,
        feature=feature,
        lba=lba,
        sector_count=sector_count,
        data_out=data_out,
        data_in_len=data_in_len,
        use_lba48=use_lba48,
        timeout_seconds=timeout_seconds,
    )
    if raw is None:
        return None
    return AtaCommandResult(
        success=not bool(raw.status & ATA_STATUS_ERR),
        ata_status=raw.status,
        ata_error=raw.error,
        data=raw.data,
    )


def read_native_max_address(handle: int) -> int | None:
    """Issue READ NATIVE MAX ADDRESS EXT (0x27) → native sector count.

    The native max LBA (which includes any Host Protected Area) is read from
    the output task-file registers: low 24 bits from CurrentTaskFile, high 24
    bits from PreviousTaskFile. Native sectors = native max LBA + 1.

    Returns ``None`` if pass-through fails or the drive aborts the command.
    """
    try:
        raw = _execute_aptd(
            handle,
            command=ATA_CMD_READ_NATIVE_MAX_ADDRESS_EXT,
            use_lba48=True,
        )
        if raw is None or (raw.status & ATA_STATUS_ERR):
            return None

        cur = raw.current
        prev = raw.previous
        native_max_lba = (
            cur[_TF_LBA_LOW]
            | (cur[_TF_LBA_MID] << 8)
            | (cur[_TF_LBA_HIGH] << 16)
            | (prev[_TF_LBA_LOW] << 24)
            | (prev[_TF_LBA_MID] << 32)
            | (prev[_TF_LBA_HIGH] << 40)
        )
        native_sectors = native_max_lba + 1
        audit_log(f"READ NATIVE MAX ADDRESS EXT: native_sectors={native_sectors}")
        return native_sectors
    except Exception as exc:  # noqa: BLE001 — public surface must never raise
        audit_log(f"read_native_max_address raised: {exc}")
        return None


def device_configuration_identify_max(handle: int) -> int | None:
    """Issue DEVICE CONFIGURATION IDENTIFY (0xB1 / feature 0xC2) → DCO max sectors.

    The DCO "maximum number of user-addressable sectors" is a 48/64-bit value
    stored in words 3..6 of the returned data. It can exceed the native max
    when a Device Configuration Overlay hides capacity.

    Returns ``None`` if the drive rejects the command (common — many drives /
    USB bridges do not support DCO) or reports no usable value.
    """
    try:
        raw = _execute_aptd(
            handle,
            command=ATA_CMD_DEVICE_CONFIGURATION,
            feature=ATA_FEATURE_DEVICE_CONFIGURATION_IDENTIFY,
            data_in_len=IDENTIFY_BUFFER_LEN,
        )
        if raw is None or (raw.status & ATA_STATUS_ERR):
            return None
        if len(raw.data) < IDENTIFY_BUFFER_LEN:
            return None

        words = _words_from_identify(raw.data)
        dco_max_sectors = (
            words[3]
            | (words[4] << 16)
            | (words[5] << 32)
            | (words[6] << 48)
        )
        if dco_max_sectors == 0:
            return None
        audit_log(
            f"DEVICE CONFIGURATION IDENTIFY: dco_max_sectors={dco_max_sectors}"
        )
        return dco_max_sectors
    except Exception as exc:  # noqa: BLE001 — public surface must never raise
        audit_log(f"device_configuration_identify_max raised: {exc}")
        return None
