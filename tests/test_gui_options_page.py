"""The Optionen page: settings that used to live in the settings dialog, plus
appearance, demo mode and the About section."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from tests.gui_fixtures import (  # noqa: E402,F401
    app_config,
    gui_reset,
    isolated_profile,
)


@pytest.fixture()
def config(app_config):
    app_config.company.name = "ACME IT GmbH"
    app_config.company.address = "Musterstraße 1\n90402 Nürnberg"
    app_config.company.logo_path = "C:/logos/acme.png"
    app_config.operator_name = "Robin"
    app_config.default_wipe_method = "standard"
    app_config.default_verify_mode = "sample"
    app_config.default_schutzklasse = 3
    app_config.cert_language = "both"
    return app_config


@pytest.fixture()
def page(qtbot, config, gui_reset):
    from gui.pages.options_page import OptionsPage
    from gui.theme import ThemeManager

    ThemeManager(gui_reset, "dark").apply()
    widget = OptionsPage(config)
    qtbot.addWidget(widget)
    widget.resize(1192, 800)
    widget.show()
    return widget


def test_fields_show_the_stored_settings(page, config):
    assert page.company_edit.text() == "ACME IT GmbH"
    assert page.address_edit.toPlainText() == "Musterstraße 1\n90402 Nürnberg"
    assert page.logo_edit.text() == "C:/logos/acme.png"
    assert page.cert_dir_edit.text() == config.cert_output_dir
    assert page.cert_language_combo.currentData() == "both"
    assert page.operator_edit.text() == "Robin"
    assert page.method_combo.currentData() == "standard"
    assert page.verify_combo.currentData() == "sample"
    assert page.sk_combo.currentData() == 3


def test_save_round_trip(qtbot, page, config, isolated_profile, tmp_path):
    from core.config import AppConfig

    page.company_edit.setText("Neue Firma AG")
    page.address_edit.setPlainText("Hauptstraße 2")
    page.logo_edit.setText("")
    page.cert_dir_edit.setText(str(tmp_path / "zertifikate"))
    page.cert_language_combo.setCurrentIndex(page.cert_language_combo.findData("en"))
    page.operator_edit.setText("Kim")
    page.method_combo.setCurrentIndex(page.method_combo.findData("bsi"))
    page.verify_combo.setCurrentIndex(page.verify_combo.findData("full"))
    page.sk_combo.setCurrentIndex(page.sk_combo.findData(1))

    with qtbot.waitSignal(page.saved, timeout=1000):
        qtbot.mouseClick(page.save_button, Qt.MouseButton.LeftButton)

    assert (config.company.name, config.company.address, config.company.logo_path) == (
        "Neue Firma AG", "Hauptstraße 2", "")
    assert config.cert_output_dir == str(tmp_path / "zertifikate")
    assert (config.cert_language, config.operator_name) == ("en", "Kim")
    assert (config.default_wipe_method, config.default_verify_mode,
            config.default_schutzklasse) == ("bsi", "full", 1)
    stored = json.loads((isolated_profile / "config.json").read_text(encoding="utf-8"))
    assert stored["company"]["name"] == "Neue Firma AG"
    reloaded = AppConfig.load()
    assert (reloaded.default_wipe_method, reloaded.default_verify_mode) == ("bsi", "full")
    assert page.status_label.text() == "Gespeichert"


def test_reset_brings_back_the_stored_values(page):
    page.company_edit.setText("Tippfehler")
    page.reset_button.click()
    assert page.company_edit.text() == "ACME IT GmbH"


def test_address_without_company_is_refused(page, config, isolated_profile):
    page.company_edit.setText("")
    page.save_button.click()
    assert config.company.name == "ACME IT GmbH"
    assert not (isolated_profile / "config.json").exists()
    assert "Firmenname" in page.status_label.text()
    assert page.status_label.property("tone") == "danger"


def test_one_letter_operator_is_refused(page, config):
    page.operator_edit.setText("R")
    page.save_button.click()
    assert config.operator_name == "Robin"
    assert page.status_label.property("tone") == "danger"


def test_empty_certificate_folder_falls_back_to_the_default(page, config, isolated_profile):
    import core.config as cfg

    page.cert_dir_edit.setText("")
    page.save_button.click()
    assert config.cert_output_dir == str(cfg.DEFAULT_CERT_OUTPUT)


def test_browse_buttons_fill_the_fields(page, monkeypatch, tmp_path):
    import gui.pages.options_page as options_page

    monkeypatch.setattr(options_page, "pick_image", lambda *a: "D:/logo.png")
    monkeypatch.setattr(options_page, "pick_folder", lambda *a: str(tmp_path))
    page.logo_browse_button.click()
    page.cert_dir_browse_button.click()
    assert page.logo_edit.text() == "D:/logo.png"
    assert page.cert_dir_edit.text() == str(tmp_path)
    page.logo_clear_button.click()
    assert page.logo_edit.text() == ""


# -- Appearance, demo, about ---------------------------------------------------


def test_theme_and_language_choices_ask_the_window(qtbot, page):
    with qtbot.waitSignal(page.theme_requested, timeout=1000) as theme:
        page.theme_buttons["light"].click()
    assert theme.args == ["light"]
    with qtbot.waitSignal(page.language_requested, timeout=1000) as lang:
        page.language_buttons["en"].click()
    assert lang.args == ["en"]


def test_appearance_follows_the_window(page):
    page.sync_appearance("system", "en")
    assert page.theme_buttons["system"].isChecked()
    assert page.language_buttons["en"].isChecked()
    page.sync_appearance("dark", "de")
    assert page.theme_buttons["dark"].isChecked()
    assert page.language_buttons["de"].isChecked()


def test_demo_button_asks_for_the_demo(qtbot, page):
    with qtbot.waitSignal(page.demo_requested, timeout=1000):
        page.demo_button.click()


def test_about_shows_version_licence_and_github(page, monkeypatch):
    import gui.pages.options_page as options_page
    from core.config import APP_VERSION

    opened = []
    monkeypatch.setattr(options_page, "open_url", lambda url: opened.append(url))
    assert APP_VERSION in page.version_label.text()
    assert "MIT" in page.about_text.text()
    page.github_button.click()
    assert opened == ["https://github.com/rctom16-bit/stickshredder"]


def test_page_switches_language_live(page):
    from gui import i18n

    assert page.save_button.text() == "Speichern"
    i18n.set_language("en")
    page.retranslate()
    assert page.save_button.text() == "Save"
    assert page.method_combo.currentData() == "standard"  # choice kept
    assert page.method_combo.currentText() == "3× random"
