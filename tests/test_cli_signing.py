"""CLI: signed certificates on wipe, verify-cert, keycard, trust, key, history --verify."""

from __future__ import annotations

import ctypes
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_fake_kernel32 = MagicMock()
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()  # type: ignore[attr-defined]
ctypes.windll.kernel32 = _fake_kernel32

import cli  # noqa: E402
import core.config as config  # noqa: E402
import core.log as log  # noqa: E402
from cert.generator import CertificateData, generate_certificate  # noqa: E402
from cert.signing import SigningKey, SigningKeyError, b64url, keycard_url  # noqa: E402
from cert.verify_cert import TrustStore  # noqa: E402

SEED = bytes(range(1, 33))
FINGERPRINT = "65B6-0673-D6ED-884B-F01C-2C22-2D82-ADA0"
PUBLIC_KEY_B64 = "ebVWLo_mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ"


@pytest.fixture(autouse=True)
def _logs_in_tmp(tmp_path, monkeypatch):
    """Audit log and history go to tmp, never to the real profile."""
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr(log, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(log, "AUDIT_LOG_FILE", cfg_dir / "audit.log")
    monkeypatch.setattr(log, "WIPE_HISTORY_FILE", cfg_dir / "wipe_history.csv")


@pytest.fixture()
def app_config(tmp_path):
    cfg = config.AppConfig()
    cfg.company.name = "Beispiel GmbH"
    cfg.cert_output_dir = str(tmp_path / "certs")
    cfg.cert_language = "de"
    with patch.object(cli.AppConfig, "load", return_value=cfg):
        yield cfg


def _run(argv: list[str]) -> int:
    args = cli.build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _cert_data(**overrides) -> CertificateData:
    values = dict(
        cert_number=7, date=datetime(2026, 11, 17, 10, 0, 0), operator="Max Mustermann",
        client_reference="", asset_tag="", device_model="SanDisk 3.2Gen1",
        device_manufacturer="", serial_number="DEMO00000001", capacity_bytes=61953654784,
        filesystem="FAT32", connection_type="USB", wipe_method="ZeroFill",
        sicherheitsstufe="H-2", schutzklasse=2, passes=1,
        start_time=datetime(2026, 11, 17, 9, 0, 0), end_time=datetime(2026, 11, 17, 9, 50, 0),
        verification_passed=True, sectors_checked=100, verification_hash="",
        company_name="Beispiel GmbH", company_address="", company_logo_path="",
        language="de", verify_method="full",
    )
    values.update(overrides)
    return CertificateData(**values)


def _signed_pdf(tmp_path, name="cert.pdf") -> str:
    key = SigningKey.from_seed(SEED)
    return generate_certificate(_cert_data(), str(tmp_path / name), signing_key=key)


# ── wipe signs its certificate ───────────────────────────────────────

def _fake_device():
    return SimpleNamespace(
        drive_letter="E:", disk_letters=["E:"], device_id=r"\\.\PhysicalDrive99",
        model="Fake USB Stick", serial_number="FAKE-SERIAL-0001",
        capacity_bytes=1024 * 1024, filesystem="FAT32", connection_type="USB",
        is_removable=True, is_system_drive=False, is_internal=False,
        has_bitlocker=False, has_active_processes=False, partition_count=1,
        safe_to_wipe=True, friendly_name="Fake USB Stick (E:)",
    )


def _fake_wipe_result():
    now = datetime.now()
    verify = SimpleNamespace(
        success=True, method="full", bytes_verified=1024 * 1024, expected_pattern="zeros",
        error_count=0, mismatch_offsets=[], duration_seconds=0.5, sectors_checked=2048,
        sectors_matched=2048, sample_hash="a" * 64, timestamp=now,
    )
    return SimpleNamespace(
        method_name="ZeroFill", passes=1, start_time=now, end_time=now,
        bytes_written=1024 * 1024, success=True, error_message=None,
        verify_result=verify, zero_blank_appended=False,
        bad_sector_count=0, bad_sector_bytes=0, bad_sector_offsets=[],
    )


def _wipe(app_config, *, generate=None, log_csv=None, extra=None):
    args = cli.build_parser().parse_args(
        ["wipe", "--device", "E:", "--method", "zero", "--operator", "Op", "--yes",
         "--verify", "full"]
    )
    method = MagicMock()
    method.name = "ZeroFill"
    method.passes = 1
    method.sicherheitsstufe = "H-2"
    method.execute.return_value = _fake_wipe_result()
    patches = [
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "_resolve_wipe_method", return_value=method),
        patch.object(cli, "prepare_target"),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(
            cli, "detect_hidden_areas",
            return_value=SimpleNamespace(supported=False, hpa_present=False, dco_present=False),
        ),
        patch.object(cli, "get_next_cert_number", return_value=1),
        patch.object(cli, "generate_certificate", side_effect=generate or (lambda *a, **k: "x.pdf")),
        patch.object(cli, "log_wipe_to_csv", side_effect=log_csv or (lambda row: "ab" * 32)),
        patch.object(cli, "audit_log"),
    ] + list(extra or [])
    for p in patches:
        p.start()
    try:
        code = 0
        try:
            cli.cmd_wipe(args)
        except SystemExit as exc:
            code = int(exc.code or 0)
    finally:
        for p in reversed(patches):
            p.stop()
    return code, method


