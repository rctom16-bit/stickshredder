"""Tests for core.history_chain — tamper-evident history."""

from __future__ import annotations

import hashlib

from cert.signing import canonical_bytes
from core.history_chain import GENESIS_HASH, chain_hash, verify_chain


def _records() -> list[dict]:
    return [
        {"n": 1, "t": "2026-11-17T09:00:00Z", "dm": "SanDisk", "r": "ok"},
        {"n": 2, "t": "2026-11-17T10:00:00Z", "dm": "Kingston", "r": "failed"},
        {"n": 3, "t": "2026-11-17T11:00:00Z", "dm": "Samsung", "r": "ok"},
        {"n": 4, "t": "2026-11-17T12:00:00Z", "dm": "Intenso", "r": "limited"},
    ]


def _hashes(records: list[dict]) -> list[str]:
    out, prev = [], GENESIS_HASH
    for rec in records:
        prev = chain_hash(prev, rec)
        out.append(prev)
    return out


def test_chain_hash_definition():
    rec = {"b": 2, "a": "ü"}
    expected = hashlib.sha256(b"abc" + canonical_bytes(rec)).hexdigest()
    assert chain_hash("abc", rec) == expected


def test_genesis_is_empty_string():
    assert GENESIS_HASH == ""


def test_chain_hash_is_key_order_independent():
    assert chain_hash("", {"a": 1, "b": 2}) == chain_hash("", {"b": 2, "a": 1})


def test_intact_chain_verifies():
    recs = _records()
    assert verify_chain(recs, _hashes(recs)) is None


def test_empty_chain_verifies():
    assert verify_chain([], []) is None


def test_edited_record_is_detected():
    recs = _records()
    hashes = _hashes(recs)
    recs[2]["r"] = "ok-edited"
    assert verify_chain(recs, hashes) == 2


def test_edited_record_with_recomputed_own_hash_breaks_next_link():
    recs = _records()
    hashes = _hashes(recs)
    recs[1]["r"] = "ok"
    hashes[1] = chain_hash(hashes[0], recs[1])
    assert verify_chain(recs, hashes) == 2


def test_deleted_record_is_detected():
    recs = _records()
    hashes = _hashes(recs)
    del recs[1]
    del hashes[1]
    assert verify_chain(recs, hashes) == 1


def test_deleted_first_record_is_detected():
    recs = _records()
    hashes = _hashes(recs)
    assert verify_chain(recs[1:], hashes[1:]) == 0


def test_reordered_records_are_detected():
    recs = _records()
    hashes = _hashes(recs)
    recs[1], recs[2] = recs[2], recs[1]
    hashes[1], hashes[2] = hashes[2], hashes[1]
    assert verify_chain(recs, hashes) == 1


def test_length_mismatch_reports_first_unmatched_index():
    recs = _records()
    hashes = _hashes(recs)
    assert verify_chain(recs, hashes[:3]) == 3
    assert verify_chain(recs[:2], hashes) == 2


def test_hash_comparison_is_case_insensitive():
    recs = _records()
    hashes = [h.upper() for h in _hashes(recs)]
    assert verify_chain(recs, hashes) is None
