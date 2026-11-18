"""Signed certificates: generate a real PDF, read it back, verify the token."""

from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

import cert.generator as generator
from cert.generator import CertificateData, generate_certificate
from cert.signing import SigningKey, b64url, b64url_decode, verify_url
from cert.verify_cert import extract_token_from_pdf, verify_token

SEED = bytes(range(1, 33))
FINGERPRINT = "65B6-0673-D6ED-884B-F01C-2C22-2D82-ADA0"
# The exact regex the browser verify page uses (docs/verify/verify.js).
JS_TOKEN_RE = re.compile(rb"stickshredder-token:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")


@pytest.fixture(autouse=True)
def _no_audit_log(monkeypatch):
    monkeypatch.setattr(generator, "audit_log", lambda *_a, **_k: None)


def _data(**overrides) -> CertificateData:
    defaults = dict(
        cert_number=7,
        date=datetime(2026, 11, 17, 10, 0, 0),
        operator="Max Mustermann",
        client_reference="Ticket 4711",
        asset_tag="",
        device_model="SanDisk 3.2Gen1",
        device_manufacturer="",
        serial_number="DEMO00000001",
        capacity_bytes=61953654784,
        filesystem="FAT32",
        connection_type="USB",
        wipe_method="ZeroFill",
        sicherheitsstufe="H-2",
        schutzklasse=2,
        passes=1,
        start_time=datetime(2026, 11, 17, 9, 0, 0),
        end_time=datetime(2026, 11, 17, 9, 50, 0),
        verification_passed=True,
        sectors_checked=100,
        verification_hash="",
        company_name="Beispiel GmbH (Ä&Ö)",
        company_address="Teststr. 1",
        company_logo_path="",
        language="de",
        verify_method="full",
    )
    defaults.update(overrides)
    return CertificateData(**defaults)


class _ParagraphSpy:
    """Record every Paragraph text the generator renders."""

    def __init__(self, monkeypatch):
        self.texts: list[str] = []
        real = generator.Paragraph
        spy = self

        class Spy(real):  # type: ignore[misc, valid-type]
            def __init__(self, text, *args, **kwargs):
                spy.texts.append(str(text))
                super().__init__(text, *args, **kwargs)

        monkeypatch.setattr(generator, "Paragraph", Spy)

    @property
    def joined(self) -> str:
        return "\n".join(self.texts)


# ── Required round trip ──────────────────────────────────────────────

def test_round_trip_signed_pdf_verifies_as_trusted(tmp_path):
    key = SigningKey.from_seed(SEED)
    out = generate_certificate(
        _data(), str(tmp_path / "cert.pdf"), signing_key=key, history_hash="ab" * 32
    )
    pdf_bytes = open(out, "rb").read()

    token = extract_token_from_pdf(pdf_bytes)
    assert token is not None
    outcome = verify_token(token, trusted={FINGERPRINT: "x"})
    assert outcome.status == "trusted"
    assert outcome.fingerprint == FINGERPRINT
    assert outcome.payload["n"] == 7
    assert outcome.payload["h"] == "ab" * 32
    assert outcome.payload["co"] == "Beispiel GmbH (Ä&Ö)"
    assert outcome.payload["k"] == b64url(key.public_key_bytes())


def test_raw_pdf_bytes_match_the_js_regex_exactly(tmp_path):
    key = SigningKey.from_seed(SEED)
    out = generate_certificate(_data(), str(tmp_path / "cert.pdf"), signing_key=key)
    pdf_bytes = open(out, "rb").read()

    matches = JS_TOKEN_RE.findall(pdf_bytes)
    assert len(matches) == 1
    token = matches[0].decode("ascii")
    # The match must be the whole token: no escaping or hex encoding by
    # reportlab, and nothing token-like glued on either side.
    end = pdf_bytes.index(matches[0]) + len(matches[0])
    assert pdf_bytes[end : end + 1] == b")"
    outcome = verify_token(token, trusted={})
    assert outcome.status == "unknown_key"


def test_one_changed_payload_byte_makes_token_invalid(tmp_path):
    key = SigningKey.from_seed(SEED)
    out = generate_certificate(_data(), str(tmp_path / "cert.pdf"), signing_key=key)
    token = extract_token_from_pdf(open(out, "rb").read())
    body, signature = token.split(".")

    raw = bytearray(b64url_decode(body))
    index = raw.index(b'"n":7') + 4
    raw[index] = ord("8")  # certificate number 7 -> 8
    tampered = f"{b64url(bytes(raw))}.{signature}"
    assert json.loads(bytes(raw))["n"] == 8

    assert verify_token(tampered, trusted={FINGERPRINT: "x"}).status == "invalid"


