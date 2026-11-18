"""Protection class 3 (DIN 66399) on flash media: what overwriting can't do.

SSDs and USB sticks keep spare (reserve) flash that no write from the PC
reaches. For protection class 3 that leaves only physical destruction. One
wording for the selection page, the CLI and the certificate.
"""

from __future__ import annotations

_SSD_DE = (
    "Schutzklasse 3: Überschreiben erreicht die Reserve-Bereiche der SSD nicht, "
    "Restdaten bleiben möglich. Für Schutzklasse 3 muss die SSD physisch "
    "vernichtet werden."
)
_SSD_EN = (
    "Protection class 3: overwriting cannot reach the SSD's spare areas, "
    "residual data remains possible. Protection class 3 requires physically "
    "destroying the SSD."
)
_SSD_HARDWARE_DE = (
    "Schutzklasse 3: Ob das Hardware-Löschen (Beta) die Reserve-Bereiche der SSD "
    "erreicht, lässt sich von außen nicht prüfen, und Überschreiben erreicht sie "
    "nicht. Für Schutzklasse 3 muss die SSD physisch vernichtet werden."
)
_SSD_HARDWARE_EN = (
    "Protection class 3: whether the hardware erase (Beta) reaches the SSD's spare "
    "areas cannot be checked from outside, and overwriting cannot reach them. "
    "Protection class 3 requires physically destroying the SSD."
)
_FLASH_DE = (
    "Hinweis: für Schutzklasse 3 physisch vernichten — Überschreiben "
    "erreicht nicht sicher alle Flash-Zellen."
)
_FLASH_EN = (
    "For protection class 3, destroy physically: overwriting cannot reliably "
    "reach every flash cell."
)


def class3_note(
    media: str, schutzklasse: int, hardware_erase: bool = False
) -> tuple[str, str] | None:
    """(German, English) note for SSDs and flash with protection class 3.

    ``media`` is "ssd", "flash", "hdd" or "unknown"; ``hardware_erase`` is
    True when the SSD's own erase (Beta) runs before the overwrite. None
    when no note applies.
    """
    if int(schutzklasse or 0) < 3:
        return None
    if media == "ssd":
        return (_SSD_HARDWARE_DE, _SSD_HARDWARE_EN) if hardware_erase else (_SSD_DE, _SSD_EN)
    if media == "flash":
        return _FLASH_DE, _FLASH_EN
    return None
