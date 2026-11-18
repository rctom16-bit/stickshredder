"""What the user chose for one wipe, and what follows from it (method, time, labels)."""

from __future__ import annotations

from dataclasses import dataclass

from gui.i18n import tr
from wipe.methods import BsiVsitr, CustomWipe, RandomThreePass, WipeMethod, ZeroFill
from wipe.recommend import recommend

METHOD_KEYS = ("zero", "standard", "bsi", "custom")
MULTI_PASS_KEYS = ("standard", "bsi")
VERIFY_KEYS = ("full", "sample")
FILESYSTEMS = ("exfat", "fat32", "ntfs")
FS_NAMES = {"exfat": "exFAT", "fat32": "FAT32", "ntfs": "NTFS"}

# Conservative MB/s for the time estimate (same figure as the old window).
ESTIMATE_MBPS = 80.0

# Connections whose disks may reach the drive's own erase: wipe.secure_erase
# only works on the "ata" path (SATA on Microsoft's storahci driver). USB
# bridges (SAT) are read-only by design and NVMe has no erase path yet.
HARDWARE_CONNECTIONS = ("SATA", "ATA")

# Old config keys ("quick" was the GUI's name for the one-pass zero fill).
_CONFIG_METHODS = {"quick": "zero", "zero": "zero", "standard": "standard", "bsi": "bsi"}


@dataclass(frozen=True)
class WipeOptions:
    method_key: str = "zero"
    custom_passes: int = 3
    custom_pattern: str = "zero"     # zero | ones | random
    schutzklasse: int = 2
    operator: str = ""
    verify_mode: str = "full"        # full | sample
    reformat: str = "none"           # none | exfat | fat32 | ntfs
    reformat_label: str = "USB"
    # Hardware erase (Beta): the drive erases itself, then 1x zeros + verify.
    hardware_erase: bool = False

    @classmethod
    def from_config(cls, config) -> "WipeOptions":
        verify = getattr(config, "default_verify_mode", "full")
        sk = int(getattr(config, "default_schutzklasse", 2) or 2)
        return cls(
            method_key=_CONFIG_METHODS.get(getattr(config, "default_wipe_method", "quick"), "zero"),
            schutzklasse=min(3, max(1, sk)),
            operator=getattr(config, "operator_name", "") or "",
            verify_mode=verify if verify in VERIFY_KEYS else "full",
        )


def hardware_erase_fits(device, media: str, schutzklasse: int = 2) -> bool:
    """Whether the hardware erase may be offered for ``device`` (WMI facts only).

    Never sends a drive command: the worker asks the drive itself after the
    safety gate, and refuses before any write when the drive says no.
    """
    if device.is_system_drive:
        return False
    plausible = (device.connection_type or "").upper() in HARDWARE_CONNECTIONS
    return recommend(device, media, plausible, schutzklasse).hardware_erase == "secure-erase"


def hardware_erase_hint(device, media: str) -> str:
    """Why an SSD gets no hardware erase ("" when it fits or is no SSD)."""
    if media != "ssd" or device.is_system_drive or hardware_erase_fits(device, media):
        return ""
    return tr("hw.hint_sata_only")


def build_method(options: WipeOptions) -> WipeMethod:
    if options.hardware_erase:
        return ZeroFill()  # the overwrite after the drive's own erase
    match options.method_key:
        case "standard":
            return RandomThreePass()
        case "bsi":
            return BsiVsitr()
        case "custom":
            return CustomWipe(passes=options.custom_passes, pattern=options.custom_pattern)
        case _:
            return ZeroFill()


def passes_of(options: WipeOptions) -> int:
    if options.hardware_erase:
        return 1
    return {"zero": 1, "standard": 3, "bsi": 7}.get(options.method_key, options.custom_passes)


def estimate_seconds(options: WipeOptions, capacity_bytes: int) -> float:
    """Rough wipe time: every pass plus one full read for a full verify."""
    mb = max(0, capacity_bytes) / (1024 * 1024)
    seconds = mb / ESTIMATE_MBPS * passes_of(options)
    if options.verify_mode == "full":
        seconds += mb / ESTIMATE_MBPS
    return seconds


def method_label(options: WipeOptions) -> str:
    """Tile text, e.g. ``1× Nullen``."""
    if options.hardware_erase:
        return tr("method.hardware")
    if options.method_key == "custom":
        return tr("method.custom", passes=options.custom_passes,
                  pattern=tr(f"pattern.{options.custom_pattern}"))
    return tr(f"method.{options.method_key}")


def method_short(options: WipeOptions) -> str:
    """Pattern words for the result summary, e.g. ``Nullen``."""
    if options.hardware_erase:
        return tr("pattern.zero")
    if options.method_key == "custom":
        return tr(f"pattern.{options.custom_pattern}")
    if options.method_key == "bsi":
        return "BSI-VSITR"
    if options.method_key == "standard":
        return tr("pattern.random")
    return tr("pattern.zero")


def fs_label(options: WipeOptions) -> str:
    label = options.reformat_label or "USB"
    return label[:11] if options.reformat == "fat32" else label