def test_wipe_signs_certificate_and_passes_history_hash(app_config):
    calls: list[str] = []
    captured: dict = {}

    def fake_log(row):
        calls.append("log")
        captured["row"] = row
        return "cd" * 32

    def fake_generate(data, path, signing_key=None, history_hash=""):
        calls.append("cert")
        captured.update(data=data, key=signing_key, history_hash=history_hash)
        return path

    code, _ = _wipe(app_config, generate=fake_generate, log_csv=fake_log)

    assert code == 0
    assert calls == ["log", "cert"], "history entry must exist before the certificate"
    assert isinstance(captured["key"], SigningKey)
    assert captured["key"].fingerprint() == config.load_signing_key().fingerprint()
    assert captured["history_hash"] == "cd" * 32
    assert captured["row"]["cert_number"] == "1"


def test_wipe_refuses_before_touching_the_disk_when_key_unreadable(app_config):
    broken = patch.object(cli, "load_signing_key", side_effect=SigningKeyError("kaputt"))
    opener = MagicMock(return_value=12345)
    code, method = _wipe(
        app_config, extra=[broken, patch.object(cli, "open_physical_drive", opener)]
    )
    assert code == 1
    opener.assert_not_called()
    method.execute.assert_not_called()


# ── verify-cert ──────────────────────────────────────────────────────

def test_verify_cert_trusted_exit_0(tmp_path, capsys):
    TrustStore.load(config.TRUST_FILE).add(SigningKey.from_seed(SEED).public_key_bytes(), "IT Beispiel")
    pdf = _signed_pdf(tmp_path)
    assert _run(["verify-cert", pdf]) == 0
    out = capsys.readouterr().out
    assert "Echt" in out and "IT Beispiel" in out
    assert FINGERPRINT in out
    assert "DEMO00000001" in out and "Max Mustermann" in out
    assert "SS-000007" in out


def test_verify_cert_unknown_key_exit_1(tmp_path, capsys):
    pdf = _signed_pdf(tmp_path)
    assert _run(["verify-cert", pdf]) == 1
    out = capsys.readouterr().out
    assert "Schlüssel unbekannt" in out
    assert FINGERPRINT in out


def test_verify_cert_tampered_exit_2(tmp_path, capsys):
    pdf = _signed_pdf(tmp_path)
    data = bytearray(open(pdf, "rb").read())
    pos = bytes(data).index(b"stickshredder-token:") + len("stickshredder-token:") + 12
    data[pos] = ord("A") if data[pos] != ord("A") else ord("B")
    tampered = tmp_path / "tampered.pdf"
    tampered.write_bytes(bytes(data))
    assert _run(["verify-cert", str(tampered)]) == 2
    assert "Ungültig" in capsys.readouterr().out


