"""Ed25519 signing for verifiable deletion certificates.

Each installation owns one Ed25519 key pair. The private key is stored on disk
protected by Windows DPAPI (bound to the current Windows user). A certificate's
data is serialised as canonical JSON and signed; the resulting *token* is
``base64url(canonical_json) + "." + base64url(signature)``. The browser verify
page and :mod:`cert.verify_cert` check that token.

The token format is a shared contract with the browser verify page — do not
change ``canonical_bytes``, ``b64url`` or the fingerprint format without
changing the page as well.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

VERIFY_BASE_URL = "https://rctom16-bit.github.io/stickshredder/verify/"
KEY_FILE_NAME = "signing_key.bin"

# Extra entropy mixed into the DPAPI protection so other programs running as the
# same user cannot unprotect the blob by accident with an empty entropy.
_DPAPI_ENTROPY = b"StickShredder signing key v1"
_DPAPI_DESCRIPTION = "StickShredder certificate signing key"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*$")


class SigningKeyError(ValueError):
    """The signing key could not be loaded, stored, imported or exported."""


# ---------------------------------------------------------------------------
# Encoding helpers (shared contract with the verify page)
# ---------------------------------------------------------------------------
def b64url(data: bytes) -> str:
    """RFC 4648 URL-safe base64 without ``=`` padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Strictly decode unpadded URL-safe base64.

    Raises ``ValueError`` for padding, characters outside the URL-safe alphabet
    or an impossible length.
    """
    if not isinstance(text, str) or not _B64URL_RE.match(text) or len(text) % 4 == 1:
        raise ValueError("invalid base64url text")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except ValueError as exc:  # binascii.Error; pragma: no cover - guarded above
        raise ValueError("invalid base64url text") from exc


def canonical_bytes(payload: dict) -> bytes:
    """Canonical JSON encoding that is signed (sorted keys, no spaces, UTF-8)."""
    return json.dumps(
        payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")


def fingerprint_of(public_key: bytes) -> str:
    """Short human-comparable fingerprint: first 16 bytes of SHA-256, ``XXXX-XXXX-…``."""
    if len(public_key) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    digest = hashlib.sha256(public_key).digest()[:16].hex().upper()
    return "-".join(digest[i : i + 4] for i in range(0, len(digest), 4))


def verify_url(token: str) -> str:
    """Verify-page link for a certificate token (data stays behind ``#``)."""
    return f"{VERIFY_BASE_URL}#{token}"


def keycard_url(public_key: bytes, company: str) -> str:
    """Verify-page link that lets an auditor add this key as trusted."""
    return f"{VERIFY_BASE_URL}#key={b64url(public_key)}&name={quote(company, safe='')}"


# ---------------------------------------------------------------------------
# DPAPI helpers
# ---------------------------------------------------------------------------
def _dpapi_protect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(
        data, _DPAPI_DESCRIPTION, _DPAPI_ENTROPY, None, None, _CRYPTPROTECT_UI_FORBIDDEN
    )


def _dpapi_unprotect(blob: bytes) -> bytes:
    import pywintypes
    import win32crypt

    try:
        _desc, data = win32crypt.CryptUnprotectData(
            blob, _DPAPI_ENTROPY, None, None, _CRYPTPROTECT_UI_FORBIDDEN
        )
    except pywintypes.error as exc:
        raise SigningKeyError(
            "Signaturschlüssel kann nicht entschlüsselt werden (anderer Windows-Benutzer "
            "oder beschädigte Datei) / Signing key cannot be decrypted (different Windows "
            "user or damaged file)"
        ) from exc
    return bytes(data)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# SigningKey
# ---------------------------------------------------------------------------
class SigningKey:
    """An installation's Ed25519 certificate signing key."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        self._public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    def __repr__(self) -> str:
        return f"SigningKey(fingerprint={self.fingerprint()!r})"

    # -- construction -------------------------------------------------------
    @classmethod
    def from_seed(cls, seed: bytes) -> SigningKey:
        """Build a key from a 32-byte Ed25519 seed (tests and storage)."""
        if len(seed) != 32:
            raise ValueError("Ed25519 seed must be 32 bytes")
        return cls(Ed25519PrivateKey.from_private_bytes(bytes(seed)))

    @classmethod
    def load_or_create(cls, key_dir: Path) -> SigningKey:
        """Load the DPAPI-protected key from ``key_dir``, creating it on first use.

        A key file that exists but cannot be read raises :class:`SigningKeyError`
        and is never overwritten — replacing it would silently change the
        installation's identity.
        """
        key_file = Path(key_dir) / KEY_FILE_NAME
        if key_file.exists():
            seed = _dpapi_unprotect(key_file.read_bytes())
            if len(seed) != 32:
                raise SigningKeyError(
                    "Signaturschlüssel-Datei ist beschädigt / Signing key file is damaged"
                )
            return cls.from_seed(seed)
        key = cls(Ed25519PrivateKey.generate())
        key._store(Path(key_dir))
        return key

    def _seed(self) -> bytes:
        return self._private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )

    def _store(self, key_dir: Path) -> None:
        _write_atomic(key_dir / KEY_FILE_NAME, _dpapi_protect(self._seed()))

    # -- public key ---------------------------------------------------------
    def public_key_bytes(self) -> bytes:
        return self._public_key

    def fingerprint(self) -> str:
        return fingerprint_of(self._public_key)

    # -- signing ------------------------------------------------------------
    def sign_payload(self, payload: dict) -> str:
        """Sign ``payload`` and return the token. Sets ``payload["k"]`` to this key."""
        payload["k"] = b64url(self._public_key)
        data = canonical_bytes(payload)
        signature = self._private_key.sign(data)
        return f"{b64url(data)}.{b64url(signature)}"

    # -- export / import ----------------------------------------------------
    def export_protected(self, path: Path, password: str) -> None:
        """Write the private key as password-encrypted PKCS#8 PEM."""
        if not password:
            raise ValueError("Passwort darf nicht leer sein / Password must not be empty")
        pem = self._private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(password.encode("utf-8")),
        )
        _write_atomic(Path(path), pem)

    @classmethod
    def import_protected(cls, path: Path, password: str, key_dir: Path) -> SigningKey:
        """Read an exported key and make it this installation's key.

        An existing key in ``key_dir`` is kept as ``signing_key.<timestamp>.bak``.
        """
        try:
            data = Path(path).read_bytes()
            private_key = serialization.load_pem_private_key(
                data, password=password.encode("utf-8") if password else None
            )
        except (ValueError, TypeError, UnsupportedAlgorithm, OSError) as exc:
            raise SigningKeyError(
                "Schlüssel konnte nicht importiert werden (falsches Passwort oder keine "
                "Schlüsseldatei) / Key could not be imported (wrong password or not a key file)"
            ) from exc
        if not isinstance(private_key, Ed25519PrivateKey):
            raise SigningKeyError(
                "Datei enthält keinen Ed25519-Schlüssel / File does not contain an Ed25519 key"
            )
        key = cls(private_key)
        key_dir = Path(key_dir)
        existing = key_dir / KEY_FILE_NAME
        if existing.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            os.replace(existing, key_dir / f"signing_key.{stamp}.bak")
        key._store(key_dir)
        return key
