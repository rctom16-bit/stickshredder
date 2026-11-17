"""Verification of signed deletion certificates and the list of trusted keys.

Three outcomes, mirroring the browser verify page:

* ``trusted``     – signature valid and the signing key is in the trust list.
* ``unknown_key`` – signature valid, but the key is not trusted yet; the reader
                    must compare the fingerprint with the company's key card.
* ``invalid``     – malformed, altered, or not signed by the embedded key.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cert.signing import b64url, b64url_decode, fingerprint_of

SUPPORTED_FORMAT_VERSIONS = (1,)

_TOKEN_IN_PDF_RE = re.compile(rb"stickshredder-token:([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")
_KEY_IN_PDF_RE = re.compile(rb"stickshredder-key:([A-Za-z0-9_-]{43})(?![A-Za-z0-9_-])")


@dataclass
class VerifyOutcome:
    status: Literal["trusted", "unknown_key", "invalid"]
    payload: dict | None
    fingerprint: str | None
    reason: str


def _invalid(reason: str) -> VerifyOutcome:
    return VerifyOutcome("invalid", None, None, reason)


def verify_token(token: str, trusted: dict[str, str]) -> VerifyOutcome:
    """Check a certificate token. Never raises for bad input.

    ``trusted`` maps fingerprint (``XXXX-…``) to a display name.
    """
    if not isinstance(token, str):
        return _invalid("Kein Prüfcode / No verification code")
    parts = token.strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return _invalid("Prüfcode hat ein falsches Format / Verification code is malformed")

    try:
        data = b64url_decode(parts[0])
        signature = b64url_decode(parts[1])
    except ValueError:
        return _invalid("Prüfcode ist nicht lesbar / Verification code is not readable")

    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _invalid("Zertifikatsdaten sind nicht lesbar / Certificate data is not readable")
    if not isinstance(payload, dict):
        return _invalid("Zertifikatsdaten sind nicht lesbar / Certificate data is not readable")

    version = payload.get("v")
    if version not in SUPPORTED_FORMAT_VERSIONS or isinstance(version, bool):
        return _invalid(
            f"Unbekanntes Zertifikatsformat {version!r} / Unknown certificate format {version!r}"
        )

    key_text = payload.get("k")
    try:
        public_key = b64url_decode(key_text) if isinstance(key_text, str) else b""
    except ValueError:
        public_key = b""
    if len(public_key) != 32:
        return _invalid("Schlüssel fehlt oder ist ungültig / Key missing or invalid")

    if len(signature) != 64:
        return _invalid("Signatur ist ungültig / Signature is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, data)
    except (InvalidSignature, ValueError):
        return _invalid(
            "Signatur ungültig — Zertifikat wurde verändert / "
            "Signature invalid — certificate was altered"
        )

    fingerprint = fingerprint_of(public_key)
    names = {fp.upper(): name for fp, name in trusted.items()}
    if fingerprint in names:
        name = names[fingerprint]
        return VerifyOutcome(
            "trusted",
            payload,
            fingerprint,
            f"Echt — Schlüssel bekannt ({name}) / Genuine — key known ({name})",
        )
    return VerifyOutcome(
        "unknown_key",
        payload,
        fingerprint,
        "Signatur gültig, Schlüssel unbekannt — Fingerabdruck mit der Schlüsselkarte "
        "vergleichen / Signature valid, key unknown — compare the fingerprint with the key card",
    )


def extract_token_from_pdf(pdf_bytes: bytes) -> str | None:
    """Find the ``stickshredder-token:`` marker in a certificate PDF's raw bytes."""
    match = _TOKEN_IN_PDF_RE.search(pdf_bytes or b"")
    return match.group(1).decode("ascii") if match else None


def extract_key_from_keycard_pdf(pdf_bytes: bytes) -> bytes | None:
    """Return the public key stored in a key card PDF (``stickshredder-key:`` marker)."""
    match = _KEY_IN_PDF_RE.search(pdf_bytes or b"")
    if not match:
        return None
    try:
        key = b64url_decode(match.group(1).decode("ascii"))
    except ValueError:
        return None
    return key if len(key) == 32 else None


# ---------------------------------------------------------------------------
# Trust store
# ---------------------------------------------------------------------------
class TrustStore:
    """Persistent list of trusted public keys (JSON file).

    File format::

        {"version": 1, "keys": [{"fingerprint": "...", "public_key": "<b64url>",
                                 "name": "...", "added": "<ISO-8601 UTC>"}]}

    The fingerprint is always recomputed from the stored public key, so a
    hand-edited fingerprint cannot make a different key trusted.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._entries: dict[str, dict] = {}

    @classmethod
    def load(cls, path: Path) -> TrustStore:
        """Load the store from ``path``; a missing file gives an empty store.

        Raises ``ValueError`` if the file exists but is not a valid trust list.
        """
        store = cls(path)
        if not store.path.exists():
            return store
        try:
            data = json.loads(store.path.read_text("utf-8"))
            keys = data["keys"]
            if not isinstance(keys, list):
                raise TypeError("keys is not a list")
            for item in keys:
                public_key = b64url_decode(item["public_key"])
                name = str(item["name"])
                fingerprint = fingerprint_of(public_key)
                store._entries[fingerprint] = {
                    "fingerprint": fingerprint,
                    "public_key": b64url(public_key),
                    "name": name,
                    "added": str(item.get("added", "")),
                }
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(
                f"Liste vertrauter Schlüssel ist beschädigt / Trusted key list is damaged: "
                f"{store.path}"
            ) from exc
        return store

    def add(self, public_key: bytes, name: str) -> str:
        """Trust ``public_key`` under ``name`` (renames if already trusted). Returns its fingerprint."""
        fingerprint = fingerprint_of(public_key)  # validates length
        name = (name or "").strip()
        if not name:
            raise ValueError("Name darf nicht leer sein / Name must not be empty")
        previous = self._entries.get(fingerprint)
        self._entries[fingerprint] = {
            "fingerprint": fingerprint,
            "public_key": b64url(public_key),
            "name": name,
            "added": previous["added"]
            if previous
            else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self._save()
        return fingerprint

    def remove(self, fingerprint: str) -> bool:
        """Remove a trusted key. Returns ``False`` if it was not in the list."""
        if self._entries.pop((fingerprint or "").strip().upper(), None) is None:
            return False
        self._save()
        return True

    def as_dict(self) -> dict[str, str]:
        """Fingerprint → display name, suitable for :func:`verify_token`."""
        return {fp: entry["name"] for fp, entry in self._entries.items()}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "keys": list(self._entries.values())}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.path)
