"""Tests for cert.verify_cert — token verification, PDF extraction, trust store."""

from __future__ import annotations

import json

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate

from cert.signing import SigningKey, b64url, b64url_decode, canonical_bytes
from cert.verify_cert import (
    TrustStore,
    VerifyOutcome,
    extract_token_from_pdf,
    verify_token,
)
from tests.test_signing import FINGERPRINT, PUBKEY_B64, SEED, TOKEN, vector_payload


def _other_key() -> SigningKey:
    return SigningKey.from_seed(bytes(range(100, 132)))


# ---------------------------------------------------------------------------
# verify_token — three-level outcome
# ---------------------------------------------------------------------------
def test_trusted_when_signature_valid_and_key_known():
    out = verify_token(TOKEN, {FINGERPRINT: "Beispiel GmbH"})
    assert isinstance(out, VerifyOutcome)
    assert out.status == "trusted"
    assert out.fingerprint == FINGERPRINT
    assert out.payload is not None
    assert out.payload["n"] == 42
    assert out.payload["co"] == "Beispiel GmbH"
    assert "Beispiel GmbH" in out.reason


def test_unknown_key_when_signature_valid_but_not_trusted():
    out = verify_token(TOKEN, {})
    assert out.status == "unknown_key"
    assert out.fingerprint == FINGERPRINT
    assert out.payload is not None and out.payload["ds"] == "0901C3F2"


def test_unknown_key_when_other_key_is_trusted():
    other = _other_key()
    out = verify_token(TOKEN, {other.fingerprint(): "Andere Firma"})
    assert out.status == "unknown_key"


def test_tampered_payload_character_is_invalid():
    head, sig = TOKEN.split(".")
    raw = bytearray(b64url_decode(head))
    pos = raw.index(b"Max")
    raw[pos] = ord("N")  # "Max" -> "Nax"
    tampered = b64url(bytes(raw)) + "." + sig
    out = verify_token(tampered, {FINGERPRINT: "Beispiel GmbH"})
    assert out.status == "invalid"
    assert out.payload is None


def test_tampered_token_text_character_is_invalid():
    # Flip one character of the encoded payload itself.
    idx = 20
    ch = "A" if TOKEN[idx] != "A" else "B"
    tampered = TOKEN[:idx] + ch + TOKEN[idx + 1 :]
    out = verify_token(tampered, {FINGERPRINT: "x"})
    assert out.status == "invalid"


def test_tampered_signature_is_invalid():
    head, sig = TOKEN.split(".")
    raw = bytearray(b64url_decode(sig))
    raw[0] ^= 0x01
    out = verify_token(head + "." + b64url(bytes(raw)), {FINGERPRINT: "x"})
    assert out.status == "invalid"


def test_swapped_public_key_is_invalid():
    """A forger replacing ``k`` with their own key but keeping the old signature fails."""
    payload = json.loads(b64url_decode(TOKEN.split(".")[0]))
    payload["k"] = b64url(_other_key().public_key_bytes())
    forged = b64url(canonical_bytes(payload)) + "." + TOKEN.split(".")[1]
    assert verify_token(forged, {}).status == "invalid"


def test_forger_signing_with_own_key_is_only_unknown_key():
    payload = vector_payload()
    forged = _other_key().sign_payload(payload)
    out = verify_token(forged, {FINGERPRINT: "Beispiel GmbH"})
    assert out.status == "unknown_key"
    assert out.fingerprint != FINGERPRINT


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no-dot-here",
        "a.b.c",
        ".",
        "abc.",
        ".abc",
        "!!!.???",
        "A.A",  # base64 of impossible length
        b64url(b"not json") + "." + b64url(b"\x00" * 64),
        b64url(b"[1,2,3]") + "." + b64url(b"\x00" * 64),
        b64url(b"\xff\xfe") + "." + b64url(b"\x00" * 64),
        b64url(b'{"v":1}') + "." + b64url(b"\x00" * 64),  # no key
        b64url(b'{"v":1,"k":"abc"}') + "." + b64url(b"\x00" * 64),  # short key
        b64url(b'{"v":1,"k":5}') + "." + b64url(b"\x00" * 64),  # key not a string
        TOKEN.split(".")[0] + "." + b64url(b"\x00" * 10),  # short signature
    ],
)
def test_malformed_tokens_are_invalid_without_exception(bad):
    out = verify_token(bad, {FINGERPRINT: "x"})
    assert out.status == "invalid"
    assert out.payload is None
    assert out.reason


def test_non_string_token_is_invalid():
    assert verify_token(None, {}).status == "invalid"  # type: ignore[arg-type]


def test_unsupported_format_version_is_invalid():
    key = SigningKey.from_seed(SEED)
    payload = vector_payload()
    payload["v"] = 2
    token = key.sign_payload(payload)
    out = verify_token(token, {FINGERPRINT: "x"})
    assert out.status == "invalid"
    assert "2" in out.reason


