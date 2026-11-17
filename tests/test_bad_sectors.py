"""Tests for v1.2 bad-sector tolerance, reporting, and certificate rendering."""

import os
from datetime import datetime

from wipe.demo import wipe_demo_file, create_demo_file
from wipe.methods import ZeroFill
from cert.generator import (
    CertificateData,
    generate_certificate,
    _build_bad_sector_elements,
    _build_styles,
)

_MB = 1024 * 1024


# ── Demo-mode bad-sector accounting ───────────────────────────────────

def test_demo_wipe_records_simulated_bad_sectors():
    """A handful of simulated unwritable blocks are skipped, counted, reported."""
    path = create_demo_file(30 * _MB)  # 30 blocks of 1 MB → 2 bad = 6.7% (< 10% ceiling)
    try:
        bad = {3 * _MB, 7 * _MB}
        result = wipe_demo_file(
            path, ZeroFill(), verify_mode="none",
            bad_block_simulator=lambda off: off in bad,
        )
        assert result.success is True
        assert result.bad_sector_count == 2
        assert result.bad_sector_bytes == 2 * _MB
        assert set(result.bad_sector_offsets) == bad
    finally:
        os.remove(path)


def test_demo_full_verify_flags_skipped_bad_block():
    """A skipped bad block keeps its old (non-zero) data → full verify must flag it."""
    path = create_demo_file(16 * _MB)  # 1 bad block = 6.25% (< ceiling, so verify still runs)
    try:
        bad = {2 * _MB}
        result = wipe_demo_file(
            path, ZeroFill(), verify_mode="full",
            bad_block_simulator=lambda off: off in bad,
        )
        assert result.bad_sector_count == 1
        assert result.verify_result is not None
        assert result.verify_result.success is False
        assert result.verify_result.error_count >= 1
    finally:
        os.remove(path)


def test_demo_wipe_ceiling_fails_when_mostly_unwritable():
    """If essentially the whole drive is unwritable, the wipe fails (not 'success')."""
    path = create_demo_file(10 * _MB)
    try:
        result = wipe_demo_file(
            path, ZeroFill(), verify_mode="none",
            bad_block_simulator=lambda off: True,  # every block bad
        )
        assert result.success is False
        assert "unwritable" in (result.error_message or "").lower()
        assert result.bad_sector_count == 10
    finally:
        os.remove(path)


def test_demo_wipe_clean_drive_has_zero_bad_sectors():
    """No simulator → no bad sectors recorded (regression guard)."""
    path = create_demo_file(4 * _MB)
    try:
        result = wipe_demo_file(path, ZeroFill(), verify_mode="none")
        assert result.success is True
        assert result.bad_sector_count == 0
        assert result.bad_sector_bytes == 0
        assert result.bad_sector_offsets == []
    finally:
        os.remove(path)


# ── Certificate rendering ─────────────────────────────────────────────

def _cert_data(bad_count: int, **over) -> CertificateData:
    base = dict(
        cert_number=1, date=datetime.now(), operator="Robin",
        client_reference="", asset_tag="",
        device_model="Demo Drive", device_manufacturer="", serial_number="SN-1",
        capacity_bytes=10 * _MB, filesystem="FAT32", connection_type="USB",
        wipe_method="ZeroFill", sicherheitsstufe="1-2", schutzklasse=2, passes=1,
        start_time=datetime.now(), end_time=datetime.now(),
        verification_passed=False, sectors_checked=0, verification_hash="",
        company_name="ACME GmbH", company_address="", company_logo_path="",
        language="both",
        bad_sector_count=bad_count,
        bad_sector_bytes=bad_count * _MB,
        bad_sector_offsets=[i * _MB for i in range(min(bad_count, 12))],
    )
    base.update(over)
    return CertificateData(**base)


def test_certificate_with_bad_sectors_renders(tmp_path):
    data = _cert_data(3)
    out = generate_certificate(data, str(tmp_path / "cert.pdf"))
    assert os.path.isfile(out)
    # The section builder yields a header + table + warning at minimum.
    elements = _build_bad_sector_elements(data, _build_styles(), "both")
    assert len(elements) >= 3


def test_certificate_without_bad_sectors_renders(tmp_path):
    data = _cert_data(0, verification_passed=True, verification_hash="abc")
    out = generate_certificate(data, str(tmp_path / "cert2.pdf"))
    assert os.path.isfile(out)
    assert data.bad_sector_count == 0