def test_verify_cert_pdf_without_token_exit_2(tmp_path, capsys):
    pdf = generate_certificate(_cert_data(), str(tmp_path / "plain.pdf"))
    assert _run(["verify-cert", pdf]) == 2
    assert "Kein Prüfcode" in capsys.readouterr().out


def test_verify_cert_does_not_pass_control_characters_to_the_terminal(tmp_path, capsys):
    key = SigningKey.from_seed(SEED)
    pdf = generate_certificate(
        _cert_data(operator="\x1b[32mEcht\x1b[0m\r\nFake"), str(tmp_path / "c.pdf"),
        signing_key=key,
    )
    assert _run(["verify-cert", pdf]) == 1
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out
    assert "Echt" in out and "Fake" in out


def test_verify_cert_missing_file_exit_2(tmp_path):
    assert _run(["verify-cert", str(tmp_path / "nope.pdf")]) == 2


def test_verify_cert_damaged_trust_list_still_verifies_signature(tmp_path, capsys):
    config.TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TRUST_FILE.write_text("{broken", "utf-8")
    pdf = _signed_pdf(tmp_path)
    assert _run(["verify-cert", pdf]) == 1


# ── keycard ──────────────────────────────────────────────────────────

def test_keycard_writes_pdf_for_own_key(tmp_path, app_config, capsys):
    out = tmp_path / "karte.pdf"
    assert _run(["keycard", "--output", str(out), "--language", "en"]) == 0
    key = config.load_signing_key()
    assert out.exists()
    assert f"stickshredder-key:{b64url(key.public_key_bytes())}".encode() in out.read_bytes()
    assert key.fingerprint() in capsys.readouterr().out


def test_keycard_default_output_in_cert_dir(tmp_path, app_config):
    assert _run(["keycard"]) == 0
    from pathlib import Path

    pdfs = list(Path(app_config.cert_output_dir).glob("*.pdf"))
    assert len(pdfs) == 1


def test_keycard_rejects_bad_language():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["keycard", "--language", "fr"])


# ── trust ────────────────────────────────────────────────────────────

def test_trust_add_from_keycard_url_uses_its_name(capsys):
    url = keycard_url(SigningKey.from_seed(SEED).public_key_bytes(), "Beispiel GmbH")
    assert _run(["trust", "add", url]) == 0
    assert TrustStore.load(config.TRUST_FILE).as_dict() == {FINGERPRINT: "Beispiel GmbH"}
    assert FINGERPRINT in capsys.readouterr().out


def test_trust_add_name_overrides_url_name():
    url = keycard_url(SigningKey.from_seed(SEED).public_key_bytes(), "Beispiel GmbH")
    assert _run(["trust", "add", url, "--name", "Kunde A"]) == 0
    assert TrustStore.load(config.TRUST_FILE).as_dict() == {FINGERPRINT: "Kunde A"}


def test_trust_add_raw_key_with_name():
    assert _run(["trust", "add", PUBLIC_KEY_B64, "--name", "Kunde B"]) == 0
    assert TrustStore.load(config.TRUST_FILE).as_dict() == {FINGERPRINT: "Kunde B"}


def test_trust_add_raw_key_without_name_fails():
    assert _run(["trust", "add", PUBLIC_KEY_B64]) == 1
    assert TrustStore.load(config.TRUST_FILE).as_dict() == {}


def test_trust_add_keycard_pdf(tmp_path):
    from cert.keycard import generate_keycard

    card = generate_keycard(SigningKey.from_seed(SEED), "Beispiel GmbH", tmp_path / "k.pdf")
    assert _run(["trust", "add", str(card), "--name", "Kunde C"]) == 0
    assert TrustStore.load(config.TRUST_FILE).as_dict() == {FINGERPRINT: "Kunde C"}


