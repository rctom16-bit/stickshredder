"""Serial numbers a device does not report (2.0 polish).

USB enclosures such as the Realtek RTL9210B answer "0000000000000000".
Empty, all-zero and all-space serials all mean "not reported by the
device": the GUI, the certificate PDF and the history show that text, the
certificate file name says "noserial", and the signed payload keeps the
raw value.
"""

from __future__ import annotations

import pytest

from core.serial import (
    FILENAME_PLACEHOLDER, NOT_REPORTED_DE, NOT_REPORTED_EN,
    history_serial, serial_for_filename, serial_missing, serial_text,
)


@pytest.mark.parametrize("raw", [
    None, "", "   ", "0000000000000000", "0", " 0000 ", "\t",
    "0000_0000_0000_0000.", "N/A", NOT_REPORTED_EN, NOT_REPORTED_DE,
])
def test_missing_serials(raw):
    assert serial_missing(raw) is True


@pytest.mark.parametrize("raw", [
    "4C530001230525112334", "0000000000000001", "AA00", "0025_3885_8100_5D11.",
])
def test_real_serials(raw):
    assert serial_missing(raw) is False


def test_text_per_language():
    assert serial_text("0000000000000000", "de") == "vom Gerät nicht geliefert"
    assert serial_text("", "en") == "not reported by the device"
    assert serial_text("  ", "both") == "vom Gerät nicht geliefert / not reported by the device"
    assert serial_text(" ABC123 ", "de") == "ABC123"


def test_file_name_and_history_values():
    assert FILENAME_PLACEHOLDER == "noserial"
    assert serial_for_filename("0000000000000000") == "noserial"
    assert serial_for_filename(None) == "noserial"
    assert serial_for_filename("ABC123") == "ABC123"
    assert history_serial("0000000000000000") == NOT_REPORTED_EN
    assert history_serial("ABC123") == "ABC123"
