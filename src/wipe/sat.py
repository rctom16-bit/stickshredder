"""SAT pass-through: ATA commands for SATA drives behind a USB bridge.

A SATA SSD/HDD in a USB enclosure is not reachable with
``IOCTL_ATA_PASS_THROUGH_DIRECT`` (see ``wipe/passthrough.py``) because Windows
sees a SCSI device. Bridges that implement SAT (SCSI / ATA Translation) accept
the SCSI command ATA PASS-THROUGH (16) and forward the ATA command inside it to
the drive. This module sends that CDB with ``IOCTL_SCSI_PASS_THROUGH_DIRECT``.

READ-ONLY BY CONSTRUCTION:
  * Only the PIO data-in protocol is ever encoded.
  * Only the (command, features) pairs in ``READ_ONLY_COMMANDS`` are sent.
    The protocol field alone does not stop a drive from acting on an opcode
    (a SANITIZE sent as "data-in" would still run), so everything else —
    erase, sanitize, set-max, DCO set/restore, SMART write/enable, writes —
    is refused before any IOCTL is issued.
  * Only 28-bit commands (EXTEND=0) are encoded; none of the allowed commands
    needs more.

ATA PASS-THROUGH (16) (opcode 0x85) is used instead of the 12-byte variant
because opcode 0xA1 collides with the MMC BLANK command on optical drives.

Callers send these only to a disk ``wipe.transport`` chose "sat" for (a USB
device identifying as vendor "ATA", not a system disk) — never to USB flash
drives or NVMe bridges.

Access rights: ``IOCTL_SCSI_PASS_THROUGH_DIRECT`` is defined with
FILE_READ_ACCESS | FILE_WRITE_ACCESS, so Windows rejects it (error 5) on a
handle opened without both GENERIC_READ and GENERIC_WRITE. On a query-only
handle every call here simply returns ``None``.

The public surface never raises; any failure yields ``None``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import struct

from core.log import audit_log
from wipe.passthrough import (
    IDENTIFY_BUFFER_LEN,
    IdentifyData,
    _ata_string,
    _erase_unit_minutes,
    _words_from_identify,
    aligned_buffer,
)

# ── Constants ────────────────────────────────────────────────────────────

IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x0004D014

SCSI_IOCTL_DATA_IN = 1
SCSI_STATUS_GOOD = 0x00

SAT_OPCODE_ATA_PASS_THROUGH_16 = 0x85
SAT_PROTOCOL_PIO_DATA_IN = 4

# Byte 2 of the CDB: T_DIR=1 (device -> host), BYT_BLOK=1 (length in blocks),
# T_LENGTH=2 (transfer length is in the SECTOR_COUNT field). CK_COND=0.
_SAT_BYTE2_PIO_IN = 0x08 | 0x04 | 0x02

ATA_SECTOR_SIZE = 512
SENSE_BUFFER_LEN = 32
DEFAULT_TIMEOUT_S = 10

# (command, features) → name. Every entry only reads data from the drive.
READ_ONLY_COMMANDS: dict[tuple[int, int], str] = {
    (0xEC, 0x00): "IDENTIFY DEVICE",
    (0xB0, 0xD0): "SMART READ DATA",
    (0xB1, 0xC2): "DEVICE CONFIGURATION IDENTIFY",
}

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


class SCSI_PASS_THROUGH_DIRECT(ctypes.Structure):
    """Mirror of the Win32 ``SCSI_PASS_THROUGH_DIRECT`` structure.

    Natural alignment (no ``_pack_``): 56 bytes on 64-bit Windows, with the
    ``DataBuffer`` pointer at offset 24 (pinned by tests/test_struct_layout.py).
    """

    _fields_ = [
        ("Length", ctypes.c_ushort),
        ("ScsiStatus", ctypes.c_ubyte),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
        ("CdbLength", ctypes.c_ubyte),
        ("SenseInfoLength", ctypes.c_ubyte),
        ("DataIn", ctypes.c_ubyte),
        ("DataTransferLength", ctypes.c_ulong),
        ("TimeOutValue", ctypes.c_ulong),
        ("DataBuffer", ctypes.c_void_p),
        ("SenseInfoOffset", ctypes.c_ulong),
        ("Cdb", ctypes.c_ubyte * 16),
    ]


class SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER(ctypes.Structure):
    """The request block plus room for the sense data the driver may return.

    Same layout as Microsoft's SPTI sample: 96 bytes, sense area at 60..92.
    """

    _fields_ = [
        ("sptd", SCSI_PASS_THROUGH_DIRECT),
        ("Filler", ctypes.c_ulong),
        ("ucSenseBuf", ctypes.c_ubyte * SENSE_BUFFER_LEN),
    ]


# ── CDB ──────────────────────────────────────────────────────────────────

def _build_cdb(command: int, features: int, sector_count: int, lba: int) -> bytes:
    """ATA PASS-THROUGH (16), PIO data-in, 28-bit (EXTEND=0).

    The DEVICE byte only carries LBA bits 27:24 (DEV=0, LBA bit clear), the
    way smartmontools sends IDENTIFY/SMART through bridges.
    """
    return bytes([
        SAT_OPCODE_ATA_PASS_THROUGH_16,
        SAT_PROTOCOL_PIO_DATA_IN << 1,
        _SAT_BYTE2_PIO_IN,
        0x00, features & 0xFF,
        0x00, sector_count & 0xFF,
        0x00, lba & 0xFF,
        0x00, (lba >> 8) & 0xFF,
        0x00, (lba >> 16) & 0xFF,
        (lba >> 24) & 0x0F,
        command & 0xFF,
        0x00,
    ])


def _sense_summary(sense: bytes) -> str:
    """Sense key / ASC / ASCQ for the audit log (fixed or descriptor format)."""
    if len(sense) < 4:
        return "no sense data"
    response = sense[0] & 0x7F
    if response in (0x72, 0x73):  # descriptor format
        key, asc, ascq = sense[1] & 0x0F, sense[2], sense[3]
    elif len(sense) >= 14:        # fixed format
        key, asc, ascq = sense[2] & 0x0F, sense[12], sense[13]
    else:
        return "no sense data"
    return f"sense key 0x{key:X}, ASC/ASCQ 0x{asc:02X}/0x{ascq:02X}"


# ── Public API ───────────────────────────────────────────────────────────

def sat_ata_command(
    handle,
    command: int,
    features: int = 0,
    sector_count: int = 0,
    lba: int = 0,
    data_in_len: int = 512,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> bytes | None:
    """Send one read-only ATA command through a SAT bridge.

    ``sector_count`` 0 means "derive it from ``data_in_len``" (SAT takes the
    transfer length from that field). Returns the data, or ``None`` when the
    command is not allowed, the request is malformed, the IOCTL fails, the
    bridge answers with a non-GOOD SCSI status, or fewer bytes arrive than
    requested.
    """
    name = READ_ONLY_COMMANDS.get((command, features))
    if name is None:
        audit_log(
            f"SAT: refused ATA command 0x{command:02X} features 0x{features:02X} "
            f"(not on the read-only list)"
        )
        return None

    if data_in_len <= 0 or data_in_len % ATA_SECTOR_SIZE:
        return None
    if sector_count == 0:
        sector_count = data_in_len // ATA_SECTOR_SIZE
    if sector_count * ATA_SECTOR_SIZE != data_in_len or sector_count > 0xFF:
        return None
    if not 0 <= lba <= 0x0FFFFFFF:
        return None

    try:
        req = SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER()
        sptd = req.sptd
        data_buf = aligned_buffer(data_in_len)

        sptd.Length = ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT)
        sptd.CdbLength = 16
        sptd.SenseInfoLength = SENSE_BUFFER_LEN
        sptd.SenseInfoOffset = SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER.ucSenseBuf.offset
        sptd.DataIn = SCSI_IOCTL_DATA_IN
        sptd.DataTransferLength = ctypes.sizeof(data_buf)
        sptd.TimeOutValue = timeout_s
        sptd.DataBuffer = ctypes.addressof(data_buf)
        cdb = _build_cdb(command, features, sector_count, lba)
        for i, b in enumerate(cdb):
            sptd.Cdb[i] = b

        bytes_returned = wintypes.DWORD(0)
        size = ctypes.sizeof(SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER)
        ok = kernel32.DeviceIoControl(
            handle,
            IOCTL_SCSI_PASS_THROUGH_DIRECT,
            ctypes.addressof(req),
            size,
            ctypes.addressof(req),
            size,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok:
            err = ctypes.get_last_error()
            audit_log(f"SAT {name} failed (DeviceIoControl error {err})")
            return None
        if sptd.ScsiStatus != SCSI_STATUS_GOOD:
            audit_log(
                f"SAT {name} rejected by bridge (SCSI status 0x{sptd.ScsiStatus:02X}, "
                f"{_sense_summary(bytes(req.ucSenseBuf))})"
            )
            return None
        if sptd.DataTransferLength < data_in_len:
            audit_log(
                f"SAT {name} returned {sptd.DataTransferLength} of {data_in_len} bytes"
            )
            return None
        return bytes(data_buf)
    except Exception as exc:  # noqa: BLE001 — public surface must never raise
        audit_log(f"SAT {name} raised: {exc}")
        return None


def identify_words_plausible(words: tuple) -> bool:
    """False for images a bridge fakes (all 0x0000 / all 0xFFFF) or whose
    integrity word (255, signature 0xA5) does not check out."""
    if len(words) < 256:
        return False
    if all(w == 0 for w in words) or all(w == 0xFFFF for w in words):
        return False
    if (words[255] & 0xFF) == 0xA5:
        raw = struct.pack("<256H", *words[:256])
        if sum(raw) & 0xFF:
            return False
    return True


def parse_identify_data(data: bytes) -> IdentifyData | None:
    """Parse a 512-byte IDENTIFY DEVICE image into PR #1's ``IdentifyData``.

    Same field rules as ``passthrough.ata_identify`` (which parses inline),
    plus a plausibility check because bridges sometimes fake success.
    """
    try:
        if data is None or len(data) < IDENTIFY_BUFFER_LEN:
            return None
        words = _words_from_identify(data)
        if not identify_words_plausible(words):
            return None

        w82, w85, w128 = words[82], words[85], words[128]
        lba48 = words[100] | (words[101] << 16) | (words[102] << 32) | (words[103] << 48)
        return IdentifyData(
            model=_ata_string(words, 27, 46),
            serial=_ata_string(words, 10, 19),
            firmware=_ata_string(words, 23, 26),
            lba48_max_sectors=lba48,
            security_supported=bool(w82 & 0x0002) or bool(w128 & 0x0001),
            security_enabled=bool(w85 & 0x0002) or bool(w128 & 0x0002),
            security_locked=bool(w128 & 0x0004),
            security_frozen=bool(w128 & 0x0008),
            enhanced_erase_supported=bool(w128 & 0x0020),
            erase_unit_minutes=_erase_unit_minutes(words[89]),
            raw_words=words,
        )
    except Exception as exc:  # noqa: BLE001
        audit_log(f"SAT IDENTIFY parse failed: {exc}")
        return None


def sat_identify(handle) -> IdentifyData | None:
    """IDENTIFY DEVICE through a SAT bridge, or ``None``."""
    data = sat_ata_command(handle, 0xEC, data_in_len=IDENTIFY_BUFFER_LEN)
    if data is None:
        return None
    info = parse_identify_data(data)
    if info is not None:
        audit_log(
            f"SAT IDENTIFY: model={info.model!r} serial={info.serial!r} "
            f"lba48_max={info.lba48_max_sectors} "
            f"security(supp={info.security_supported},frozen={info.security_frozen},"
            f"enhanced={info.enhanced_erase_supported})"
        )
    return info

def sat_dco_max_lba(handle) -> int | None:
    """DEVICE CONFIGURATION IDENTIFY through a SAT bridge → factory max LBA.

    Words 3..6 hold the highest LBA the drive can expose (sector count - 1).
    ``None`` when the bridge or drive does not support it (DCO is obsolete
    since ACS-3, so modern drives often abort).
    """
    data = sat_ata_command(handle, 0xB1, features=0xC2, data_in_len=IDENTIFY_BUFFER_LEN)
    if data is None:
        return None
    try:
        words = _words_from_identify(data)
        value = words[3] | (words[4] << 16) | (words[5] << 32) | (words[6] << 48)
        return value or None
    except Exception:  # noqa: BLE001
        return None