@pytest.mark.parametrize("bad", ["not-a-key", "abc", "https://example.com/#key=xyz&name=a"])
def test_trust_add_invalid_key_fails(bad):
    assert _run(["trust", "add", bad, "--name", "X"]) == 1


def test_trust_list_and_remove(capsys):
    TrustStore.load(config.TRUST_FILE).add(SigningKey.from_seed(SEED).public_key_bytes(), "Kunde A")
    assert _run(["trust", "list"]) == 0
    out = capsys.readouterr().out
    assert FINGERPRINT in out and "Kunde A" in out

    assert _run(["trust", "remove", FINGERPRINT.lower()]) == 0
    assert TrustStore.load(config.TRUST_FILE).as_dict() == {}
    assert _run(["trust", "remove", FINGERPRINT]) == 1


def test_trust_list_empty(capsys):
    assert _run(["trust", "list"]) == 0
    assert "Keine" in capsys.readouterr().out


# ── key export / import ──────────────────────────────────────────────

def test_key_export_import_round_trip(tmp_path, monkeypatch):
    own = config.load_signing_key()
    answers = iter(["geheim123", "geheim123"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: next(answers))
    export = tmp_path / "key.pem"
    assert _run(["key", "export", str(export)]) == 0
    assert export.exists()

    # Simulate a second PC: different key dir and trust list.
    other = tmp_path / "pc2"
    monkeypatch.setattr(config, "KEY_DIR", other)
    monkeypatch.setattr(config, "TRUST_FILE", other / "trusted_keys.json")
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: "geheim123")
    assert _run(["key", "import", str(export)]) == 0
    assert config.load_signing_key().fingerprint() == own.fingerprint()
    assert own.fingerprint() in TrustStore.load(config.TRUST_FILE).as_dict()


def test_key_export_password_mismatch(tmp_path, monkeypatch):
    answers = iter(["eins", "zwei"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: next(answers))
    export = tmp_path / "key.pem"
    assert _run(["key", "export", str(export)]) == 1
    assert not export.exists()


def test_key_export_empty_password(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: "")
    assert _run(["key", "export", str(tmp_path / "k.pem")]) == 1


def test_key_import_wrong_password(tmp_path, monkeypatch):
    SigningKey.from_seed(SEED).export_protected(tmp_path / "k.pem", "richtig")
    before = config.load_signing_key().fingerprint()
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: "falsch")
    assert _run(["key", "import", str(tmp_path / "k.pem")]) == 1
    assert config.load_signing_key().fingerprint() == before


# ── history --verify ─────────────────────────────────────────────────

def _history_row(n: int) -> dict:
    return {"date": "2026-11-17 10:00:00", "serial_number": f"S{n}", "cert_number": n,
            "result": "SUCCESS"}


def test_history_verify_intact(capsys):
    for n in range(3):
        log.log_wipe_to_csv(_history_row(n))
    assert _run(["history", "--verify"]) == 0
    assert "Verlauf unverändert" in capsys.readouterr().out


def test_history_verify_reports_first_broken_entry(capsys):
    import csv

    for n in range(1, 4):
        log.log_wipe_to_csv(_history_row(n))
    rows = log.read_wipe_history()
    rows[1]["serial_number"] = "GEFAELSCHT"
    with open(log.WIPE_HISTORY_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=log.CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    assert _run(["history", "--verify"]) == 1
    out = capsys.readouterr().out
    assert "Verlauf unverändert" not in out
    assert "2" in out and "SS-000002" in out


def test_history_verify_counts_legacy_rows(capsys):
    import csv

    legacy = [h for h in log.CSV_HEADERS if h not in ("prev_hash", "entry_hash")]
    log.WIPE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(log.WIPE_HISTORY_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=legacy)
        writer.writeheader()
        writer.writerow({"serial_number": "OLD", "cert_number": "1"})
    log.log_wipe_to_csv(_history_row(2))
    assert _run(["history", "--verify"]) == 0
    out = capsys.readouterr().out
    assert "Verlauf unverändert" in out
    assert "1" in out