def test_whitespace_around_token_is_tolerated():
    assert verify_token("  " + TOKEN + "\n", {FINGERPRINT: "x"}).status == "trusted"


def test_trusted_fingerprint_lookup_is_case_insensitive():
    assert verify_token(TOKEN, {FINGERPRINT.lower(): "x"}).status == "trusted"


# ---------------------------------------------------------------------------
# extract_token_from_pdf
# ---------------------------------------------------------------------------
def _pdf_with_keywords(path, keywords: str) -> bytes:
    doc = SimpleDocTemplate(str(path), pagesize=A4, keywords=keywords)
    doc.build([Paragraph("Löschzertifikat", getSampleStyleSheet()["Normal"])])
    return path.read_bytes()


def test_extract_token_from_reportlab_pdf(tmp_path):
    pdf = _pdf_with_keywords(tmp_path / "cert.pdf", f"stickshredder-token:{TOKEN}")
    assert extract_token_from_pdf(pdf) == TOKEN


def test_extract_token_then_verify(tmp_path):
    pdf = _pdf_with_keywords(tmp_path / "cert.pdf", f"stickshredder-token:{TOKEN}")
    token = extract_token_from_pdf(pdf)
    assert token is not None
    assert verify_token(token, {FINGERPRINT: "Beispiel GmbH"}).status == "trusted"


def test_extract_token_missing_marker_returns_none(tmp_path):
    pdf = _pdf_with_keywords(tmp_path / "cert.pdf", "something else")
    assert extract_token_from_pdf(pdf) is None


def test_extract_token_from_garbage_returns_none():
    assert extract_token_from_pdf(b"") is None
    assert extract_token_from_pdf(b"\x00\xff random bytes") is None


# ---------------------------------------------------------------------------
# TrustStore
# ---------------------------------------------------------------------------
def test_trust_store_missing_file_is_empty(tmp_path):
    store = TrustStore.load(tmp_path / "trusted_keys.json")
    assert store.as_dict() == {}


def test_trust_store_add_persists(tmp_path):
    path = tmp_path / "sub" / "trusted_keys.json"
    store = TrustStore.load(path)
    fp = store.add(b64url_decode(PUBKEY_B64), "Beispiel GmbH")
    assert fp == FINGERPRINT
    assert store.as_dict() == {FINGERPRINT: "Beispiel GmbH"}

    reloaded = TrustStore.load(path)
    assert reloaded.as_dict() == {FINGERPRINT: "Beispiel GmbH"}
    data = json.loads(path.read_text("utf-8"))
    assert data["keys"][0]["public_key"] == PUBKEY_B64


def test_trust_store_add_same_key_renames(tmp_path):
    store = TrustStore.load(tmp_path / "t.json")
    pk = b64url_decode(PUBKEY_B64)
    store.add(pk, "Alt")
    store.add(pk, "Neu")
    assert store.as_dict() == {FINGERPRINT: "Neu"}


def test_trust_store_remove(tmp_path):
    path = tmp_path / "t.json"
    store = TrustStore.load(path)
    store.add(b64url_decode(PUBKEY_B64), "Beispiel GmbH")
    other = _other_key()
    store.add(other.public_key_bytes(), "Andere")
    assert store.remove(FINGERPRINT.lower()) is True
    assert store.remove(FINGERPRINT) is False
    assert TrustStore.load(path).as_dict() == {other.fingerprint(): "Andere"}


def test_trust_store_rejects_bad_key_and_empty_name(tmp_path):
    store = TrustStore.load(tmp_path / "t.json")
    with pytest.raises(ValueError):
        store.add(b"\x00" * 31, "x")
    with pytest.raises(ValueError):
        store.add(b64url_decode(PUBKEY_B64), "   ")


def test_trust_store_corrupt_file_raises(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{not json", "utf-8")
    with pytest.raises(ValueError):
        TrustStore.load(path)


def test_trust_store_ignores_entry_with_mismatched_fingerprint(tmp_path):
    """A hand-edited file cannot make a key trusted under a fake fingerprint."""
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "keys": [
                    {"fingerprint": "0000-0000-0000-0000-0000-0000-0000-0000",
                     "public_key": PUBKEY_B64, "name": "Fake"}
                ],
            }
        ),
        "utf-8",
    )
    store = TrustStore.load(path)
    assert store.as_dict() == {FINGERPRINT: "Fake"}


def test_trust_store_works_with_verify_token(tmp_path):
    store = TrustStore.load(tmp_path / "t.json")
    assert verify_token(TOKEN, store.as_dict()).status == "unknown_key"
    store.add(b64url_decode(PUBKEY_B64), "Beispiel GmbH")
    assert verify_token(TOKEN, store.as_dict()).status == "trusted"
