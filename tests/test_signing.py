"""Tests for cert.signing — Ed25519 certificate signing."""

from __future__ import annotations

import json
import sys

import pytest

from cert.signing import (
    SigningKey,
    SigningKeyError,
    b64url,
    b64url_decode,
    canonical_bytes,
    fingerprint_of,
    keycard_url,
    verify_url,
)

# --- Shared test vector (must match the browser verify page byte-for-byte) ---
SEED = bytes(range(1, 33))
PUBKEY_B64 = "ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ"
FINGERPRINT = "65B6-0673-D6ED-884B-F01C-2C22-2D82-ADA0"
TOKEN = (
    "eyJicyI6MCwiY28iOiJCZWlzcGllbCBHbWJIIiwiZGMiOjYxOTUzNjU0Nzg0LCJkbSI6IlNhbkRpc2sgMy4yR2VuMSIs"
    "ImRzIjoiMDkwMUMzRjIiLCJkdCI6IlVTQiIsImgiOiIiLCJocGEiOiJuL2EiLCJrIjoiZWJWV0xvX21WUGxBZUxFUzZL"
    "bUxwNUFmaFRybWxiN1g0T09SQzYwRWxtUSIsIm0iOiJaZXJvRmlsbCIsIm4iOjQyLCJvcCI6Ik1heCBNdXN0ZXJtYW5u"
    "IiwicCI6MSwiciI6Im9rIiwic2siOjIsInNsIjoiSC0yIiwic3ciOiIyLjAuMCIsInQiOiIyMDI2LTExLTE3VDEwOjAw"
    "OjAwWiIsInYiOjEsInZlIjowLCJ2bSI6ImZ1bGwifQ"
    ".XR2XsL_KAfkdyOTIduvuAletZ4CSwL-TmNYHY4UqFanWmkaTnpj_O7sHe1CJIY8L3zkiQEZh_jCIBtCbBc4gBA"
)


def vector_payload() -> dict:
    """The test-vector payload without ``k`` (sign_payload sets it)."""
    return {
        "v": 1,
        "n": 42,
        "t": "2026-11-17T10:00:00Z",
        "op": "Max Mustermann",
        "co": "Beispiel GmbH",
        "dm": "SanDisk 3.2Gen1",
        "ds": "0901C3F2",
        "dc": 61953654784,
        "dt": "USB",
        "m": "ZeroFill",
        "p": 1,
        "sk": 2,
        "sl": "H-2",
        "r": "ok",
        "vm": "full",
        "ve": 0,
        "bs": 0,
        "hpa": "n/a",
        "sw": "2.0.0",
        "h": "",
    }


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


# ---------------------------------------------------------------------------
# Test vector
# ---------------------------------------------------------------------------
def test_vector_public_key():
    key = SigningKey.from_seed(SEED)
    assert b64url(key.public_key_bytes()) == PUBKEY_B64
    assert len(key.public_key_bytes()) == 32


def test_vector_fingerprint():
    key = SigningKey.from_seed(SEED)
    assert key.fingerprint() == FINGERPRINT
    assert fingerprint_of(key.public_key_bytes()) == FINGERPRINT


def test_vector_token_is_reproduced_exactly():
    key = SigningKey.from_seed(SEED)
    assert key.sign_payload(vector_payload()) == TOKEN


def test_sign_payload_sets_public_key_field():
    key = SigningKey.from_seed(SEED)
    payload = vector_payload()
    payload["k"] = "something-else"
    token = key.sign_payload(payload)
    assert payload["k"] == PUBKEY_B64
    assert token == TOKEN


def test_from_seed_rejects_wrong_length():
    with pytest.raises(ValueError):
        SigningKey.from_seed(b"\x01" * 31)


# ---------------------------------------------------------------------------
# Canonical bytes / encoding helpers
# ---------------------------------------------------------------------------
def test_canonical_bytes_sorted_compact_utf8():
    data = canonical_bytes({"b": 1, "a": "Prüfung", "c": [1, 2]})
    assert data == '{"a":"Prüfung","b":1,"c":[1,2]}'.encode("utf-8")


def test_canonical_bytes_matches_token_payload():
    payload = vector_payload()
    payload["k"] = PUBKEY_B64
    head = TOKEN.split(".")[0]
    assert b64url(canonical_bytes(payload)) == head


def test_b64url_has_no_padding_and_url_alphabet():
    encoded = b64url(b"\xfb\xff\xfe")
    assert encoded == "-__-"
    assert "=" not in b64url(b"a")
    assert b64url(b"a") == "YQ"


@pytest.mark.parametrize("data", [b"", b"a", b"ab", b"abc", bytes(range(256))])
def test_b64url_roundtrip(data):
    assert b64url_decode(b64url(data)) == data


@pytest.mark.parametrize("bad", ["YQ=", "Y Q", "a+b/", "abc!", "A"])
def test_b64url_decode_rejects_invalid(bad):
    with pytest.raises(ValueError):
        b64url_decode(bad)


