"""Tamper-evident wipe history: every entry's hash covers the previous hash.

``hash[i] = SHA-256(hash[i-1] + canonical_json(record[i]))`` with
``hash[-1] = GENESIS_HASH``. Editing, deleting, inserting or reordering an
entry breaks the chain from that point on. Removing entries at the very end is
not detectable from the chain alone; the newest hash is therefore also written
into each signed certificate (payload field ``h``).
"""

from __future__ import annotations

import hashlib

from cert.signing import canonical_bytes

GENESIS_HASH = ""


def chain_hash(prev_hash: str, record: dict) -> str:
    """Hex SHA-256 of ``prev_hash.encode() + canonical_bytes(record)``."""
    return hashlib.sha256(prev_hash.encode("utf-8") + canonical_bytes(record)).hexdigest()


def verify_chain(records: list[dict], hashes: list[str]) -> int | None:
    """Return the index of the first broken link, or ``None`` if the chain is intact.

    If the lists differ in length, the first index present in only one of them
    counts as broken (unless an earlier link is already broken).
    """
    prev = GENESIS_HASH
    for index, (record, stored) in enumerate(zip(records, hashes)):
        expected = chain_hash(prev, record)
        if not isinstance(stored, str) or stored.lower() != expected:
            return index
        prev = expected
    if len(records) != len(hashes):
        return min(len(records), len(hashes))
    return None
