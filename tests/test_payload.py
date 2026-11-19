"""Tests for cert.payload — CertificateData → signed payload (shared contract v1)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cert.generator import CertificateData
from cert.payload import PAYLOAD_LABELS, RESULT_LABELS, build_payload, result_status
from core.config import APP_VERSION

CONTRACT_KEYS = {
    "v", "n", "t", "op", "co", "dm", "ds", "dc", "dt", "m", "p", "sk", "sl",
    "r", "vm", "ve", "bs", "hpa", "sw", "h", "k",
}


def _data(**overrides) -> CertificateData:
    defaults = dict(
        cert_number=42,
        date=datetime(2026, 11, 17, 10, 0, 0, tzinfo=timezone.utc),
        operator="Max Mustermann",
        client_reference="",
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
        company_name="Beispiel GmbH",
        company_address="",
        company_logo_path="",
        language="de",
        verify_method="full",
    )
    defaults.update(overrides)
    return CertificateData(**defaults)


# ── result_status ────────────────────────────────────────────────────

def test_result_ok_when_everything_passed():
    assert result_status(_data()) == "ok"


def test_result_ok_when_verification_skipped():
    assert result_status(_data(verify_method="none", verification_passed=False)) == "ok"


def test_result_failed_when_verification_failed():
    assert result_status(_data(verification_passed=False, verify_error_count=3)) == "failed"


def test_result_failed_when_sample_verification_failed():
    assert result_status(_data(verify_method="sample", verification_passed=False)) == "failed"


def test_result_failed_when_wipe_failed():
    assert result_status(_data(wipe_success=False)) == "failed"


def test_result_failed_beats_limited():
    data = _data(wipe_success=False, bad_sector_count=2)
    assert result_status(data) == "failed"


def test_result_limited_with_unwritable_sectors():
    assert result_status(_data(bad_sector_count=1, bad_sector_bytes=512)) == "limited"


def test_result_limited_with_hidden_area_not_removed():
    data = _data(hidden_area_probed=True, hpa_present=True, hpa_hidden_bytes=1 << 20)
    assert result_status(data) == "limited"


def test_result_limited_with_dco_not_removed():
    data = _data(hidden_area_probed=True, dco_present=True, dco_hidden_bytes=1 << 20)
    assert result_status(data) == "limited"


def test_result_ok_when_hidden_area_was_removed():
    data = _data(
        hidden_area_probed=True, hpa_present=True, hpa_hidden_bytes=1 << 20,
        hidden_area_removed=True,
    )
    assert result_status(data) == "ok"


# ── build_payload ────────────────────────────────────────────────────

def test_payload_has_exactly_the_contract_keys_except_k():
    payload = build_payload(_data())
    assert set(payload) == CONTRACT_KEYS - {"k"}


def test_payload_maps_the_test_vector_fields():
    payload = build_payload(_data())
    assert payload == {
        "v": 1,
        "n": 42,
        "t": "2026-11-17T10:00:00Z",
        "op": "Max Mustermann",
        "co": "Beispiel GmbH",
        "dm": "SanDisk 3.2Gen1",
        "ds": "DEMO00000001",
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
        "sw": APP_VERSION,
        "h": "",
    }


def test_payload_time_is_utc_with_z_for_naive_local_time():
    naive = datetime(2026, 11, 17, 11, 30, 5)
    expected = naive.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = build_payload(_data(date=naive))
    assert payload["t"] == expected
    assert payload["t"].endswith("Z")


def test_payload_time_converts_aware_datetimes():
    from datetime import timedelta

    cet = timezone(timedelta(hours=1))
    payload = build_payload(_data(date=datetime(2026, 11, 17, 11, 0, 0, tzinfo=cet)))
    assert payload["t"] == "2026-11-17T10:00:00Z"


def test_payload_carries_history_hash():
    payload = build_payload(_data(), history_hash="ab" * 32)
    assert payload["h"] == "ab" * 32


def test_payload_joins_manufacturer_and_model():
    payload = build_payload(_data(device_manufacturer="SanDisk", device_model="Ultra"))
    assert payload["dm"] == "SanDisk Ultra"


def test_payload_hidden_area_text():
    assert build_payload(_data(hidden_area_probed=True))["hpa"] == "none"
    data = _data(
        hidden_area_probed=True, hpa_present=True, hpa_hidden_bytes=1024**3,
        dco_present=True, dco_hidden_bytes=512 * 1024**2,
    )
    assert build_payload(data)["hpa"] == "HPA:1.00 GB+DCO:512.00 MB"


def test_payload_counts_and_types():
    data = _data(
        verify_method="full", verification_passed=False, verify_error_count=7,
        bad_sector_count=3, bad_sector_bytes=3 * 1024**2, passes=4, schutzklasse=3,
    )
    payload = build_payload(data)
    assert payload["ve"] == 7 and payload["bs"] == 3
    assert payload["p"] == 4 and payload["sk"] == 3
    assert payload["r"] == "failed"
    for key in ("v", "n", "dc", "p", "sk", "ve", "bs"):
        assert type(payload[key]) is int, key


def test_payload_unknown_verify_method_falls_back_to_none_text():
    payload = build_payload(_data(verify_method="weird"))
    assert payload["vm"] == "none"


@pytest.mark.parametrize("key", sorted(CONTRACT_KEYS))
def test_every_payload_key_has_a_bilingual_label(key):
    de, en = PAYLOAD_LABELS[key]
    assert de and en


def test_result_labels_are_bilingual():
    assert RESULT_LABELS["ok"] == ("Erfolgreich", "Successful")
    assert RESULT_LABELS["limited"][0] == "Mit Einschränkungen"
    assert RESULT_LABELS["failed"][0] == "Fehlgeschlagen"