def test_fingerprint_of_rejects_wrong_length():
    with pytest.raises(ValueError):
        fingerprint_of(b"\x00" * 31)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
def test_verify_url():
    assert verify_url("abc.def") == "https://rctom16-bit.github.io/stickshredder/verify/#abc.def"


def test_keycard_url_percent_encodes_company():
    key = SigningKey.from_seed(SEED)
    url = keycard_url(key.public_key_bytes(), "Müller & Söhne GmbH")
    assert url == (
        "https://rctom16-bit.github.io/stickshredder/verify/"
        f"#key={PUBKEY_B64}&name=M%C3%BCller%20%26%20S%C3%B6hne%20GmbH"
    )


# ---------------------------------------------------------------------------
# DPAPI-protected key storage
# ---------------------------------------------------------------------------
@windows_only
def test_load_or_create_creates_and_reloads_same_key(tmp_path):
    key_dir = tmp_path / "keys"
    first = SigningKey.load_or_create(key_dir)
    key_file = key_dir / "signing_key.bin"
    assert key_file.is_file()
    second = SigningKey.load_or_create(key_dir)
    assert first.public_key_bytes() == second.public_key_bytes()


@windows_only
def test_key_file_is_not_plain_seed(tmp_path):
    key = SigningKey.load_or_create(tmp_path)
    raw = (tmp_path / "signing_key.bin").read_bytes()
    # DPAPI blobs are much larger than the 32-byte seed and never equal to it.
    assert len(raw) > 32
    assert key.public_key_bytes() not in raw


@windows_only
def test_dpapi_roundtrip_signs_identically(tmp_path):
    key = SigningKey.load_or_create(tmp_path)
    reloaded = SigningKey.load_or_create(tmp_path)
    p1, p2 = vector_payload(), vector_payload()
    assert key.sign_payload(p1) == reloaded.sign_payload(p2)


@windows_only
def test_corrupt_key_file_raises_and_is_not_overwritten(tmp_path):
    key_file = tmp_path / "signing_key.bin"
    key_file.write_bytes(b"garbage, not a DPAPI blob")
    with pytest.raises(SigningKeyError):
        SigningKey.load_or_create(tmp_path)
    assert key_file.read_bytes() == b"garbage, not a DPAPI blob"


# ---------------------------------------------------------------------------
# Password-protected export / import
# ---------------------------------------------------------------------------
@windows_only
def test_export_import_roundtrip(tmp_path):
    key = SigningKey.from_seed(SEED)
    export = tmp_path / "export" / "key.pem"
    key.export_protected(export, "correct horse battery staple")
    text = export.read_text("ascii")
    assert "ENCRYPTED PRIVATE KEY" in text

    key_dir = tmp_path / "other-pc"
    imported = SigningKey.import_protected(export, "correct horse battery staple", key_dir)
    assert imported.fingerprint() == FINGERPRINT
    # The imported key is now this installation's key.
    assert SigningKey.load_or_create(key_dir).fingerprint() == FINGERPRINT


def test_import_with_wrong_password_fails(tmp_path):
    key = SigningKey.from_seed(SEED)
    export = tmp_path / "key.pem"
    key.export_protected(export, "right password")
    key_dir = tmp_path / "keys"
    with pytest.raises(SigningKeyError):
        SigningKey.import_protected(export, "wrong password", key_dir)
    assert not (key_dir / "signing_key.bin").exists()


def test_export_rejects_empty_password(tmp_path):
    key = SigningKey.from_seed(SEED)
    with pytest.raises(ValueError):
        key.export_protected(tmp_path / "key.pem", "")


def test_import_rejects_non_key_file(tmp_path):
    bogus = tmp_path / "bogus.pem"
    bogus.write_text("not a key", "ascii")
    with pytest.raises(SigningKeyError):
        SigningKey.import_protected(bogus, "pw", tmp_path / "keys")


@windows_only
def test_import_backs_up_existing_key(tmp_path):
    key_dir = tmp_path / "keys"
    old = SigningKey.load_or_create(key_dir)
    export = tmp_path / "key.pem"
    SigningKey.from_seed(SEED).export_protected(export, "pw12345")
    SigningKey.import_protected(export, "pw12345", key_dir)

    backups = list(key_dir.glob("signing_key.*.bak"))
    assert len(backups) == 1
    assert SigningKey.load_or_create(key_dir).fingerprint() == FINGERPRINT
    # The old key is still recoverable from the backup.
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    (restored_dir / "signing_key.bin").write_bytes(backups[0].read_bytes())
    assert SigningKey.load_or_create(restored_dir).fingerprint() == old.fingerprint()


def test_token_payload_is_valid_json():
    key = SigningKey.from_seed(SEED)
    head = key.sign_payload(vector_payload()).split(".")[0]
    assert json.loads(b64url_decode(head))["k"] == PUBKEY_B64


def test_signing_key_repr_hides_secret():
    key = SigningKey.from_seed(SEED)
    assert SEED.hex() not in repr(key)
    assert FINGERPRINT in repr(key)

