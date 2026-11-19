"""The Zertifikate page: history table, search, PDF/folder/CSV, chain status, verify."""

from __future__ import annotations

import csv

import pytest

pytest.importorskip("PySide6")

from tests.gui_fixtures import (  # noqa: E402,F401
    app_config,
    gui_reset,
    isolated_profile,
)


def _row(n: int, model: str, serial: str, result: str, date: str) -> dict:
    return {
        "date": date,
        "device_model": model,
        "serial_number": serial,
        "capacity_bytes": 61_933_428_736,
        "method": "Zero Fill (1 pass)",
        "passes": 1,
        "operator": "Robin",
        "start_time": "",
        "end_time": "",
        "duration_seconds": 60,
        "result": result,
        "verification": "PASSED",
        "cert_number": n,
        "bad_sectors": 0,
    }


@pytest.fixture()
def history(isolated_profile):
    from core.log import log_wipe_to_csv

    log_wipe_to_csv(_row(1, "SanDisk 3.2Gen1", "DEMO00000001", "OK", "2026-11-16 09:10:00"))
    log_wipe_to_csv(_row(2, "Samsung SSD 870 EVO", "S6PNNS0T123456", "LIMITED",
                         "2026-11-17 10:20:00"))
    log_wipe_to_csv(_row(3, "Intenso Basic", "AA11BB22", "FAILED", "2026-11-18 11:30:00"))
    return isolated_profile / "wipe_history.csv"


@pytest.fixture()
def page(qtbot, app_config, gui_reset):
    from gui.pages.certificates_page import CertificatesPage
    from gui.theme import ThemeManager

    ThemeManager(gui_reset, "dark").apply()
    widget = CertificatesPage(app_config)
    qtbot.addWidget(widget)
    widget.resize(1192, 800)
    widget.show()
    return widget


# -- Table ---------------------------------------------------------------------


def test_empty_history_shows_the_empty_state(page):
    page.refresh()
    assert page.empty_card.isVisible()
    assert not page.table.isVisible()
    assert not page.export_button.isEnabled()
    assert page.empty_title.text() == "Noch keine Löschungen"


def test_filled_history_lists_newest_first_with_result_chips(page, history):
    page.refresh()
    assert not page.empty_card.isVisible()
    assert page.table.isVisible()
    assert page.table.rowCount() == 3
    assert [page.cell_text(r, "device") for r in range(3)] == [
        "Intenso Basic", "Samsung SSD 870 EVO", "SanDisk 3.2Gen1"]
    assert [page.chip_at(r) for r in range(3)] == [
        ("FEHLGESCHLAGEN", "danger"), ("EINGESCHRÄNKT", "warning"), ("OK", "accent")]
    assert page.cell_text(0, "date") == "18.11.2026 11:30"
    assert page.cell_text(0, "cert") == "SS-000003"
    assert page.cell_text(1, "serial") == "S6PNNS0T123456"
    assert page.count_label.text() == "3 Löschungen"


@pytest.mark.parametrize("raw, de, en, tone", [
    ("OK", "OK", "OK", "accent"),
    ("SUCCESS", "OK", "OK", "accent"),          # before 2.0
    ("LIMITED", "EINGESCHRÄNKT", "LIMITED", "warning"),
    ("FAILED", "FEHLGESCHLAGEN", "FAILED", "danger"),
    ("odd", "odd", "odd", "neutral"),
])
def test_result_chip_maps_new_and_legacy_values(gui_reset, raw, de, en, tone):
    from gui import i18n
    from gui.pages.certificates_page import result_chip

    assert result_chip(raw) == (de, tone)
    i18n.set_language("en")
    assert result_chip(raw) == (en, tone)


def test_legacy_rows_show_ok_and_failed(page, isolated_profile):
    from core.log import CSV_HEADERS

    path = isolated_profile / "wipe_history.csv"
    old = [h for h in CSV_HEADERS if h not in ("prev_hash", "entry_hash")]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=old, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(_row(1, "Old Stick", "X1", "SUCCESS", "2025-06-01 08:00:00"))
        writer.writerow(_row(2, "Old Disk", "X2", "FAILED", "2025-06-02 08:00:00"))
    page.refresh()
    assert [page.chip_at(r)[0] for r in range(2)] == ["FEHLGESCHLAGEN", "OK"]


def test_search_filters_the_rows(page, history):
    page.refresh()
    page.search_edit.setText("samsung")
    assert page.visible_rows() == [1]
    page.search_edit.setText("SS-000003")
    assert page.visible_rows() == [0]
    page.search_edit.setText("")
    assert page.visible_rows() == [0, 1, 2]


# -- Chain status --------------------------------------------------------------


def test_chain_status_intact(page, history):
    page.refresh()
    assert page.chain_label.isVisible()
    assert page.chain_label.text() == "Verlauf unverändert · 3 Einträge geprüft"
    assert page.chain_label.property("tone") == "ok"


def test_chain_status_broken_names_the_entry(page, history):
    text = history.read_text(encoding="utf-8")
    history.write_text(text.replace("S6PNNS0T123456", "S6PNNS0T999999"), encoding="utf-8")
    page.refresh()
    assert page.chain_label.text().startswith("Verlauf verändert ab Eintrag 2")
    assert "Eintrag wurde verändert" in page.chain_label.text()
    assert "Entry was changed" not in page.chain_label.text()
    assert page.chain_label.property("tone") == "danger"


# -- Actions -------------------------------------------------------------------


