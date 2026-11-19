"""Theme (light/dark), fonts, UI language and their settings in AppConfig."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from tests.gui_fixtures import app_config, gui_reset, isolated_profile  # noqa: F401


# -- Theme ---------------------------------------------------------------------


def test_theme_switch_changes_palette_of_a_widget(qtbot, gui_reset):
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QWidget

    from gui.theme import ThemeManager

    widget = QWidget()
    qtbot.addWidget(widget)
    widget.show()
    theme = ThemeManager(gui_reset, "dark")
    theme.apply()
    assert widget.palette().color(QPalette.ColorRole.Window).name() == "#0b1020"
    assert widget.palette().color(QPalette.ColorRole.WindowText).name() == "#eef2ff"

    theme.set_setting("light")
    assert widget.palette().color(QPalette.ColorRole.Window).name() == "#f3f5fa"
    assert widget.palette().color(QPalette.ColorRole.WindowText).name() == "#0b1020"


def test_theme_change_restyles_live_and_notifies(qtbot, gui_reset):
    from gui.theme import ThemeManager

    theme = ThemeManager(gui_reset, "dark")
    theme.apply()
    assert "#0B1020" in gui_reset.styleSheet()
    with qtbot.waitSignal(theme.changed, timeout=1000):
        theme.set_setting("light")
    assert "#F3F5FA" in gui_reset.styleSheet()
    assert theme.mode == "light"
    assert theme.palette.accent_text == "#0B7A5E"


@pytest.mark.parametrize(
    "setting, scheme, expected",
    [
        ("system", "Dark", "dark"),
        ("system", "Light", "light"),
        ("system", "Unknown", "light"),
        ("dark", "Light", "dark"),
        ("light", "Dark", "light"),
        ("purple", "Dark", "dark"),  # unknown value counts as "system"
    ],
)
def test_resolve_mode_follows_windows_unless_overridden(setting, scheme, expected):
    from PySide6.QtCore import Qt

    from gui.theme import resolve_mode

    assert resolve_mode(setting, getattr(Qt.ColorScheme, scheme)) == expected


def test_palettes_carry_the_artboard_colours():
    from gui.theme import DARK, LIGHT

    assert (DARK.bg, DARK.rail, DARK.surface, DARK.inset) == (
        "#0B1020", "#080C18", "#121A2E", "#0E1528")
    assert (DARK.accent, DARK.on_accent, DARK.danger, DARK.danger_bg) == (
        "#2DD4A7", "#04130E", "#FFB4AA", "#2A1416")
    assert (LIGHT.bg, LIGHT.surface, LIGHT.border, LIGHT.text, LIGHT.muted) == (
        "#F3F5FA", "#FFFFFF", "#DDE3EE", "#0B1020", "#4A5470")
    assert (LIGHT.danger, LIGHT.danger_bg, LIGHT.warning, LIGHT.warning_bg) == (
        "#B42318", "#FEECEB", "#7A4A00", "#FFF1D6")


# -- Fonts ---------------------------------------------------------------------


def test_bundled_fonts_register(qapp):
    from gui.theme import load_fonts

    fonts = load_fonts()
    assert (fonts.heading, fonts.body, fonts.mono) == (
        "Space Grotesk", "Inter", "JetBrains Mono")


def test_fonts_fall_back_when_missing(qapp, tmp_path):
    from gui.theme import load_fonts

    fonts = load_fonts(tmp_path / "no-fonts-here")
    assert (fonts.heading, fonts.body, fonts.mono) == (
        "Segoe UI", "Segoe UI", "Consolas")


def test_every_bundled_font_keeps_its_licence():
    from pathlib import Path

    import gui

    font_dir = Path(gui.__file__).parent / "fonts"
    families = [d for d in font_dir.iterdir() if d.is_dir()]
    assert len(families) == 3
    for family in families:
        assert list(family.glob("*.ttf")), family
        assert (family / "OFL.txt").is_file(), family


# -- Language ------------------------------------------------------------------


def test_tr_returns_german_by_default_and_english_on_switch(gui_reset):
    from gui import i18n

    assert i18n.tr("nav.delete") == "Löschen"
    i18n.set_language("en")
    assert i18n.tr("nav.delete") == "Delete"
    i18n.set_language("xx")  # unknown -> German
    assert i18n.language() == "de"


def test_every_string_has_both_languages_and_no_double_labels():
    from gui.i18n import STRINGS

    for key, (de, en) in STRINGS.items():
        assert de and en, key
        assert " / " not in de, f"{key}: German text carries an English half"


def test_tr_fills_placeholders(gui_reset):
    from gui import i18n

    assert i18n.tr("running.title", target="F:") == "F: wird gelöscht"


@pytest.mark.parametrize(
    "lang, size, expected",
    [("de", 61_933_428_736, "57,7 GB"), ("en", 61_933_428_736, "57.7 GB"),
     ("de", 10 * 1024 * 1024, "10 MB"), ("de", 1_000_204_886_016, "931,5 GB")],
)
def test_size_is_formatted_per_language(gui_reset, lang, size, expected):
    from gui import i18n

    i18n.set_language(lang)
    assert i18n.fmt_size(size) == expected


@pytest.mark.parametrize(
    "seconds, de, en",
    [(42, "42 Sek.", "42 s"), (1062, "17 Min. 42 Sek.", "17 min 42 s"),
     (7500, "2 Std. 5 Min.", "2 h 5 min")],
)
def test_duration_is_formatted_per_language(gui_reset, seconds, de, en):
    from gui import i18n

    assert i18n.fmt_duration(seconds) == de
    i18n.set_language("en")
    assert i18n.fmt_duration(seconds) == en


# -- Settings ------------------------------------------------------------------


def test_config_has_theme_and_language_defaults(isolated_profile):
    from core.config import AppConfig

    cfg = AppConfig()
    assert cfg.theme == "system"
    assert cfg.ui_language == "de"


def test_old_config_without_new_fields_still_loads(isolated_profile):
    import core.config as config

    config.CONFIG_FILE.write_text(json.dumps({"operator_name": "Robin"}), encoding="utf-8")
    cfg = config.AppConfig.load()
    assert cfg.operator_name == "Robin"
    assert cfg.theme == "system"
    assert cfg.ui_language == "de"
