"""Wave 3a / Task 8: three-level result, recommendations, defaults, NIST, PDF attachment."""

from __future__ import annotations

import ctypes
import json
import re
import zlib
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
from cert.generator import (  # noqa: E402
    ATTACHMENT_NAME, CertificateData, _build_result_row, _build_styles, generate_certificate,
)
from cert.payload import RESULT_LABELS, build_payload, nist_category, result_status  # noqa: E402
from cert.signing import SigningKey, b64url_decode  # noqa: E402
from cert.verify_cert import extract_token_from_pdf  # noqa: E402
from wipe.recommend import Recommendation  # noqa: E402

SEED = bytes(range(1, 33))


@pytest.fixture(autouse=True)
def _logs_in_tmp(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr(log, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(log, "AUDIT_LOG_FILE", cfg_dir / "audit.log")
    monkeypatch.setattr(log, "WIPE_HISTORY_FILE", cfg_dir / "wipe_history.csv")


@pytest.fixture()
def app_config(tmp_path):
    cfg = config.AppConfig()
    cfg.company.name = "Beispiel GmbH"
    cfg.cert_output_dir = str(tmp_path / "certs")
    with patch.object(cli.AppConfig, "load", return_value=cfg):
        yield cfg


def _cert_data(**overrides) -> CertificateData:
    values = dict(
        cert_number=7, date=datetime(2026, 11, 17, 10, 0, 0), operator="Max Mustermann",
        client_reference="", asset_tag="", device_model="SanDisk 3.2Gen1",
        device_manufacturer="", serial_number="0901C3F2", capacity_bytes=61953654784,
        filesystem="FAT32", connection_type="USB", wipe_method="ZeroFill",
        sicherheitsstufe="H-2", schutzklasse=2, passes=1,
        start_time=datetime(2026, 11, 17, 9, 0, 0), end_time=datetime(2026, 11, 17, 9, 50, 0),
        verification_passed=True, sectors_checked=100, verification_hash="",
        company_name="Beispiel GmbH", company_address="", company_logo_path="",
        language="both", verify_method="full",
    )
    values.update(overrides)
    return CertificateData(**values)


# ── History result values ────────────────────────────────────────────

@pytest.mark.parametrize("status, value", [("ok", "OK"), ("limited", "LIMITED"),
                                           ("failed", "FAILED")])
def test_history_result_value(status, value):
    assert log.history_result_value(status) == value


@pytest.mark.parametrize("raw, status", [
    ("OK", "ok"), ("LIMITED", "limited"), ("FAILED", "failed"),
    ("SUCCESS", "ok"), ("success", "ok"), (" failed ", "failed"),
    ("", None), ("WHATEVER", None), (None, None),
])
def test_history_result_status_accepts_new_and_legacy_values(raw, status):
    assert log.history_result_status(raw) == status


# ── NIST category ────────────────────────────────────────────────────

def test_nist_category_overwrite_is_clear():
    assert nist_category(_cert_data()) == "Clear"
    assert nist_category(_cert_data(wipe_method="BSI-VSITR", passes=8)) == "Clear"


def test_nist_category_hardware_erase_is_purge():
    data = _cert_data(secure_erase_used=True, secure_erase_method="ata-secure-erase")
    assert nist_category(data) == "Purge"


def _capture_paragraphs(monkeypatch) -> list[str]:
    import cert.generator as gen

    captured: list[str] = []
    real = gen.Paragraph

    def spy(text, *args, **kwargs):
        captured.append(str(text))
        return real(text, *args, **kwargs)

    monkeypatch.setattr(gen, "Paragraph", spy)
    return captured


def _value_after(texts: list[str], label: str) -> str:
    index = texts.index(label)
    return texts[index + 1]


def test_certificate_shows_nist_clear_next_to_din(tmp_path, monkeypatch):
    texts = _capture_paragraphs(monkeypatch)
    generate_certificate(_cert_data(language="de"), str(tmp_path / "c.pdf"))
    assert _value_after(texts, "NIST SP 800-88") == "Clear"
    # Directly after the DIN 66399 level row.
    din = texts.index("DIN 66399 Sicherheitsstufe")
    assert texts[din + 2] == "NIST SP 800-88"


def test_certificate_shows_nist_purge_for_hardware_erase(tmp_path, monkeypatch):
    texts = _capture_paragraphs(monkeypatch)
    data = _cert_data(language="en", secure_erase_used=True,
                      secure_erase_method="ata-secure-erase")
    generate_certificate(data, str(tmp_path / "c.pdf"))
    assert _value_after(texts, "NIST SP 800-88") == "Purge"


# ── CLI, CSV and certificate agree ───────────────────────────────────

_CASES = {
    "ok": {},
    "limited": dict(bad_sector_count=3, bad_sector_bytes=3 * 512),
    "failed": dict(verification_passed=False, verify_error_count=5),
}


@pytest.mark.parametrize("status", ["ok", "limited", "failed"])
def test_cli_csv_and_certificate_agree(status):
    data = _cert_data(**_CASES[status])
    assert result_status(data) == status

    de, en = RESULT_LABELS[status]
    exit_code, line = cli.wipe_outcome(data)
    assert exit_code == {"ok": 0, "limited": 3, "failed": 1}[status]
    assert f"{de} / {en}" in line

    csv_value = log.history_result_value(result_status(data))
    assert log.history_result_status(csv_value) == status

    row = _build_result_row(data, _build_styles(), "both")
    cert_text = row._cellvalues[0][1].text
    assert cert_text == f"{de} / {en}"


# ── wipe: exit codes, result line, CSV column ────────────────────────

def _fake_device(**overrides):
    values = dict(
        drive_letter="E:", disk_letters=["E:"], device_id=r"\\.\PhysicalDrive99",
        model="Fake USB Stick", serial_number="FAKE-SERIAL-0001",
        capacity_bytes=1024 * 1024, filesystem="FAT32", connection_type="USB",
        is_removable=True, is_system_drive=False, is_internal=False,
        has_bitlocker=False, has_active_processes=False, partition_count=1,
        safe_to_wipe=True, friendly_name="Fake USB Stick (E:)",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _fake_wipe_result(*, verify_ok=True, bad=0):
    now = datetime.now()
    verify = SimpleNamespace(
        success=verify_ok, method="full", bytes_verified=1024 * 1024,
        expected_pattern="zeros", error_count=0 if verify_ok else 4,
        mismatch_offsets=[] if verify_ok else [0, 512, 1024, 2048],
        duration_seconds=0.5, sectors_checked=2048, sectors_matched=2048,
        sample_hash="a" * 64, timestamp=now,
    )
    return SimpleNamespace(
        method_name="ZeroFill", passes=1, start_time=now, end_time=now,
        bytes_written=1024 * 1024, success=True, error_message=None,
        verify_result=verify, zero_blank_appended=False,
        bad_sector_count=bad, bad_sector_bytes=bad * 512,
        bad_sector_offsets=[4096 * i for i in range(bad)],
    )


def _wipe(argv_extra, *, wipe_result=None, media="flash", extra=None):
    args = cli.build_parser().parse_args(
        ["wipe", "--device", "E:", "--operator", "Op", "--yes", *argv_extra]
    )
    method = MagicMock()
    method.name = "ZeroFill"
    method.passes = 1
    method.sicherheitsstufe = "H-2"
    method.execute.return_value = wipe_result or _fake_wipe_result()
    rows: list[dict] = []
    resolver = MagicMock(return_value=method)
    patches = [
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[_fake_device()]),
        patch.object(cli, "_resolve_wipe_method", resolver),
        patch.object(cli, "prepare_target"),
        patch.object(cli, "open_physical_drive", return_value=12345),
        patch.object(cli, "close_drive"),
        patch.object(cli, "lock_volume"),
        patch.object(cli, "unlock_volume"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(
            cli, "detect_hidden_areas",
            return_value=SimpleNamespace(supported=False, hpa_present=False, dco_present=False),
        ),
        patch.object(cli, "media_type_of", return_value=media),
        patch.object(cli, "get_next_cert_number", return_value=1),
        patch.object(cli, "generate_certificate", side_effect=lambda *a, **k: "x.pdf"),
        patch.object(cli, "log_wipe_to_csv", side_effect=lambda row: rows.append(row) or "ab" * 32),
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
    return SimpleNamespace(code=code, rows=rows, resolver=resolver, method=method, args=args)


def test_wipe_ok_exits_0_and_logs_ok(app_config, capsys):
    run = _wipe(["--method", "zero"])
    out = capsys.readouterr().out
    assert run.code == 0
    assert run.rows[0]["result"] == "OK"
    assert "Erfolgreich / Successful" in out


def test_wipe_with_bad_sectors_exits_3_and_logs_limited(app_config, capsys):
    run = _wipe(["--method", "zero"], wipe_result=_fake_wipe_result(bad=2))
    out = capsys.readouterr().out
    assert run.code == 3
    assert run.rows[0]["result"] == "LIMITED"
    assert "Mit Einschränkungen / With limitations" in out


def test_wipe_with_failed_verification_exits_1_and_logs_failed(app_config, capsys):
    run = _wipe(["--method", "zero"], wipe_result=_fake_wipe_result(verify_ok=False))
    out = capsys.readouterr().out
    assert run.code == 1
    assert run.rows[0]["result"] == "FAILED"
    assert "Fehlgeschlagen / Failed" in out


# ── wipe: recommended method and multi-pass note ─────────────────────

def test_method_and_verify_are_optional_with_full_default():
    args = cli.build_parser().parse_args(["wipe", "--device", "E:", "--operator", "Op"])
    assert args.method is None
    assert args.verify == "full"


def test_wipe_without_method_uses_recommendation(app_config, capsys):
    run = _wipe([], media="hdd")
    out = capsys.readouterr().out
    assert run.code == 0
    assert run.resolver.call_args.args[0].method == "zero"
    assert "Empfohlenes Verfahren / Recommended method" in out
    assert "Clear" in out
    assert run.method.execute.call_args.kwargs["verify_mode"] == "full"


def test_wipe_without_method_shows_recommendation_warnings(app_config, capsys):
    _wipe(["--schutzklasse", "3"], media="flash")
    out = capsys.readouterr().out
    assert "physisch vernichten" in out


@pytest.mark.parametrize("method", ["bsi", "standard"])
def test_multi_pass_method_prints_policy_note(app_config, capsys, method):
    _wipe(["--method", method])
    out = capsys.readouterr().out
    assert "nur wenn eine Vorgabe es verlangt" in out
    assert "only if a policy requires it" in out


def test_zero_method_has_no_policy_note(app_config, capsys):
    _wipe(["--method", "zero"])
    assert "nur wenn eine Vorgabe es verlangt" not in capsys.readouterr().out


# ── info ─────────────────────────────────────────────────────────────

def _run_info(argv, devices, media="flash"):
    opener = MagicMock()
    prober = MagicMock()
    eraser = MagicMock()
    with (
        patch.object(cli, "list_devices", return_value=devices),
        patch.object(cli, "media_type_of", return_value=media) as media_mock,
        patch.object(cli, "open_physical_drive", opener),
        patch.object(cli, "detect_hidden_areas", prober),
        patch.object(cli, "secure_erase", eraser),
        patch.object(cli, "recommend", wraps=cli.recommend) as rec,
    ):
        args = cli.build_parser().parse_args(["info", *argv])
        code = 0
        try:
            args.func(args)
        except SystemExit as exc:
            code = int(exc.code or 0)
    for never in (opener, prober, eraser):
        never.assert_not_called()
    return code, rec, media_mock


def test_info_prints_device_and_recommendation(app_config, capsys):
    dev = _fake_device(drive_letter="F:", disk_letters=["F:", "G:"], model="SanDisk 3.2Gen1")
    code, rec, _ = _run_info(["--device", "F:"], [dev])
    out = capsys.readouterr().out
    assert code == 0
    assert "SanDisk 3.2Gen1" in out
    assert "F:, G:" in out
    assert "USB-Stick/SD-Karte / USB stick/SD card" in out
    assert "Wechseldatenträger / Removable" in out
    assert "zero" in out
    assert "Vollständig / Full" in out
    assert "NIST SP 800-88:" in out and "Clear" in out
    assert "USB-Stick/SD-Karte: Überschreiben mit Nullen" in out
    # WMI only: hardware erase is never probed.
    assert rec.call_args.args[2] is False or rec.call_args.kwargs.get(
        "hardware_erase_available") is False


def test_info_warns_for_schutzklasse_3(app_config, capsys):
    dev = _fake_device(drive_letter="F:")
    _run_info(["--device", "F:", "--schutzklasse", "3"], [dev])
    out = capsys.readouterr().out
    assert "für Schutzklasse 3 physisch vernichten" in out
    assert "For protection class 3, destroy physically" in out


def test_info_by_disk_number(app_config, capsys):
    dev = _fake_device(drive_letter="", disk_letters=[], device_id=r"\\.\PhysicalDrive2",
                       is_internal=True, is_removable=False, connection_type="SATA")
    code, _, _ = _run_info(["--device", "2"], [dev], media="hdd")
    out = capsys.readouterr().out
    assert code == 0
    assert "Festplatte (HDD)" in out
    assert "Intern / Internal" in out


def test_info_system_disk_gives_no_recommendation(app_config, capsys):
    dev = _fake_device(drive_letter="C:", disk_letters=["C:"], is_system_drive=True,
                       is_internal=True, is_removable=False)
    code, rec, _ = _run_info(["--device", "C:"], [dev], media="ssd")
    out = capsys.readouterr().out
    assert code == 0
    assert "Systemplatte" in out
    rec.assert_not_called()


def test_info_unknown_device_exits_1(app_config):
    code, _, _ = _run_info(["--device", "Z:"], [_fake_device()])
    assert code == 1


# ── Defaults ─────────────────────────────────────────────────────────

def test_app_config_defaults_to_full_verify_and_zero_method():
    cfg = config.AppConfig()
    assert cfg.default_verify_mode == "full"
    assert cfg.default_wipe_method == "quick"


def test_app_config_loads_old_file_without_verify_mode(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"operator_name": "X", "default_wipe_method": "bsi"}),
                    encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    cfg = config.AppConfig.load()
    assert cfg.default_verify_mode == "full"
    assert cfg.default_wipe_method == "bsi"


# ── PDF attachment (G5) ──────────────────────────────────────────────

def _embedded_json(pdf: bytes) -> dict:
    match = re.search(
        rb"/Type /EmbeddedFile.*?stream\r?\n(.*?)endstream", pdf, re.DOTALL,
    )
    assert match, "no embedded file stream"
    header = pdf[max(0, match.start() - 400):match.start(1)]
    raw = match.group(1).rstrip(b"\r\n")
    if b"FlateDecode" in header:
        raw = zlib.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def test_signed_certificate_carries_payload_attachment(tmp_path):
    key = SigningKey.from_seed(SEED)
    data = _cert_data()
    path = generate_certificate(data, str(tmp_path / "c.pdf"), signing_key=key,
                                history_hash="cd" * 32)
    pdf = open(path, "rb").read()

    assert b"/EmbeddedFiles" in pdf
    assert b"/Filespec" in pdf
    assert ATTACHMENT_NAME.encode() in pdf

    attached = _embedded_json(pdf)
    token = extract_token_from_pdf(pdf)
    assert attached["token"] == token
    expected = build_payload(data, "cd" * 32)
    expected["k"] = attached["payload"]["k"]
    assert attached["payload"] == expected
    # The attached payload is exactly what the token signs.
    signed = json.loads(b64url_decode(token.split(".")[0]))
    assert attached["payload"] == signed


def test_unsigned_certificate_has_no_attachment(tmp_path):
    path = generate_certificate(_cert_data(), str(tmp_path / "c.pdf"))
    assert b"/EmbeddedFiles" not in open(path, "rb").read()


def test_certificate_with_attachment_opens_in_a_pdf_reader(tmp_path):
    pytest.importorskip("PySide6.QtPdf")
    from PySide6.QtPdf import QPdfDocument

    key = SigningKey.from_seed(SEED)
    path = generate_certificate(_cert_data(), str(tmp_path / "c.pdf"), signing_key=key)
    doc = QPdfDocument(None)
    assert doc.load(path) == QPdfDocument.Error.None_
    assert doc.pageCount() >= 1


# ── Readers accept legacy and new values ─────────────────────────────

def test_history_table_shows_new_and_legacy_results(capsys):
    for value in ("SUCCESS", "LIMITED", "FAILED", "OK"):
        log.log_wipe_to_csv({"date": "2026-11-17 10:00:00", "result": value,
                             "cert_number": "1"})
    cli.cmd_history(SimpleNamespace(export=None, verify=False))
    out = capsys.readouterr().out
    table = out.split("=" * 10, 1)[1]
    assert table.count("OK") >= 2  # legacy SUCCESS shown as OK
    assert "LIMITED" in table and "FAILED" in table
    assert "SUCCESS" not in table


def test_certificates_page_result_chip():
    from gui.pages.certificates_page import result_chip

    assert result_chip("OK") == ("OK", "accent")
    assert result_chip("SUCCESS") == ("OK", "accent")
    assert result_chip("LIMITED") == ("EINGESCHRÄNKT", "warning")
    assert result_chip("FAILED") == ("FEHLGESCHLAGEN", "danger")
    assert result_chip("odd") == ("odd", "neutral")


def test_recommendation_dataclass_is_what_info_uses():
    # Guard: the CLI imports the real recommend function.
    from wipe import recommend as rec_module
    assert cli.recommend is rec_module.recommend
    assert isinstance(
        cli.recommend(_fake_device(), "hdd", False, 2), Recommendation
    )
