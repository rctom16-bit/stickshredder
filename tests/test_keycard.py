"""Tests for cert.keycard — the one-page trust anchor PDF."""

from __future__ import annotations

import base64
import re
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest

from cert.keycard import generate_keycard
from cert.signing import SigningKey, keycard_url
from tests.test_signing import FINGERPRINT, PUBKEY_B64, SEED


_PDF_STRING_RE = re.compile(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", re.S)
_PDF_ESCAPE_RE = re.compile(rb"\\([0-7]{1,3}|.)", re.S)


def _unescape_pdf_string(raw: bytes) -> bytes:
    def repl(m: re.Match) -> bytes:
        esc = m.group(1)
        if esc[:1].isdigit():
            return bytes([int(esc, 8)])
        return {b"n": b"\n", b"r": b"\r", b"t": b"\t"}.get(esc, esc)

    return _PDF_ESCAPE_RE.sub(repl, raw)


def _pdf_text(pdf: bytes) -> str:
    """Return the visible text of all content streams, one text line per output line.

    Inflates reportlab's ASCII85+Flate streams, joins the ``(...) Tj`` fragments
    of each line and decodes PDF string escapes (umlauts are WinAnsi octal escapes).
    """
    lines = []
    for match in re.finditer(rb"stream\r?\n(.*?)\s*endstream", pdf, re.S):
        body = match.group(1).strip()
        if body.endswith(b"~>"):  # reportlab's default ASCII85 layer
            body = base64.a85decode(body, adobe=True)
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        for raw_line in body.splitlines():
            parts = [_unescape_pdf_string(s) for s in _PDF_STRING_RE.findall(raw_line)]
            if parts:
                lines.append(b"".join(parts).decode("cp1252"))
    return "\n".join(lines)


@pytest.fixture
def key() -> SigningKey:
    return SigningKey.from_seed(SEED)


@pytest.fixture(autouse=True)
def _no_audit_log():
    with patch("cert.keycard.audit_log"):
        yield


@pytest.mark.parametrize("language", ["de", "en", "both"])
def test_keycard_pdf_is_created_with_fingerprint(key, tmp_path, language):
    out = generate_keycard(key, "Beispiel GmbH", tmp_path / "card.pdf", language=language)
    assert isinstance(out, Path)
    pdf = out.read_bytes()
    assert pdf.startswith(b"%PDF")
    text = _pdf_text(pdf)
    assert FINGERPRINT in text
    assert "Beispiel GmbH" in text


def test_keycard_german_texts(key, tmp_path):
    text = _pdf_text(generate_keycard(key, "Beispiel GmbH", tmp_path / "c.pdf").read_bytes())
    assert "Prüfschlüssel für Löschzertifikate" in text
    assert "IT-Leitung" in text
    assert "Verification key" not in text


def test_keycard_english_texts(key, tmp_path):
    text = _pdf_text(
        generate_keycard(key, "Beispiel GmbH", tmp_path / "c.pdf", language="en").read_bytes()
    )
    assert "Verification key for deletion certificates" in text
    assert "Head of IT" in text


def test_keycard_is_single_page(key, tmp_path):
    pdf = generate_keycard(key, "A" * 120, tmp_path / "c.pdf", language="both").read_bytes()
    assert len(re.findall(rb"/Type /Page\b(?!s)", pdf)) == 1


def test_keycard_contains_public_key_and_metadata_marker(key, tmp_path):
    pdf = generate_keycard(key, "Beispiel GmbH", tmp_path / "c.pdf").read_bytes()
    assert PUBKEY_B64 in _pdf_text(pdf)
    assert f"stickshredder-key:{PUBKEY_B64}".encode() in pdf


def test_keycard_qr_encodes_keycard_url(key, tmp_path):
    captured = {}
    from reportlab.graphics.barcode import qr

    real_init = qr.QrCodeWidget.__init__

    def spy(self, value="Hello World", **kw):
        captured["value"] = value
        real_init(self, value, **kw)

    with patch.object(qr.QrCodeWidget, "__init__", spy):
        generate_keycard(key, "Beispiel GmbH", tmp_path / "c.pdf")
    assert captured["value"] == keycard_url(key.public_key_bytes(), "Beispiel GmbH")


def test_keycard_escapes_markup_in_company(key, tmp_path):
    out = generate_keycard(key, "<b>Evil</b> & Co", tmp_path / "c.pdf")
    assert "<b>Evil</b> & Co" in _pdf_text(out.read_bytes())


def test_keycard_creates_parent_directory(key, tmp_path):
    out = generate_keycard(key, "X", tmp_path / "a" / "b" / "card.pdf")
    assert out.is_file()


def test_keycard_rejects_unknown_language(key, tmp_path):
    with pytest.raises(ValueError):
        generate_keycard(key, "X", tmp_path / "c.pdf", language="fr")


def test_key_can_be_read_back_from_keycard_pdf(key, tmp_path):
    from cert.verify_cert import extract_key_from_keycard_pdf

    pdf = generate_keycard(key, "Beispiel GmbH", tmp_path / "c.pdf").read_bytes()
    assert extract_key_from_keycard_pdf(pdf) == key.public_key_bytes()
    assert extract_key_from_keycard_pdf(b"%PDF no marker") is None
