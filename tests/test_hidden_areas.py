"""Tests for v1.2 hidden-area (HPA/DCO) detection — report-only.

Pure unit tests: the ATA passthrough layer (`wipe.passthrough`) is mocked, so
these run on any machine with no real drive and no hardware access. We patch the
three fixed passthrough entry points that `wipe.hidden_areas` depends on:

    ata_identify(handle) -> IdentifyData | None   (.lba48_max_sectors)
    read_native_max_address(handle) -> int | None
    device_configuration_identify_max(handle) -> int | None
"""

from types import SimpleNamespace
from unittest.mock import patch

from wipe.hidden_areas import (
    HiddenAreaInfo,
    detect_hidden_areas,
    hidden_bytes,
)

SECTOR_SIZE = 512


def _identify(lba48_max_sectors: int) -> SimpleNamespace:
    """Stand-in for passthrough.IdentifyData — only the field we read matters."""
    return SimpleNamespace(lba48_max_sectors=lba48_max_sectors)


def _patch(*, identify, native, dco):
    """Patch the three passthrough functions on the hidden_areas namespace."""
    return (
        patch("wipe.hidden_areas.ata_identify", return_value=identify),
        patch("wipe.hidden_areas.read_native_max_address", return_value=native),
        patch("wipe.hidden_areas.device_configuration_identify_max", return_value=dco),
    )


def _detect(*, identify, native, dco, handle=123) -> HiddenAreaInfo:
    p_id, p_native, p_dco = _patch(identify=identify, native=native, dco=dco)
    with p_id, p_native, p_dco:
        return detect_hidden_areas(handle)


# ── HPA present ────────────────────────────────────────────────────────

def test_hpa_present_when_native_exceeds_accessible():
    info = _detect(identify=_identify(1000), native=1200, dco=1200)
    assert info.supported is True
    assert info.hpa_present is True
    assert info.hpa_hidden_sectors == 200
    assert info.accessible_max_sectors == 1000
    assert info.native_max_sectors == 1200
    assert info.error is None


def test_hpa_hidden_bytes_via_helper():
    info = _detect(identify=_identify(1000), native=1200, dco=1200)
    assert hidden_bytes(info.hpa_hidden_sectors) == 200 * SECTOR_SIZE


# ── DCO present ────────────────────────────────────────────────────────

def test_dco_present_when_dco_exceeds_native():
    info = _detect(identify=_identify(1000), native=1000, dco=1500)
    assert info.dco_present is True
    assert info.dco_hidden_sectors == 500
    assert info.dco_max_sectors == 1500
    # No HPA in this case (native == accessible).
    assert info.hpa_present is False
    assert info.hpa_hidden_sectors == 0


def test_both_hpa_and_dco_present():
    info = _detect(identify=_identify(1000), native=1200, dco=1500)
    assert info.hpa_present is True
    assert info.hpa_hidden_sectors == 200
    assert info.dco_present is True
    assert info.dco_hidden_sectors == 300  # dco(1500) - native(1200)
    assert info.supported is True


# ── Nothing hidden ─────────────────────────────────────────────────────

def test_nothing_hidden_when_all_equal():
    info = _detect(identify=_identify(1000), native=1000, dco=1000)
    assert info.supported is True
    assert info.hpa_present is False
    assert info.dco_present is False
    assert info.hpa_hidden_sectors == 0
    assert info.dco_hidden_sectors == 0
    assert info.error is None


# ── Unsupported / graceful degradation ─────────────────────────────────

def test_ata_identify_none_means_unsupported():
    """USB bridges often block ATA passthrough → IDENTIFY returns None."""
    info = _detect(identify=None, native=None, dco=None)
    assert info.supported is False
    assert info.hpa_present is False
    assert info.dco_present is False
    assert info.accessible_max_sectors == 0
    assert info.native_max_sectors == 0
    assert info.dco_max_sectors == 0
    assert info.hpa_hidden_sectors == 0
    assert info.dco_hidden_sectors == 0
    assert info.error is not None


def test_native_max_none_falls_back_to_accessible_no_hpa():
    """If READ NATIVE MAX is unavailable, fall back to accessible → no HPA claim."""
    info = _detect(identify=_identify(1000), native=None, dco=None)
    assert info.supported is True
    assert info.native_max_sectors == 1000  # fell back to accessible
    assert info.hpa_present is False
    assert info.hpa_hidden_sectors == 0


def test_dco_none_means_dco_unknown_zero():
    info = _detect(identify=_identify(1000), native=1000, dco=None)
    assert info.dco_max_sectors == 0
    assert info.dco_present is False
    assert info.dco_hidden_sectors == 0


# ── Never-negative invariants ──────────────────────────────────────────

def test_dco_below_native_never_negative():
    """A DCO max below native (odd firmware) must not yield negative hidden."""
    info = _detect(identify=_identify(1000), native=1200, dco=1100)
    assert info.dco_present is False
    assert info.dco_hidden_sectors == 0


def test_native_below_accessible_never_negative():
    """native < accessible (shouldn't happen) must not yield negative HPA."""
    info = _detect(identify=_identify(1000), native=800, dco=0)
    assert info.hpa_present is False
    assert info.hpa_hidden_sectors == 0


# ── detect_hidden_areas NEVER raises ───────────────────────────────────

def test_detect_never_raises_when_identify_raises():
    with patch("wipe.hidden_areas.ata_identify", side_effect=OSError("ioctl boom")):
        info = detect_hidden_areas(123)
    assert info.supported is False
    assert info.error is not None


def test_detect_never_raises_when_native_raises():
    p_id = patch("wipe.hidden_areas.ata_identify", return_value=_identify(1000))
    p_native = patch(
        "wipe.hidden_areas.read_native_max_address", side_effect=RuntimeError("boom")
    )
    p_dco = patch("wipe.hidden_areas.device_configuration_identify_max", return_value=0)
    with p_id, p_native, p_dco:
        info = detect_hidden_areas(123)
    # IDENTIFY succeeded so we still report supported; native probe failure falls
    # back to accessible and simply claims no HPA.
    assert info.supported is True
    assert info.hpa_present is False
    assert info.native_max_sectors == 1000


def test_detect_never_raises_when_dco_raises():
    p_id = patch("wipe.hidden_areas.ata_identify", return_value=_identify(1000))
    p_native = patch("wipe.hidden_areas.read_native_max_address", return_value=1200)
    p_dco = patch(
        "wipe.hidden_areas.device_configuration_identify_max",
        side_effect=ValueError("boom"),
    )
    with p_id, p_native, p_dco:
        info = detect_hidden_areas(123)
    assert info.supported is True
    assert info.hpa_present is True
    assert info.dco_max_sectors == 0
    assert info.dco_present is False


# ── hidden_bytes helper ────────────────────────────────────────────────

def test_hidden_bytes_multiplies_by_sector_size():
    assert hidden_bytes(1) == 512
    assert hidden_bytes(2048) == 2048 * 512


def test_hidden_bytes_zero():
    assert hidden_bytes(0) == 0
