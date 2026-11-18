"""Zertifikate, Schlüssel and Optionen render in both themes and both languages
without a missing text."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit  # noqa: E402

from tests.gui_fixtures import (  # noqa: E402,F401
    app_config,
    gui_reset,
    isolated_profile,
)

PAGES = ("certificates_page", "keys_page", "options_page")
_RAW_KEY = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
_PAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "gui" / "pages"


def test_every_text_key_used_by_the_pages_exists():
    from gui.i18n import STRINGS

    for name in PAGES:
        source = (_PAGE_DIR / f"{name}.py").read_text(encoding="utf-8")
        for match in re.finditer(r'\btr\(\s*(f?)"([^"]+)"', source):
            is_f, key = match.groups()
            if is_f and "{" in key:
                pattern = re.compile("^" + re.sub(r"\\\{[^}]*\\\}", "[a-z0-9_]+",
                                                  re.escape(key)) + "$")
                assert any(pattern.match(k) for k in STRINGS), f"{name}: {key}"
            else:
                assert key in STRINGS, f"{name}: {key}"


def _visible_texts(widget) -> list[str]:
    texts = [w.text() for w in widget.findChildren(QLabel)]
    texts += [w.text() for w in widget.findChildren(QAbstractButton)]
    texts += [w.placeholderText() for w in widget.findChildren(QLineEdit)]
    for combo in widget.findChildren(QComboBox):
        texts += [combo.itemText(i) for i in range(combo.count())]
    return texts


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("lang", ["de", "en"])
def test_pages_render_in_both_themes_and_languages(qtbot, app_config, gui_reset, mode, lang):
    import core.config as cfg
    from core.log import log_wipe_to_csv
    from gui import i18n
    from gui.pages.certificates_page import CertificatesPage
    from gui.pages.keys_page import KeysPage
    from gui.pages.options_page import OptionsPage
    from gui.theme import ThemeManager

    cfg.load_signing_key()
    log_wipe_to_csv({"date": "2026-11-18 12:00:00", "device_model": "SanDisk",
                     "serial_number": "123", "result": "LIMITED", "cert_number": 1})
    i18n.set_language(lang)
    ThemeManager(gui_reset, mode).apply()
    for page in (CertificatesPage(app_config), KeysPage(app_config), OptionsPage(app_config)):
        qtbot.addWidget(page)
        page.retranslate()
        page.on_theme_changed()
        page.resize(1192, 800)
        page.show()
        assert not page.grab().isNull()
        for text in _visible_texts(page):
            assert not _RAW_KEY.match(text), f"{type(page).__name__}: {text}"
            assert " / " not in text or "http" in text, f"{type(page).__name__}: {text}"
