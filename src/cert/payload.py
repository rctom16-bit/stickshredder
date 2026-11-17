"""Certificate data → the compact payload that is signed (format v1).

The keys and their meaning are a shared contract with the browser verify page
(``docs/verify/``). ``k`` (the public key) is added by
:meth:`cert.signing.SigningKey.sign_payload`.
"""

from __future__ import annotations

from datetime import timezone
from typing import TYPE_CHECKING, Literal

from core.config import APP_VERSION

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from cert.generator import CertificateData

PAYLOAD_VERSION = 1
VERIFY_MODES = ("none", "sample", "full")

ResultStatus = Literal["ok", "limited", "failed"]

RESULT_LABELS: dict[str, tuple[str, str]] = {
    "ok": ("Erfolgreich", "Successful"),
    "limited": ("Mit Einschränkungen", "With limitations"),
    "failed": ("Fehlgeschlagen", "Failed"),
}

# Field labels (German, English) for showing a payload to people.
PAYLOAD_LABELS: dict[str, tuple[str, str]] = {
    "v": ("Format", "Format"),
    "n": ("Zertifikatsnummer", "Certificate number"),
    "t": ("Datum (UTC)", "Date (UTC)"),
    "op": ("Bediener", "Operator"),
    "co": ("Firma", "Company"),
    "dm": ("Gerät", "Device"),
    "ds": ("Seriennummer", "Serial number"),
    "dc": ("Kapazität (Bytes)", "Capacity (bytes)"),
    "dt": ("Verbindungstyp", "Connection type"),
    "m": ("Löschmethode", "Deletion method"),
    "p": ("Durchgänge", "Passes"),
    "sk": ("Schutzklasse", "Protection class"),
    "sl": ("Sicherheitsstufe", "Security level"),
    "r": ("Ergebnis", "Result"),
    "vm": ("Prüfung", "Verification"),
    "ve": ("Prüffehler", "Verification errors"),
    "bs": ("Nicht beschreibbare Bereiche", "Unwritable regions"),
    "hpa": ("Versteckte Bereiche (HPA/DCO)", "Hidden areas (HPA/DCO)"),
    "sw": ("Programmversion", "Software version"),
    "h": ("Verlaufs-Hash", "History hash"),
    "k": ("Öffentlicher Schlüssel", "Public key"),
}


def _verify_mode(data: CertificateData) -> str:
    mode = data.verify_method
    return mode if mode in VERIFY_MODES else "none"


def _hidden_area_found(data: CertificateData) -> bool:
    return bool(data.hidden_area_probed and (data.hpa_present or data.dco_present))


def result_status(data: CertificateData) -> ResultStatus:
    """Three-level result: ``failed`` > ``limited`` > ``ok``.

    * ``failed``  – the wipe failed, or a verification ran and failed.
    * ``limited`` – unwritable sectors remain, or a hidden area (HPA/DCO) was
      found and not removed.
    * ``ok``      – otherwise (a skipped verification is not a failure).
    """
    if not data.wipe_success:
        return "failed"
    if _verify_mode(data) != "none" and not data.verification_passed:
        return "failed"
    if data.bad_sector_count > 0:
        return "limited"
    if _hidden_area_found(data) and not data.hidden_area_removed:
        return "limited"
    return "ok"


def _utc_text(moment) -> str:
    # A naive datetime is local time (``datetime.now()``); ``astimezone``
    # interprets it that way.
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hidden_area_text(data: CertificateData) -> str:
    from cert.generator import format_capacity

    if not data.hidden_area_probed:
        return "n/a"
    parts = []
    if data.hpa_present:
        parts.append(f"HPA:{format_capacity(data.hpa_hidden_bytes)}")
    if data.dco_present:
        parts.append(f"DCO:{format_capacity(data.dco_hidden_bytes)}")
    return "+".join(parts) if parts else "none"


def build_payload(data: CertificateData, history_hash: str = "") -> dict:
    """Map certificate data to the v1 payload keys (everything except ``k``)."""
    device = " ".join(
        part for part in (str(data.device_manufacturer or "").strip(),
                          str(data.device_model or "").strip()) if part
    )
    return {
        "v": PAYLOAD_VERSION,
        "n": int(data.cert_number),
        "t": _utc_text(data.date),
        "op": str(data.operator or ""),
        "co": str(data.company_name or ""),
        "dm": device,
        "ds": str(data.serial_number or ""),
        "dc": int(data.capacity_bytes),
        "dt": str(data.connection_type or ""),
        "m": str(data.wipe_method or ""),
        "p": int(data.passes),
        "sk": int(data.schutzklasse),
        "sl": str(data.sicherheitsstufe or ""),
        "r": result_status(data),
        "vm": _verify_mode(data),
        "ve": int(data.verify_error_count),
        "bs": int(data.bad_sector_count),
        "hpa": _hidden_area_text(data),
        "sw": APP_VERSION,
        "h": str(history_hash or ""),
    }
