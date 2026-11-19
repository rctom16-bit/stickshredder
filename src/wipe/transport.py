r"""Choose the one drive-command path for a disk — or none. READ-ONLY.

StickShredder never finds out by trial and error whether a disk understands
ATA, SAT or NVMe commands. Before any firmware command is sent,
``inspect_handle`` asks the Windows storage stack what the disk is, using
queries the drivers answer from what they already know (Explorer, Disk
Management and WMI send the same ones all the time):

  1. ``IOCTL_STORAGE_GET_DEVICE_NUMBER``   which disk the handle really is
  2. system-disk check (``wipe.device``)   a system disk gets nothing else
  3. ``IOCTL_STORAGE_QUERY_PROPERTY``      adapter + device descriptor:
     (``StorageAdapterProperty``,          bus type, buffer alignment,
     ``StorageDeviceProperty``)            maximum transfer, vendor id
  4. ``IOCTL_SCSI_GET_ADDRESS`` +          which port driver serves the disk
     ``HKLM\HARDWARE\DEVICEMAP\Scsi``      (SATA and NVMe only)

``choose_transport`` then picks exactly one path:

  SATA/ATA bus, port driver storahci     → "ata"  (IOCTL_ATA_PASS_THROUGH_DIRECT)
  USB bus, device vendor id "ATA"        → "sat"  (ATA PASS-THROUGH(16) in a SCSI
                                                   pass-through, SAT bridge)
  NVMe bus, port driver stornvme         → "nvme" (protocol-specific property
                                                   queries)
  anything else, or any doubt            → "none" (no drive command at all)

Only Microsoft's in-box drivers get firmware commands: vendor RAID/VMD
drivers (Intel RST ``iaStorVD``/``iaStorAC``, AMD RAID …) present disks with
the same bus types but handle these requests in their own code. On
17.11.2026 NVMe queries sent by trial and error to a USB stick and to a
system NVMe behind Intel RST VMD crashed Windows twice (bugcheck 0x139,
parameter 0x1D: kernel pool corruption). See ``docs/hardware-safety.md``.

The public functions never raise; any doubt yields ``transport="none"``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import winreg
from dataclasses import dataclass
from typing import Literal

from core.log import audit_log
from wipe import device

# ── Constants (Windows SDK 10.0.26100; pinned by tests/test_struct_layout.py)

IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x002D1080
IOCTL_SCSI_GET_ADDRESS = 0x00041018

# STORAGE_PROPERTY_ID / STORAGE_QUERY_TYPE
StorageDeviceProperty = 0
StorageAdapterProperty = 1
StorageAdapterProtocolSpecificProperty = 49
StorageDeviceProtocolSpecificProperty = 50
PropertyStandardQuery = 0

FILE_DEVICE_DISK = 0x00000007

# STORAGE_BUS_TYPE
BusTypeUnknown = 0x00
BusTypeScsi = 0x01
BusTypeAta = 0x03
BusTypeUsb = 0x07
BusTypeRAID = 0x08
BusTypeSas = 0x0A
BusTypeSata = 0x0B
BusTypeNvme = 0x11

_BUS_NAMES = {
    BusTypeUnknown: "unknown", BusTypeScsi: "SCSI", 0x02: "ATAPI", BusTypeAta: "ATA",
    0x04: "1394", 0x05: "SSA", 0x06: "Fibre Channel", BusTypeUsb: "USB",
    BusTypeRAID: "RAID", 0x09: "iSCSI", BusTypeSas: "SAS", BusTypeSata: "SATA",
    0x0C: "SD", 0x0D: "MMC", 0x0E: "virtual", 0x0F: "file-backed virtual",
    0x10: "Storage Spaces", BusTypeNvme: "NVMe", 0x12: "SCM", 0x13: "UFS",
    0x14: "NVMe-oF",
}

PAGE_SIZE = 4096            # drive-command data buffers are page aligned
ATA_SECTOR_SIZE = 512       # the largest transfer any drive command here needs

# Port drivers allowed to receive firmware commands (lower case).
ATA_DRIVERS = frozenset({"storahci"})
NVME_DRIVERS = frozenset({"stornvme"})
# SAT (SCSI / ATA Translation) bridges identify a translated ATA drive with
# the T10 vendor id "ATA". Anything else on USB gets no ATA command.
SAT_VENDOR_IDS = frozenset({"ATA"})

_DEVICE_DESCRIPTOR_BUFFER = 1024

Transport = Literal["ata", "sat", "nvme", "none"]


# ── Structures (mirrors of winioctl.h / ntddscsi.h, x64) ─────────────────

class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [
        ("PropertyId", ctypes.c_uint32),
        ("QueryType", ctypes.c_uint32),
        ("AdditionalParameters", ctypes.c_ubyte * 1),
    ]


class STORAGE_ADAPTER_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("Size", ctypes.c_uint32),
        ("MaximumTransferLength", ctypes.c_uint32),
        ("MaximumPhysicalPages", ctypes.c_uint32),
        ("AlignmentMask", ctypes.c_uint32),
        ("AdapterUsesPio", ctypes.c_ubyte),
        ("AdapterScansDown", ctypes.c_ubyte),
        ("CommandQueueing", ctypes.c_ubyte),
        ("AcceleratedTransfer", ctypes.c_ubyte),
        ("BusType", ctypes.c_ubyte),
        ("BusMajorVersion", ctypes.c_uint16),
        ("BusMinorVersion", ctypes.c_uint16),
        ("SrbType", ctypes.c_ubyte),
        ("AddressType", ctypes.c_ubyte),
    ]


class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("Size", ctypes.c_uint32),
        ("DeviceType", ctypes.c_ubyte),
        ("DeviceTypeModifier", ctypes.c_ubyte),
        ("RemovableMedia", ctypes.c_ubyte),
        ("CommandQueueing", ctypes.c_ubyte),
        ("VendorIdOffset", ctypes.c_uint32),
        ("ProductIdOffset", ctypes.c_uint32),
        ("ProductRevisionOffset", ctypes.c_uint32),
        ("SerialNumberOffset", ctypes.c_uint32),
        ("BusType", ctypes.c_uint32),
        ("RawPropertiesLength", ctypes.c_uint32),
        ("RawDeviceProperties", ctypes.c_ubyte * 1),
    ]


class STORAGE_DEVICE_NUMBER(ctypes.Structure):
    _fields_ = [
        ("DeviceType", ctypes.c_uint32),
        ("DeviceNumber", ctypes.c_uint32),
        ("PartitionNumber", ctypes.c_uint32),
    ]


class SCSI_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint32),
        ("PortNumber", ctypes.c_ubyte),
        ("PathId", ctypes.c_ubyte),
        ("TargetId", ctypes.c_ubyte),
        ("Lun", ctypes.c_ubyte),
    ]


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


def _ioctl(handle, code: int, in_obj, out_obj) -> int | None:
    """DeviceIoControl with lengths taken from the objects themselves.

    Returns the byte count the driver reported, or ``None`` on failure.
    """
    returned = wintypes.DWORD(0)
    ok = kernel32.DeviceIoControl(
        handle,
        code,
        None if in_obj is None else ctypes.addressof(in_obj),
        0 if in_obj is None else ctypes.sizeof(in_obj),
        ctypes.addressof(out_obj),
        ctypes.sizeof(out_obj),
        ctypes.byref(returned),
        None,
    )
    if not ok:
        return None
    return min(returned.value, ctypes.sizeof(out_obj))


# ── Result ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StorageInfo:
    transport: Transport
    reason: str                          # English, for the audit log
    disk_number: int | None = None
    adapter_bus_type: int | None = None
    device_bus_type: int | None = None
    alignment_mask: int | None = None
    max_transfer_length: int | None = None
    removable_media: bool | None = None
    vendor_id: str = ""
    product_id: str = ""
    port_driver: str | None = None


@dataclass(frozen=True)
class _Adapter:
    bus_type: int
    alignment_mask: int
    max_transfer_length: int


@dataclass(frozen=True)
class _Device:
    bus_type: int
    removable: bool
    vendor: str
    product: str


def _bus(bus_type) -> str:
    return _BUS_NAMES.get(bus_type, f"bus type {bus_type}")


# ── The rules ────────────────────────────────────────────────────────────

def choose_transport(
    *,
    adapter_bus_type: int | None,
    device_bus_type: int | None,
    alignment_mask: int | None,
    max_transfer_length: int | None,
    vendor_id: str,
    port_driver: str | None,
) -> tuple[Transport, str]:
    """The single drive-command path for these facts, with the reason."""
    if adapter_bus_type is None or device_bus_type is None:
        return "none", "bus type unknown"
    if adapter_bus_type != device_bus_type:
        return "none", (
            f"adapter and device bus types disagree "
            f"({_bus(adapter_bus_type)} vs {_bus(device_bus_type)})"
        )
    if alignment_mask is None or alignment_mask >= PAGE_SIZE:
        return "none", f"adapter buffer alignment {alignment_mask!r} is not supported"

    bus = device_bus_type
    driver = (port_driver or "").lower()
    big_enough = bool(max_transfer_length) and max_transfer_length >= ATA_SECTOR_SIZE

    if bus in (BusTypeAta, BusTypeSata):
        if driver not in ATA_DRIVERS:
            return "none", (
                f"{_bus(bus)} disk on port driver {port_driver!r}: ATA commands "
                f"are only sent through Microsoft's storahci driver"
            )
        if not big_enough:
            return "none", f"adapter transfer limit {max_transfer_length!r} is below one sector"
        return "ata", f"{_bus(bus)} disk on storahci"

    if bus == BusTypeUsb:
        vendor = (vendor_id or "").strip()
        if vendor.upper() not in SAT_VENDOR_IDS:
            return "none", (
                f"USB device with vendor id {vendor!r} does not identify as an "
                f"ATA drive behind a SAT bridge"
            )
        if not big_enough:
            return "none", f"adapter transfer limit {max_transfer_length!r} is below one sector"
        return "sat", "USB bridge presenting an ATA drive (vendor id 'ATA')"

    if bus == BusTypeNvme:
        if driver not in NVME_DRIVERS:
            return "none", (
                f"NVMe disk on port driver {port_driver!r}: NVMe queries are only "
                f"sent through Microsoft's stornvme driver"
            )
        return "nvme", "NVMe disk on stornvme"

    return "none", f"{_bus(bus)} bus has no supported drive-command path"


def current_system_disks() -> set[int] | None:
    """Physical disk numbers holding or booting Windows (``None`` if unknown)."""
    return device._current_system_disks()


def system_disk_refusal(disk_number: int, system_disks: set[int] | None) -> str | None:
    """Why this disk must not receive drive commands, or ``None`` if it may.

    StickShredder never wipes a system disk, so it never needs a drive
    command there either. An unknown layout refuses every disk.
    """
    if not system_disks:
        return "system disks could not be determined; no drive commands (fail closed)"
    if disk_number in system_disks:
        return f"disk {disk_number} is a system disk; StickShredder sends it no drive commands"
    return None


# ── Benign storage-stack queries ─────────────────────────────────────────

def get_device_number(handle) -> int | None:
    """Disk number behind ``handle`` (``IOCTL_STORAGE_GET_DEVICE_NUMBER``)."""
    out = STORAGE_DEVICE_NUMBER()
    returned = _ioctl(handle, IOCTL_STORAGE_GET_DEVICE_NUMBER, None, out)
    if returned is None or returned < ctypes.sizeof(out):
        return None
    if out.DeviceType != FILE_DEVICE_DISK:
        return None
    return out.DeviceNumber


def _query_adapter(handle) -> _Adapter | None:
    query = STORAGE_PROPERTY_QUERY(StorageAdapterProperty, PropertyStandardQuery)
    out = STORAGE_ADAPTER_DESCRIPTOR()
    returned = _ioctl(handle, IOCTL_STORAGE_QUERY_PROPERTY, query, out)
    if returned is None or returned <= STORAGE_ADAPTER_DESCRIPTOR.BusType.offset:
        return None
    return _Adapter(out.BusType, out.AlignmentMask, out.MaximumTransferLength)


def _descriptor_string(raw: bytes, offset: int) -> str:
    """NUL-terminated ASCII string at ``offset`` inside the returned bytes."""
    if offset < STORAGE_DEVICE_DESCRIPTOR.RawDeviceProperties.offset or offset >= len(raw):
        return ""
    end = raw.find(b"\0", offset)
    if end < 0:
        end = len(raw)
    return raw[offset:end].decode("ascii", errors="replace").strip()


def _query_device(handle) -> _Device | None:
    query = STORAGE_PROPERTY_QUERY(StorageDeviceProperty, PropertyStandardQuery)
    out = (ctypes.c_ubyte * _DEVICE_DESCRIPTOR_BUFFER)()
    returned = _ioctl(handle, IOCTL_STORAGE_QUERY_PROPERTY, query, out)
    if returned is None or returned < STORAGE_DEVICE_DESCRIPTOR.RawPropertiesLength.offset:
        return None
    raw = bytes(out)[:returned]
    desc = STORAGE_DEVICE_DESCRIPTOR.from_buffer_copy(
        raw.ljust(ctypes.sizeof(STORAGE_DEVICE_DESCRIPTOR), b"\0")
    )
    return _Device(
        bus_type=desc.BusType,
        removable=bool(desc.RemovableMedia),
        vendor=_descriptor_string(raw, desc.VendorIdOffset),
        product=_descriptor_string(raw, desc.ProductIdOffset),
    )


def _driver_for_scsi_port(port: int) -> str | None:
    """Port driver service name from ``HKLM\\HARDWARE\\DEVICEMAP\\Scsi``."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"HARDWARE\DEVICEMAP\Scsi\Scsi Port {port}") as key:
            value, _kind = winreg.QueryValueEx(key, "Driver")
    except OSError:
        return None
    return str(value) if value else None


