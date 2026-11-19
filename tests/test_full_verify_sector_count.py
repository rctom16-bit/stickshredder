"""A full verification reports how many sectors it read, and the certificate shows it.

Guards against a full-mode certificate printing "Sectors Checked: 0": the
1.1 regression test only covers the demo verifier, not ``full_verify`` on a
real handle (here with ReadFile and SetFilePointerEx faked).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from reportlab.platypus import Paragraph, Table

import wipe.verify as verify
from cert.generator import CertificateData, _build_styles, _build_verification_elements

DRIVE_SIZE = 3 * 1_048_576 + 4096  # three full blocks plus a short tail


@pytest.fixture
def fake_disk(monkeypatch):
    monkeypatch.setattr(verify, "_set_file_pointer", lambda handle, position: True)
    monkeypatch.setattr(verify, "_read_block", lambda handle, size: b"\x00" * size)
    monkeypatch.setattr(verify, "audit_log", lambda *a, **k: None)


def _cell_texts(elements) -> list[str]:
    texts: list[str] = []

    def walk(item):
        if isinstance(item, Paragraph):
            texts.append(item.getPlainText())
        elif isinstance(item, Table):
            for row in item._cellvalues:
                for cell in row:
                    walk(cell)
        elif isinstance(item, (list, tuple)):
            for sub in item:
                walk(sub)
        elif isinstance(item, str):
            texts.append(item)

    walk(elements)
    return texts


def test_full_verify_counts_every_sector(fake_disk):
    result = verify.full_verify(handle=1, drive_size=DRIVE_SIZE, expected_pattern=b"\x00")
    assert result.success and result.method == "full"
    assert result.bytes_verified == DRIVE_SIZE
    assert result.sectors_checked == DRIVE_SIZE // verify.SECTOR_SIZE > 0


@pytest.mark.parametrize("lang", ["de", "en", "both"])
def test_full_verify_certificate_never_shows_zero_sectors(fake_disk, lang):
    result = verify.full_verify(handle=1, drive_size=DRIVE_SIZE, expected_pattern=b"\x00")
    now = datetime(2026, 11, 18, 10, 0, 0)
    data = CertificateData(
        cert_number=1, date=now, operator="Max Mustermann", client_reference="",
        asset_tag="", device_model="Generic USB Flash Disk", device_manufacturer="",
        serial_number="DEMO00000001", capacity_bytes=DRIVE_SIZE, filesystem="RAW",
        connection_type="USB", wipe_method="ZeroFill", sicherheitsstufe="1-2",
        schutzklasse=2, passes=1, start_time=now, end_time=now,
        verification_passed=result.success, sectors_checked=result.sectors_checked,
        verification_hash="", company_name="", company_address="",
        company_logo_path="", language=lang, verify_method=result.method,
        verify_bytes=result.bytes_verified, verify_pattern=result.expected_pattern,
        verify_error_count=result.error_count,
        verify_duration_seconds=result.duration_seconds,
    )
    texts = _cell_texts(_build_verification_elements(data, _build_styles(), lang))
    label = next(i for i, t in enumerate(texts)
                 if t.startswith(("Geprüfte Sektoren", "Sectors Checked")))
    value = texts[label + 1]
    assert value == f"{DRIVE_SIZE // 512:,}".replace(",", ".")
    assert value != "0"
