"""Recommended wipe procedure per device type (spec G6).

One overwrite pass with zeros plus a full read-back verify is enough for
today's media; extra passes add time, not security. SATA SSDs with a working
hardware erase get it first (NIST SP 800-88 "Purge"), because overwriting
cannot reach an SSD's spare/remapped flash. Multi-pass methods stay
selectable for policies that require them but are never recommended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.log import audit_log
from core.protection_class import class3_note
from wipe.device import (
    DeviceInfo,
    _get_storage_wmi_connection,
    _physical_index,
)

Media = Literal["hdd", "ssd", "flash", "unknown"]

# Appended to the descriptions of the multi-pass methods (wipe.methods).
MULTI_PASS_NOTE_DE = (
    "nur wenn eine Vorgabe es verlangt — bringt bei heutigen Datenträgern "
    "keinen Sicherheitsgewinn"
)
MULTI_PASS_NOTE_EN = (
    "only if a policy requires it — adds no security on today's media"
)

# MSFT_PhysicalDisk.MediaType / BusType values.
_MEDIA_HDD = 3
_MEDIA_SSD = 4
_BUS_USB = 7
_BUS_SD = 12
_BUS_MMC = 13

@dataclass
class Recommendation:
    method_key: str                  # CLI method key, e.g. "zero"
    verify_mode: str                 # "full" | "sample"
    hardware_erase: str | None       # "secure-erase" (run first) or None
    nist_category: str               # NIST SP 800-88: "Clear" | "Purge"
    reason_de: str
    reason_en: str
    warnings_de: list[str] = field(default_factory=list)
    warnings_en: list[str] = field(default_factory=list)


def recommend(
    device: DeviceInfo,
    media: Media,
    hardware_erase_available: bool,
    schutzklasse: int,
) -> Recommendation:
    """Recommended procedure for ``device`` (spec G6).

    ``schutzklasse`` is the DIN 66399 protection class (1-3) the user needs.
    Always one zero pass + full verify; only an SSD with a working hardware
    erase additionally gets that erase first.
    """
    if media == "hdd":
        return Recommendation(
            method_key="zero", verify_mode="full", hardware_erase=None,
            nist_category="Clear",
            reason_de=(
                "Festplatte (HDD): Ein Durchgang mit Nullen und vollständige "
                "Prüfung genügen (NIST 800-88 „Clear“)."
            ),
            reason_en=(
                "Hard disk (HDD): one pass of zeros and a full verify are "
                "enough (NIST 800-88 \"Clear\")."
            ),
        )

    # SSDs and flash with protection class 3: only destruction is enough.
    note = class3_note(media, schutzklasse, hardware_erase=hardware_erase_available)
    class3_de, class3_en = ([note[0]], [note[1]]) if note else ([], [])

    if media == "ssd" and hardware_erase_available:
        return Recommendation(
            method_key="zero", verify_mode="full", hardware_erase="secure-erase",
            nist_category="Purge",
            reason_de=(
                "SSD: Hardware-Löschen (Beta) erreicht auch Reserve-Bereiche; "
                "danach ein Durchgang Nullen und vollständige Prüfung "
                "(NIST 800-88 „Purge“)."
            ),
            reason_en=(
                "SSD: hardware erase (Beta) also reaches spare areas; then one "
                "pass of zeros and a full verify (NIST 800-88 \"Purge\")."
            ),
            warnings_de=class3_de, warnings_en=class3_en,
        )

    if media == "ssd":
        warnings_de = [
            "Kein Hardware-Löschen verfügbar: Überschreiben erreicht die "
            "Reserve-Bereiche der SSD nicht, Restdaten sind möglich."
        ]
        warnings_en = [
            "No hardware erase available: overwriting cannot reach the SSD's "
            "spare areas, residual data is possible."
        ]
        return Recommendation(
            method_key="zero", verify_mode="full", hardware_erase=None,
            nist_category="Clear",
            reason_de="SSD: Überschreiben mit Nullen und vollständige Prüfung.",
            reason_en="SSD: overwrite with zeros and a full verify.",
            warnings_de=warnings_de + class3_de, warnings_en=warnings_en + class3_en,
        )

    if media == "flash":
        return Recommendation(
            method_key="zero", verify_mode="full", hardware_erase=None,
            nist_category="Clear",
            reason_de=(
                "USB-Stick/SD-Karte: Überschreiben mit Nullen und vollständige "
                "Prüfung."
            ),
            reason_en="USB stick/SD card: overwrite with zeros and a full verify.",
            warnings_de=class3_de, warnings_en=class3_en,
        )

    return Recommendation(
        method_key="zero", verify_mode="full", hardware_erase=None,
        nist_category="Clear",
        reason_de="Überschreiben mit Nullen und vollständige Prüfung.",
        reason_en="Overwrite with zeros and a full verify.",
        warnings_de=[
            "Datenträgertyp unbekannt: Bei einer SSD oder einem Stick können "
            "nach dem Überschreiben Restdaten in Reserve-Bereichen bleiben."
        ],
        warnings_en=[
            "Media type unknown: on an SSD or stick, residual data may remain "
            "in spare areas after overwriting."
        ],
    )


def media_type_of(device: DeviceInfo) -> Media:
    """Media type of ``device`` from MSFT_PhysicalDisk; "unknown" on failure.

    MediaType 3 = HDD, 4 = SSD. SD/MMC cards, and USB disks that report no
    media type (typical USB sticks), count as "flash".
    """
    try:
        index = _physical_index(device.device_id)
        storage = _get_storage_wmi_connection()
        disk = next(
            (d for d in storage.MSFT_PhysicalDisk()
             if str(getattr(d, "DeviceId", "")).strip() == str(index)),
            None,
        )
    except Exception as exc:  # noqa: BLE001 — best effort
        audit_log(f"Media type query failed for {device.device_id}: {exc}")
        return "unknown"
    if disk is None:
        return "unknown"

    media = int(getattr(disk, "MediaType", 0) or 0)
    bus = int(getattr(disk, "BusType", 0) or 0)
    if bus in (_BUS_SD, _BUS_MMC):
        return "flash"
    if media == _MEDIA_HDD:
        return "hdd"
    if media == _MEDIA_SSD:
        return "ssd"
    if bus == _BUS_USB or device.connection_type == "USB":
        return "flash"
    return "unknown"
