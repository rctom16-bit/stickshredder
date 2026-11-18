"""Certificate layout (2.0 polish): a scannable QR code, no orphaned parts.

Rendering the signed certificate showed a verify QR code with 0.35 mm
modules (too fine for phone cameras), the „Echtheit“ header alone at the
bottom of page 1, and the footer alone on page 2.
"""

from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import patch

import pytest
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.lib.units import mm

import cert.generator as generator
from cert.generator import CertificateData, generate_certificate
from cert.payload import build_payload
from cert.signing import SigningKey, verify_url

KEY = SigningKey.from_seed(bytes(range(32)))   # test key


def _data(**over) -> CertificateData:
    values = dict(
        cert_number=1042, date=datetime(2026, 11, 18, 14, 30), operator="Max Mustermann",
        client_reference="", asset_tag="", device_model="DEMO USB Stick 3.2",
        device_manufacturer="", serial_number="DEMO00000001",
        capacity_bytes=61_933_428_736, filesystem="exFAT", connection_type="USB",
        wipe_method="ZeroFill", sicherheitsstufe="H-2", schutzklasse=2, passes=1,
        start_time=datetime(2026, 11, 18, 12, 56), end_time=datetime(2026, 11, 18, 14, 30),
        verification_passed=True, sectors_checked=120_963_744, verification_hash="",
        company_name="Beispiel IT-Service GmbH",
        company_address="Musterstraße 1\n90402 Nürnberg", company_logo_path="",
        language="de", verify_method="full", verify_bytes=61_933_428_736,
        verify_pattern="zeros", verify_duration_seconds=2400.0, media_type="flash",
    )
    values.update(over)
    return CertificateData(**values)


def _pages(path) -> int:
    return len(re.findall(rb"/Type /Page\b(?!s)", open(path, "rb").read()))


def _generate(tmp_path, data, footer_calls=None) -> str:
    real_footer = generator._draw_footer

    def footer(canvas, lines):
        if footer_calls is not None:
            footer_calls.append(list(lines))
        real_footer(canvas, lines)

    with patch("cert.generator.audit_log"), patch.object(generator, "_draw_footer", footer):
        return generate_certificate(data, str(tmp_path / "cert.pdf"),
                                    signing_key=KEY, history_hash="ab" * 32)


@pytest.mark.parametrize("company,operator", [
    ("Beispiel IT-Service GmbH", "Max Mustermann"),
    ("Sehr lange Firmenbezeichnung für Datenträgervernichtung und IT-Entsorgung "
     "GmbH & Co. KG", "Maximiliane Mustermann-Schnarrenberger"),
])
def test_verify_qr_modules_are_at_least_half_a_millimetre(company, operator):
    data = _data(company_name=company, operator=operator, language="both")
    url = verify_url(KEY.sign_payload(build_payload(data, "ab" * 32)))
    drawing = generator._qr_drawing(url)
    modules = generator.qr_module_count(QrCodeWidget(url, barLevel="L"))
    assert drawing.width / modules >= 0.5 * mm - 1e-6
    assert drawing.width == drawing.height


def test_short_qr_value_keeps_the_minimum_size():
    assert generator._qr_drawing("x").width == generator._QR_SIZE


def test_typical_certificate_fits_on_one_page(tmp_path):
    calls: list = []
    path = _generate(tmp_path, _data(), calls)
    assert _pages(path) == 1
    assert len(calls) == 1


def test_long_certificate_keeps_the_footer_on_every_page(tmp_path):
    calls: list = []
    path = _generate(tmp_path, _data(
        language="both", schutzklasse=3, client_reference="Auftrag 2026-117",
        asset_tag="INV-00815", reformat_performed=True, reformat_filesystem="exFAT",
        reformat_label="USB"), calls)
    pages = _pages(path)
    assert pages == 2
    assert len(calls) == pages
    assert all(len(lines) == 2 for lines in calls)   # German and English


@pytest.mark.parametrize("lang,count", [("de", 1), ("en", 1), ("both", 2)])
def test_footer_lines_per_language(lang, count):
    lines = generator._footer_lines(lang)
    assert len(lines) == count
    assert all("StickShredder v" in line and "DEKRA" in line for line in lines)


def test_authenticity_header_stays_with_the_qr_code():
    from reportlab.platypus import KeepTogether

    styles = generator._build_styles()
    elements = generator._build_authenticity_elements("tok", "AAAA-BBBB", styles, "de")
    assert isinstance(elements[0], KeepTogether)