def test_tampered_pdf_bytes_are_detected(tmp_path):
    """Editing the token text inside the PDF file itself breaks the signature."""
    key = SigningKey.from_seed(SEED)
    out = generate_certificate(_data(), str(tmp_path / "cert.pdf"), signing_key=key)
    pdf_bytes = bytearray(open(out, "rb").read())
    token = extract_token_from_pdf(bytes(pdf_bytes))
    start = bytes(pdf_bytes).index(token.encode("ascii")) + 10
    pdf_bytes[start] = ord("A") if pdf_bytes[start] != ord("A") else ord("B")
    tampered = extract_token_from_pdf(bytes(pdf_bytes))
    assert verify_token(tampered, trusted={FINGERPRINT: "x"}).status == "invalid"


# ── Content of the signed certificate ────────────────────────────────

def test_signed_certificate_has_authenticity_section(tmp_path, monkeypatch):
    spy = _ParagraphSpy(monkeypatch)
    qr_values: list[str] = []
    real_qr = generator.QrCodeWidget

    def qr_spy(value, *args, **kwargs):
        qr_values.append(value)
        return real_qr(value, *args, **kwargs)

    monkeypatch.setattr(generator, "QrCodeWidget", qr_spy)
    key = SigningKey.from_seed(SEED)
    out = generate_certificate(_data(), str(tmp_path / "cert.pdf"), signing_key=key)
    token = extract_token_from_pdf(open(out, "rb").read())

    assert "Echtheit" in spy.joined
    assert FINGERPRINT in spy.joined
    assert (
        "Echtheit prüfen: QR-Code scannen oder PDF in StickShredder öffnen. "
        "Fingerabdruck mit der Schlüsselkarte Ihrer IT vergleichen." in spy.joined
    )
    assert qr_values == [verify_url(token)]


def test_signed_certificate_english_texts(tmp_path, monkeypatch):
    spy = _ParagraphSpy(monkeypatch)
    key = SigningKey.from_seed(SEED)
    generate_certificate(_data(language="en"), str(tmp_path / "c.pdf"), signing_key=key)
    assert "Authenticity" in spy.joined
    assert "Check authenticity: scan the QR code" in spy.joined
    assert "Echtheit prüfen" not in spy.joined


def test_signed_certificate_both_languages(tmp_path, monkeypatch):
    spy = _ParagraphSpy(monkeypatch)
    key = SigningKey.from_seed(SEED)
    generate_certificate(_data(language="both"), str(tmp_path / "c.pdf"), signing_key=key)
    assert "Echtheit / Authenticity" in spy.joined
    assert "Echtheit prüfen" in spy.joined and "Check authenticity" in spy.joined


def test_signed_certificate_still_fits_on_one_page(tmp_path):
    key = SigningKey.from_seed(SEED)
    out = generate_certificate(_data(language="both"), str(tmp_path / "c.pdf"), signing_key=key)
    pdf_bytes = open(out, "rb").read()
    assert len(re.findall(rb"/Type /Page\b", pdf_bytes)) == 1


@pytest.mark.parametrize(
    ("overrides", "text"),
    [
        ({}, "Erfolgreich"),
        ({"bad_sector_count": 2, "bad_sector_bytes": 1024}, "Mit Einschränkungen"),
        ({"verification_passed": False, "verify_error_count": 4}, "Fehlgeschlagen"),
    ],
)
def test_result_line_shows_three_level_result(tmp_path, monkeypatch, overrides, text):
    spy = _ParagraphSpy(monkeypatch)
    generate_certificate(_data(**overrides), str(tmp_path / "c.pdf"))
    assert "Gesamtergebnis" in spy.joined
    assert text in spy.texts


def test_unsigned_certificate_has_no_token_and_no_authenticity_section(tmp_path, monkeypatch):
    spy = _ParagraphSpy(monkeypatch)
    out = generate_certificate(_data(), str(tmp_path / "c.pdf"))
    pdf_bytes = open(out, "rb").read()
    assert b"stickshredder-token:" not in pdf_bytes
    assert extract_token_from_pdf(pdf_bytes) is None
    assert "Echtheit" not in spy.joined


def test_positional_call_without_key_still_works(tmp_path):
    out = generate_certificate(_data(), str(tmp_path / "c.pdf"))
    assert open(out, "rb").read(5) == b"%PDF-"