def port_driver(handle) -> str | None:
    """Service name of the storage port driver serving ``handle``'s disk."""
    out = SCSI_ADDRESS()
    returned = _ioctl(handle, IOCTL_SCSI_GET_ADDRESS, None, out)
    if returned is None or returned < ctypes.sizeof(out):
        return None
    return _driver_for_scsi_port(out.PortNumber)


# ── Public entry point ───────────────────────────────────────────────────

_UNSET = object()


def _inspect(handle, expected_disk, system_disks) -> StorageInfo:
    number = get_device_number(handle)
    if number is None:
        return StorageInfo("none", "the handle's disk number could not be read")
    if expected_disk is not None and number != expected_disk:
        return StorageInfo(
            "none", f"handle belongs to disk {number}, expected disk {expected_disk}",
            disk_number=number,
        )

    if system_disks is _UNSET:
        system_disks = current_system_disks()
    refusal = system_disk_refusal(number, system_disks)
    if refusal:
        return StorageInfo("none", refusal, disk_number=number)

    adapter = _query_adapter(handle)
    if adapter is None:
        return StorageInfo("none", "adapter descriptor unavailable", disk_number=number)
    dev = _query_device(handle)
    if dev is None:
        return StorageInfo("none", "device descriptor unavailable", disk_number=number)

    driver = None
    if dev.bus_type in (BusTypeAta, BusTypeSata, BusTypeNvme):
        driver = port_driver(handle)

    transport, reason = choose_transport(
        adapter_bus_type=adapter.bus_type,
        device_bus_type=dev.bus_type,
        alignment_mask=adapter.alignment_mask,
        max_transfer_length=adapter.max_transfer_length,
        vendor_id=dev.vendor,
        port_driver=driver,
    )
    return StorageInfo(
        transport, reason,
        disk_number=number,
        adapter_bus_type=adapter.bus_type,
        device_bus_type=dev.bus_type,
        alignment_mask=adapter.alignment_mask,
        max_transfer_length=adapter.max_transfer_length,
        removable_media=dev.removable,
        vendor_id=dev.vendor,
        product_id=dev.product,
        port_driver=driver,
    )


def inspect_handle(handle, *, expected_disk: int | None = None,
                   system_disks=_UNSET) -> StorageInfo:
    """Decide which drive commands ``handle``'s disk may receive. Never raises.

    ``expected_disk`` refuses a handle that turns out to be another disk;
    ``system_disks`` reuses a system-disk list the caller already has.
    """
    try:
        info = _inspect(handle, expected_disk, system_disks)
    except Exception as exc:  # noqa: BLE001 — never raise
        info = StorageInfo("none", f"inspection failed: {exc}")
    disk = "?" if info.disk_number is None else info.disk_number
    audit_log(f"drive commands for disk {disk}: transport={info.transport} ({info.reason})")
    return info
