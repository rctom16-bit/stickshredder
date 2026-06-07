"""Hidden-area detection — Host Protected Area (HPA) and Device Configuration
Overlay (DCO).

These are regions a drive can hide from the operating system. A normal
overwrite wipe NEVER reaches them, so they may still hold old data after a
"successful" wipe. This module's job is to **detect and report** them.

PRODUCT DECISION — REPORT ONLY (v1.2):
    We deliberately do NOT unlock, resize, or remove HPA/DCO. There are no
    SET MAX ADDRESS, DEVICE CONFIGURATION SET, DCO RESTORE, or any other
    state-changing commands anywhere in this module — every passthrough call
    below is a pure read (IDENTIFY / READ NATIVE MAX / DEVICE CONFIGURATION
    IDENTIFY). Removing a hidden area is risky and needs special hardware to
    verify; instead we warn the user on the certificate and recommend physical
    destruction when hidden sectors are found.

The actual ATA passthrough lives in ``wipe.passthrough`` (the shared low-level
IOCTL wrapper, built separately). We depend ONLY on this fixed read-only
interface:

    ata_identify(handle) -> IdentifyData | None
        IdentifyData.lba48_max_sectors -> user-accessible max sector count
    read_native_max_address(handle) -> int | None
        native max sector count, including any HPA
    device_configuration_identify_max(handle) -> int | None
        DCO max sector count (the factory maximum)

A USB-bridge enclosure typically rejects ATA passthrough entirely; in that case
``ata_identify`` returns ``None`` and we report ``supported=False`` rather than
guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.log import audit_log

# Fixed read-only interface from the shared passthrough module. It is built in
# parallel; fall back to no-op stubs so this module (and its tests) still import
# cleanly before passthrough.py lands. The real functions win once present.
try:  # pragma: no cover - exercised indirectly via patching in tests
    from wipe.passthrough import (
        ata_identify,
        read_native_max_address,
        device_configuration_identify_max,
    )
except ImportError:  # pragma: no cover - passthrough.py not yet on disk
    def ata_identify(handle):  # type: ignore[misc]
        return None

    def read_native_max_address(handle):  # type: ignore[misc]
        return None

    def device_configuration_identify_max(handle):  # type: ignore[misc]
        return None


SECTOR_SIZE = 512


@dataclass
class HiddenAreaInfo:
    """Result of probing a drive for hidden areas. Read-only — informational."""

    supported: bool            # False when ATA passthrough is unavailable (e.g. USB bridge)
    accessible_max_sectors: int
    native_max_sectors: int
    dco_max_sectors: int       # 0 when unknown
    hpa_present: bool
    hpa_hidden_sectors: int    # max(0, native - accessible)
    dco_present: bool
    dco_hidden_sectors: int    # max(0, dco - native)
    error: str | None


def hidden_bytes(sectors: int) -> int:
    """Convert a hidden-sector count to bytes (512-byte sectors)."""
    return sectors * SECTOR_SIZE


def _unsupported(error: str) -> HiddenAreaInfo:
    """An all-zero / nothing-detected result used when probing isn't possible."""
    return HiddenAreaInfo(
        supported=False,
        accessible_max_sectors=0,
        native_max_sectors=0,
        dco_max_sectors=0,
        hpa_present=False,
        hpa_hidden_sectors=0,
        dco_present=False,
        dco_hidden_sectors=0,
        error=error,
    )


def detect_hidden_areas(handle: int) -> HiddenAreaInfo:
    """Probe a drive for HPA/DCO via ``wipe.passthrough``. NEVER raises.

    All passthrough calls are read-only. Any failure degrades gracefully:
      * IDENTIFY returns None / errors -> supported=False (cannot probe).
      * accessible = identify.lba48_max_sectors
      * native = read_native_max_address(handle), falling back to accessible
        when unavailable (so we never invent an HPA we can't confirm).
      * dco = device_configuration_identify_max(handle), 0 when unknown.
      * hpa_present  = native > accessible
      * dco_present  = dco_max > native
      * hidden-sector counts are clamped to >= 0.
    """
    # ── IDENTIFY: the gatekeeper. No IDENTIFY -> we cannot probe at all. ──
    try:
        identify = ata_identify(handle)
    except Exception as exc:  # passthrough should not raise, but never trust it
        audit_log(f"hidden_areas: ata_identify raised: {exc!r}")
        return _unsupported(f"ATA IDENTIFY failed: {exc}")

    if identify is None:
        audit_log("hidden_areas: ATA passthrough unavailable (IDENTIFY returned None)")
        return _unsupported(
            "ATA passthrough unavailable (e.g. USB bridge); hidden areas not probed"
        )

    accessible = int(getattr(identify, "lba48_max_sectors", 0) or 0)

    # ── READ NATIVE MAX ADDRESS: reveals an HPA. Fall back to accessible. ──
    try:
        native_raw = read_native_max_address(handle)
    except Exception as exc:
        audit_log(f"hidden_areas: read_native_max_address raised: {exc!r}")
        native_raw = None
    native = int(native_raw) if native_raw is not None else accessible

    # ── DEVICE CONFIGURATION IDENTIFY: reveals a DCO. 0 when unknown. ──
    try:
        dco_raw = device_configuration_identify_max(handle)
    except Exception as exc:
        audit_log(f"hidden_areas: device_configuration_identify_max raised: {exc!r}")
        dco_raw = None
    dco_max = int(dco_raw) if dco_raw is not None else 0

    hpa_hidden = max(0, native - accessible)
    dco_hidden = max(0, dco_max - native)
    hpa_present = native > accessible
    dco_present = dco_max > native

    if hpa_present or dco_present:
        audit_log(
            f"hidden_areas: HPA={hpa_present} ({hpa_hidden} sectors) "
            f"DCO={dco_present} ({dco_hidden} sectors); accessible={accessible} "
            f"native={native} dco={dco_max} — REPORT ONLY, drive not modified"
        )

    return HiddenAreaInfo(
        supported=True,
        accessible_max_sectors=accessible,
        native_max_sectors=native,
        dco_max_sectors=dco_max,
        hpa_present=hpa_present,
        hpa_hidden_sectors=hpa_hidden,
        dco_present=dco_present,
        dco_hidden_sectors=dco_hidden,
        error=None,
    )
