"""A serial the device does not report, end to end (2.0 polish).

Real case (18.11.2026): an NVMe SSD in a Realtek RTL9210B USB enclosure
reports "0000000000000000". The GUI, the certificate PDF and the history
say "not reported by the device"; the certificate file is named
"..._noserial_..."; the signed payload keeps the raw value. All drive
access is faked.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

import cli
from cert.generator import generate_certificate
from cert.payload import build_payload
from tests.test_certificate import _make_cert_data, _paragraph_texts
from tests.test_cli import _fake_device, _fake_wipe_result

ZEROS = "0000000000000000"


# ── Certificate PDF ────────────────────────────────────────────────────

@pytest.mark.parametrize("lang, shown", [
    ("de", "vom Gerät nicht geliefert"),
    ("en", "not reported by the device"),
    ("both", "vom Gerät nicht geliefert / not reported by the device"),
])
def test_pdf_says_not_reported_and_payload_keeps_raw_serial(tmp_path, lang, shown):
    data = _make_cert_data(serial_number=ZEROS, language=lang)
    built: list = []
    with patch("cert.generator.audit_log"), \
         patch("cert.generator.SimpleDocTemplate.build",
               lambda self, elements, **kw: built.extend(elements)):
        generate_certificate(data, str(tmp_path / "c.pdf"))
    text = _paragraph_texts(built)
    assert shown in text
    assert ZEROS not in text
    assert build_payload(data)["ds"] == ZEROS


def test_pdf_keeps_a_real_serial(tmp_path):
    data = _make_cert_data(serial_number="ABC123DEF", language="en")
    built: list = []
    with patch("cert.generator.audit_log"), \
         patch("cert.generator.SimpleDocTemplate.build",
               lambda self, elements, **kw: built.extend(elements)):
        generate_certificate(data, str(tmp_path / "c.pdf"))
    assert "ABC123DEF" in _paragraph_texts(built)


# ── CLI wipe: file name, history, signed data ──────────────────────────

def test_cli_wipe_names_the_file_noserial_and_writes_the_text_to_history():
    device = _fake_device()
    device.serial_number = ZEROS
    args = cli.build_parser().parse_args([
        "wipe", "--device", "E:", "--method", "zero", "--operator", "Test",
        "--verify", "none", "--yes",
    ])
    method = MagicMock()
    method.name = "ZeroFill"
    method.passes = 1
    method.sicherheitsstufe = "1-2"
    method.execute.return_value = _fake_wipe_result()
    cert = MagicMock(return_value="C:/tmp/fake.pdf")
    history = MagicMock(return_value="h" * 64)
    no_hidden = MagicMock(supported=False, hpa_present=False, dco_present=False,
                          hpa_hidden_sectors=0, dco_hidden_sectors=0)
    with (
        patch.object(cli, "is_admin", return_value=True),
        patch.object(cli, "list_devices", return_value=[device]),
        patch.object(cli, "_resolve_wipe_method", return_value=method),
        patch.object(cli, "prepare_target", return_value=[]),
        patch.object(cli, "release_volumes"),
        patch.object(cli, "open_physical_drive", return_value=1),
        patch.object(cli, "close_drive"),
        patch.object(cli, "get_drive_size", return_value=1024 * 1024),
        patch.object(cli, "detect_hidden_areas", return_value=no_hidden),
        patch.object(cli, "get_next_cert_number", return_value=7),
        patch.object(cli, "generate_certificate", cert),
        patch.object(cli, "log_wipe_to_csv", history),
        patch.object(cli, "audit_log"),
    ):
        cli.cmd_wipe(args)
    path = cert.call_args.args[1]
    assert "_noserial_" in path and ZEROS not in path
    assert history.call_args.args[0]["serial_number"] == "not reported by the device"
    assert cert.call_args.args[0].serial_number == ZEROS


def test_cli_history_shows_the_text_for_old_zero_rows():
    assert cli._short_serial(ZEROS) == "not reported"
    assert cli._short_serial("SN1") == "SN1"


# ── GUI ────────────────────────────────────────────────────────────────

def test_gui_serial_label_per_language():
    pytest.importorskip("PySide6")
    from gui import i18n

    before = i18n.language()
    try:
        i18n.set_language("de")
        assert i18n.serial_label(ZEROS) == "SN vom Gerät nicht geliefert"
        assert i18n.serial_label("") == "SN vom Gerät nicht geliefert"
        i18n.set_language("en")
        assert i18n.serial_label("   ") == "SN not reported by the device"
        assert i18n.serial_label("ABC") == "SN ABC"
    finally:
        i18n.set_language(before)


def test_certificate_lookup_finds_new_and_old_file_names(tmp_path):
    pytest.importorskip("PySide6")
    from gui.pages.certificates_page import find_certificate_pdf

    old = tmp_path / f"SS-000003_{ZEROS}_20261118.pdf"
    new = tmp_path / "SS-000004_noserial_20261119.pdf"
    other = tmp_path / "SS-000004_AAA_20261119.pdf"
    for f in (old, new, other):
        f.write_bytes(b"%PDF")
    assert find_certificate_pdf(tmp_path, 3, ZEROS) == old
    assert find_certificate_pdf(tmp_path, 4, "not reported by the device") == new
    assert find_certificate_pdf(tmp_path, 4, "AAA") == other


def test_worker_names_the_file_noserial_and_writes_the_text_to_history(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from core.config import AppConfig
    from gui.wipe_worker import WipeWorker
    from tests.test_gui_verify import (
        _gui_lock_test_device, _gui_lock_test_method, _ok_wipe_result,
    )

    cfg = AppConfig()
    cfg.cert_output_dir = str(tmp_path / "certs")
    device = replace(_gui_lock_test_device(), serial_number=ZEROS)
    cert = MagicMock(return_value="c.pdf")
    history = MagicMock(return_value="h" * 64)
    no_hidden = MagicMock(supported=False, hpa_present=False, dco_present=False,
                          hpa_hidden_sectors=0, dco_hidden_sectors=0)
    with patch("gui.wipe_worker.prepare_target", return_value=[]), \
         patch("gui.wipe_worker.release_volumes"), \
         patch("gui.wipe_worker.open_physical_drive", return_value=1), \
         patch("gui.wipe_worker.close_drive"), \
         patch("gui.wipe_worker.get_drive_size", return_value=1024**2), \
         patch("gui.wipe_worker.detect_hidden_areas", return_value=no_hidden), \
         patch("gui.wipe_worker.generate_certificate", cert), \
         patch("gui.wipe_worker.log_wipe_to_csv", history), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=1), \
         patch("gui.wipe_worker.audit_log"):
        worker = WipeWorker(devices=[device],
                            wipe_method=_gui_lock_test_method(
                                lambda *a, **k: _ok_wipe_result()),
                            config=cfg, verify_mode="none")
        with qtbot.waitSignal(worker.all_completed, timeout=5000):
            worker.start()
    path = cert.call_args.args[1]
    assert "_noserial_" in path and ZEROS not in path
    assert history.call_args.args[0]["serial_number"] == "not reported by the device"
    assert cert.call_args.args[0].serial_number == ZEROS
