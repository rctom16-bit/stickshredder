"""SMART health summary for ATA drives (direct SATA or behind a SAT bridge).

Only SMART READ DATA (command 0xB0, feature 0xD0) is ever sent — it reads the
drive's attribute table and changes nothing. NVMe health comes from
``wipe/nvme.py`` and uses the same ``SmartSummary`` shape.

Raw-value rules follow smartmontools' defaults so numbers match what users
see in smartctl: attribute 5 is read as raw16 (some vendors pack other
counters into its upper bytes), 187/197/198 as plain 48-bit counters.

The public surface never raises; anything unusable yields
``SmartSummary(available=False, ...)``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from core.log import audit_log
from wipe import passthrough, sat

ATA_CMD_SMART = 0xB0
SMART_FEATURE_READ_DATA = 0xD0
SMART_LBA_SIGNATURE = 0xC24F00  # LBA mid 0x4F, LBA high 0xC2
SMART_DATA_LEN = 512

_ATTR_TABLE_OFFSET = 2
_ATTR_ENTRY_LEN = 12
_ATTR_ENTRIES = 30

ATTR_REALLOCATED = 5
ATTR_REPORTED_UNCORRECTABLE = 187
ATTR_PENDING = 197
ATTR_OFFLINE_UNCORRECTABLE = 198


@dataclass
class SmartSummary:
    available: bool
    reallocated: int | None        # ATA attr 5
    pending: int | None            # ATA attr 197
    uncorrectable: int | None      # ATA attr 198 / 187 (the worse of the two)
    media_errors: int | None       # NVMe health log
    percentage_used: int | None    # NVMe health log
    source: str                    # "ata" | "sat" | "nvme" | "none"


def smart_unavailable(source: str = "none") -> SmartSummary:
    return SmartSummary(
        available=False,
        reallocated=None,
        pending=None,
        uncorrectable=None,
        media_errors=None,
        percentage_used=None,
        source=source,
    )


def _attribute_raw_values(data: bytes) -> dict[int, int]:
    """Attribute id → 48-bit raw value for every populated table slot."""
    values: dict[int, int] = {}
    for slot in range(_ATTR_ENTRIES):
        off = _ATTR_TABLE_OFFSET + slot * _ATTR_ENTRY_LEN
        attr_id = data[off]
        if attr_id == 0:
            continue
        values[attr_id] = int.from_bytes(data[off + 5:off + 11], "little")
    return values


def parse_ata_smart(data: bytes) -> SmartSummary:
    """Summarise a SMART READ DATA image (512 bytes)."""
    try:
        if not data or len(data) < SMART_DATA_LEN:
            return smart_unavailable("ata")
        values = _attribute_raw_values(data)
        if not values:
            return smart_unavailable("ata")

        reallocated = values.get(ATTR_REALLOCATED)
        if reallocated is not None:
            reallocated &= 0xFFFF
        uncorrectable_candidates = [
            values[a] for a in (ATTR_OFFLINE_UNCORRECTABLE, ATTR_REPORTED_UNCORRECTABLE)
            if a in values
        ]
        return SmartSummary(
            available=True,
            reallocated=reallocated,
            pending=values.get(ATTR_PENDING),
            uncorrectable=max(uncorrectable_candidates) if uncorrectable_candidates else None,
            media_errors=None,
            percentage_used=None,
            source="ata",
        )
    except Exception as exc:  # noqa: BLE001 — never raise
        audit_log(f"SMART parse failed: {exc}")
        return smart_unavailable("ata")


def read_ata_smart(handle, transport: Literal["ata", "sat"]) -> SmartSummary:
    """Read the SMART attribute table over ATA pass-through or a SAT bridge."""
    try:
        if transport == "ata":
            result = passthrough.send_ata_command(
                handle,
                command=ATA_CMD_SMART,
                feature=SMART_FEATURE_READ_DATA,
                lba=SMART_LBA_SIGNATURE,
                sector_count=1,
                data_in_len=SMART_DATA_LEN,
                timeout_seconds=10,
            )
            data = result.data if (result is not None and result.success) else None
        elif transport == "sat":
            data = sat.sat_ata_command(
                handle,
                ATA_CMD_SMART,
                features=SMART_FEATURE_READ_DATA,
                sector_count=1,
                lba=SMART_LBA_SIGNATURE,
                data_in_len=SMART_DATA_LEN,
            )
        else:
            return smart_unavailable(str(transport))

        if data is None:
            return smart_unavailable(transport)
        summary = replace(parse_ata_smart(data), source=transport)
        if summary.available:
            audit_log(
                f"SMART ({transport}): reallocated={summary.reallocated} "
                f"pending={summary.pending} uncorrectable={summary.uncorrectable}"
            )
        return summary
    except Exception as exc:  # noqa: BLE001 — never raise
        audit_log(f"SMART read ({transport}) raised: {exc}")
        return smart_unavailable(str(transport))
