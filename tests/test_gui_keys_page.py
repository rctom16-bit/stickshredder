"""The Schlüssel page: own fingerprint, key card, trusted keys."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel  # noqa: E402

from tests.gui_fixtures import (  # noqa: E402,F401
    app_config,
    gui_reset,
    isolated_profile,
)


@pytest.fixture()
def own_key(isolated_profile):
    import core.config as cfg

    return cfg.load_signing_key()  # created on first use and trusted as "own key"


@pytest.fixture()
def page(qtbot, app_config, gui_reset, own_key):
    from gui.pages.keys_page import KeysPage
    from gui.theme import ThemeManager

    ThemeManager(gui_reset, "dark").apply()
    app_config.company.name = "ACME IT GmbH"
    widget = KeysPage(app_config)
    qtbot.addWidget(widget)
    widget.resize(1192, 800)
    widget.show()
    return widget


def _keycard_pdf(tmp_path, seed: int = 1):
    from cert.signing import SigningKey, b64url

    other = SigningKey.from_seed(bytes([seed]) * 32)
    path = tmp_path / f"keycard-{seed}.pdf"
    path.write_bytes(b"%PDF-1.4\n/Keywords (stickshredder-key:" +
                     b64url(other.public_key_bytes()).encode() + b")\n")
    return path, other


def _texts(widget) -> list[str]:
    texts = [w.text() for w in widget.findChildren(QLabel)]
    texts += [w.text() for w in widget.findChildren(QAbstractButton)]
    return texts


# -- Own key -------------------------------------------------------------------


def test_shows_the_own_fingerprint_grouped_in_monospace(page, own_key):
    fingerprint = own_key.fingerprint()
    groups = fingerprint.split("-")
    shown = page.fingerprint_label.text()
    assert "-".join(groups[:4]) in shown
    assert "-".join(groups[4:]) in shown
    assert "\n" in shown  # two lines of four groups
    assert page.fingerprint_label.property("role") == "fingerprint"


def test_copy_button_puts_the_fingerprint_on_the_clipboard(qtbot, page, own_key):
    qtbot.mouseClick(page.copy_button, Qt.MouseButton.LeftButton)
    assert QApplication.clipboard().text() == own_key.fingerprint()
    assert page.status_label.text() == "Fingerabdruck kopiert"


def test_shows_since_when_the_key_exists(page):
    from datetime import date

    assert date.today().strftime("%d.%m.%Y") in page.created_label.text()


def test_never_shows_the_private_key(page, own_key):
    from cert.signing import b64url

    seed = own_key._seed()
    secret_texts = {b64url(seed), seed.hex(), seed.hex().upper()}
    for text in _texts(page):
        for secret in secret_texts:
            assert secret not in text


def test_explains_what_the_key_card_is_for(page):
    assert "Prüfer" in page.keycard_text.text()


# -- Key card ------------------------------------------------------------------


def test_keycard_button_creates_the_card_and_opens_it(qtbot, page, own_key, app_config,
                                                      monkeypatch):
    from pathlib import Path

    import gui.pages.keys_page as keys_page

    calls, opened = [], []

    def fake_generate(key, company, output_path, language="de"):
        calls.append((key.fingerprint(), company, Path(output_path), language))
        return Path(output_path)

    monkeypatch.setattr(keys_page, "generate_keycard", fake_generate)
    monkeypatch.setattr(keys_page, "open_path", lambda p: opened.append(p) or True)
    app_config.cert_language = "both"
    qtbot.mouseClick(page.keycard_button, Qt.MouseButton.LeftButton)
    expected = Path(app_config.cert_output_dir) / (
        f"StickShredder-Schluesselkarte_{own_key.fingerprint()[:9]}.pdf")
    assert calls == [(own_key.fingerprint(), "ACME IT GmbH", expected, "both")]
    assert opened == [str(expected)]
    assert page.status_label.property("tone") == "ok"


def test_keycard_failure_is_shown_not_opened(page, monkeypatch):
    import gui.pages.keys_page as keys_page

    opened = []

    def broken(*_a, **_k):
        raise OSError("Datenträger voll")

    monkeypatch.setattr(keys_page, "generate_keycard", broken)
    monkeypatch.setattr(keys_page, "open_path", lambda p: opened.append(p) or True)
    assert page.create_keycard() is None
    assert opened == []
    assert "Datenträger voll" in page.status_label.text()
    assert page.status_label.property("tone") == "danger"


def test_missing_company_name_is_pointed_out(page, app_config):
    assert not page.company_hint.isVisible()
    app_config.company.name = ""
    page.refresh()
    assert page.company_hint.isVisible()


def test_unreadable_key_disables_the_key_card(qtbot, app_config, gui_reset, monkeypatch):
    import core.config as cfg
    from cert.signing import SigningKeyError
    from gui.pages.keys_page import KeysPage

    def broken():
        raise SigningKeyError("kaputt")

    monkeypatch.setattr(cfg, "load_signing_key", broken)
    widget = KeysPage(app_config)
    qtbot.addWidget(widget)
    assert not widget.keycard_button.isEnabled()
    assert not widget.copy_button.isEnabled()
    assert "kaputt" in widget.fingerprint_label.text()


# -- Trusted keys --------------------------------------------------------------


def test_own_key_is_listed_as_trusted(page, own_key):
    rows = page.trusted_rows()
    assert [r.fingerprint for r in rows] == [own_key.fingerprint()]
    assert rows[0].own_chip.isVisible()


def test_add_a_key_from_a_key_card(page, tmp_path):
    import core.config as cfg
    from cert.verify_cert import TrustStore

    path, other = _keycard_pdf(tmp_path)
    assert page.add_from_keycard(str(path), name="Prüfer Müller") == other.fingerprint()
    names = {r.fingerprint: r.name_label.text() for r in page.trusted_rows()}
    assert names[other.fingerprint()] == "Prüfer Müller"
    assert TrustStore.load(cfg.TRUST_FILE).as_dict()[other.fingerprint()] == "Prüfer Müller"
    assert other.fingerprint() in page.status_label.text()


def test_add_asks_for_a_name_and_cancel_adds_nothing(page, tmp_path, monkeypatch):
    import gui.pages.keys_page as keys_page

    path, _other = _keycard_pdf(tmp_path)
    monkeypatch.setattr(keys_page, "ask_text", lambda *a, **k: None)
    assert page.add_from_keycard(str(path)) is None
    assert len(page.trusted_rows()) == 1


def test_a_pdf_without_a_key_is_refused(page, tmp_path):
    bogus = tmp_path / "invoice.pdf"
    bogus.write_bytes(b"%PDF-1.4 no key here")
    assert page.add_from_keycard(str(bogus), name="X") is None
    assert len(page.trusted_rows()) == 1
    assert page.status_label.property("tone") == "danger"


def test_rename_a_trusted_key(page, tmp_path):
    import core.config as cfg
    from cert.verify_cert import TrustStore

    path, other = _keycard_pdf(tmp_path)
    page.add_from_keycard(str(path), name="Alt")
    assert page.rename_key(other.fingerprint(), "Neu") is True
    assert TrustStore.load(cfg.TRUST_FILE).as_dict()[other.fingerprint()] == "Neu"
    names = {r.fingerprint: r.name_label.text() for r in page.trusted_rows()}
    assert names[other.fingerprint()] == "Neu"


def test_remove_asks_first(page, tmp_path, monkeypatch):
    import core.config as cfg
    from cert.verify_cert import TrustStore

    import gui.pages.keys_page as keys_page

    path, other = _keycard_pdf(tmp_path)
    page.add_from_keycard(str(path), name="Weg damit")
    asked = []
    monkeypatch.setattr(keys_page, "ask_yes_no",
                        lambda parent, title, text: asked.append(text) or False)
    assert page.remove_key(other.fingerprint()) is False
    assert other.fingerprint() in TrustStore.load(cfg.TRUST_FILE).as_dict()
    assert "Weg damit" in asked[0]

    monkeypatch.setattr(keys_page, "ask_yes_no", lambda *a: True)
    assert page.remove_key(other.fingerprint()) is True
    assert other.fingerprint() not in TrustStore.load(cfg.TRUST_FILE).as_dict()
    assert other.fingerprint() not in [r.fingerprint for r in page.trusted_rows()]


def test_damaged_trust_list_is_reported_and_kept(qtbot, app_config, gui_reset, own_key):
    import core.config as cfg
    from gui.pages.keys_page import KeysPage

    cfg.TRUST_FILE.write_text("{broken", encoding="utf-8")
    widget = KeysPage(app_config)
    qtbot.addWidget(widget)
    assert widget.trusted_rows() == []
    assert not widget.add_button.isEnabled()
    assert widget.trust_error_label.text()
    assert cfg.TRUST_FILE.read_text(encoding="utf-8") == "{broken"


def test_own_key_name_is_shown_in_the_ui_language(page):
    from gui import i18n

    assert page.trusted_rows()[0].name_label.text() == "Eigener Schlüssel"
    i18n.set_language("en")
    page.retranslate()
    assert page.trusted_rows()[0].name_label.text() == "Own key"