def test_open_pdf_opens_the_matching_certificate(page, history, app_config, monkeypatch, tmp_path):
    import gui.pages.certificates_page as certificates_page

    opened = []
    monkeypatch.setattr(certificates_page, "open_path", lambda p: opened.append(p) or True)
    folder = tmp_path / "certs"
    folder.mkdir()
    pdf = folder / "SS-000002_S6PNNS0T123456_20261117.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    page.refresh()
    page.table.selectRow(1)
    assert page.open_pdf_button.isEnabled()
    assert page.open_selected_pdf() is True
    assert opened == [str(pdf)]


def test_missing_pdf_is_reported_not_opened(page, history, monkeypatch):
    import gui.pages.certificates_page as certificates_page

    opened = []
    monkeypatch.setattr(certificates_page, "open_path", lambda p: opened.append(p) or True)
    page.refresh()
    page.table.selectRow(0)
    assert page.open_selected_pdf() is False
    assert opened == []
    assert "SS-000003" in page.status_label.text()
    assert page.status_label.property("tone") == "danger"


def test_open_folder_opens_the_certificate_folder(page, app_config, monkeypatch):
    import gui.pages.certificates_page as certificates_page

    opened = []
    monkeypatch.setattr(certificates_page, "open_path", lambda p: opened.append(p) or True)
    page.open_folder()
    assert opened == [app_config.cert_output_dir]


def test_export_csv_writes_the_visible_rows(page, history, tmp_path):
    from core.log import CSV_HEADERS

    page.refresh()
    page.search_edit.setText("SanDisk")
    target = tmp_path / "export.csv"
    assert page.export_csv(str(target)) == 1
    with open(target, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == CSV_HEADERS
    assert [r["serial_number"] for r in rows] == ["DEMO00000001"]
    assert "1" in page.status_label.text()


# -- Verify a certificate ------------------------------------------------------


def _outcome(status: str):
    from cert.verify_cert import VerifyOutcome

    payload = {"n": 42, "t": "2026-11-18T10:00:00Z", "dm": "SanDisk 3.2Gen1",
               "ds": "DEMO00000001", "r": "ok"}
    reasons = {
        "trusted": "Echt — Schlüssel bekannt (ACME) / Genuine — key known (ACME)",
        "unknown_key": "Signatur gültig, Schlüssel unbekannt / Signature valid, key unknown",
        "invalid": "Signatur ungültig — Zertifikat wurde verändert / "
                   "Signature invalid — certificate was altered",
    }
    if status == "invalid":
        return VerifyOutcome("invalid", None, None, reasons[status])
    return VerifyOutcome(status, payload, "65B6-0673-D6ED-884B-1111-2222-3333-4444",
                         reasons[status])


@pytest.mark.parametrize("status, title, tone", [
    ("trusted", "Echt, von einem vertrauten Schlüssel", "ok"),
    ("unknown_key", "Signatur gültig, Schlüssel unbekannt", "limited"),
    ("invalid", "Ungültig oder verändert", "failed"),
])
def test_verify_shows_the_three_levels_in_plain_language(page, monkeypatch, tmp_path,
                                                         status, title, tone):
    import gui.pages.certificates_page as certificates_page

    monkeypatch.setattr(certificates_page, "check_certificate", lambda path: _outcome(status))
    pdf = tmp_path / "SS-000042.pdf"
    pdf.write_bytes(b"%PDF")
    page.verify_certificate(str(pdf))
    assert page.verify_card.isVisible()
    assert page.verify_title.text() == title
    assert page.verify_card.property("tone") == tone
    details = page.verify_text.text()
    if status == "invalid":
        assert "Zertifikat wurde verändert" in details
        assert "certificate was altered" not in details
    else:
        assert "SS-000042" in page.verify_details.text()
        assert "65B6-0673" in page.verify_details.text()
    if status == "unknown_key":
        assert "Schlüsselkarte" in details


def test_verify_in_english(page, monkeypatch, tmp_path):
    from gui import i18n

    import gui.pages.certificates_page as certificates_page

    monkeypatch.setattr(certificates_page, "check_certificate", lambda p: _outcome("invalid"))
    i18n.set_language("en")
    page.retranslate()
    page.verify_certificate(str(tmp_path / "x.pdf"))
    assert page.verify_title.text() == "Invalid or altered"
    assert "certificate was altered" in page.verify_text.text()


def test_verify_without_a_choice_does_nothing(page, monkeypatch):
    import gui.pages.certificates_page as certificates_page

    monkeypatch.setattr(certificates_page, "pick_pdf", lambda *a: "")
    assert page.verify_certificate() is None
    assert not page.verify_card.isVisible()


def test_check_certificate_uses_the_signature_and_the_trust_list(isolated_profile, tmp_path):
    import core.config as cfg
    from cert.signing import SigningKey
    from cert.verify_cert import TrustStore
    from gui.pages.certificates_page import check_certificate

    key = SigningKey.from_seed(bytes(range(32)))
    token = key.sign_payload({"v": 1, "n": 7, "r": "ok"})
    pdf = tmp_path / "SS-000007.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% stickshredder-token:" + token.encode() + b"\n")

    assert check_certificate(str(pdf)).status == "unknown_key"
    TrustStore.load(cfg.TRUST_FILE).add(key.public_key_bytes(), "ACME")
    assert check_certificate(str(pdf)).status == "trusted"

    data, sig = token.split(".")
    tampered = tmp_path / "tampered.pdf"
    tampered.write_bytes(b"stickshredder-token:" + (data[:-2] + "AA").encode() + b"." +
                         sig.encode())
    assert check_certificate(str(tampered)).status == "invalid"

    plain = tmp_path / "plain.pdf"
    plain.write_bytes(b"%PDF-1.4 nothing here")
    outcome = check_certificate(str(plain))
    assert outcome.status == "invalid"
    assert "Kein Prüfcode" in outcome.reason

    assert check_certificate(str(tmp_path / "missing.pdf")).status == "invalid"
