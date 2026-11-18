"""Serial numbers a device does not report.

Some USB enclosures (e.g. Realtek RTL9210B) answer "0000000000000000"
instead of the drive's serial; others answer nothing or only spaces. All of
these mean "not reported by the device". The GUI, the certificate PDF and
the wipe history show that text; the certificate file name uses
``noserial``. The signed certificate payload keeps the raw value.
"""

from __future__ import annotations

NOT_REPORTED_DE = "vom Gerät nicht geliefert"
NOT_REPORTED_EN = "not reported by the device"
FILENAME_PLACEHOLDER = "noserial"

# Separators Windows puts into NVMe serials built from the EUI-64
# ("0000_0000_0000_0000."): they don't make an all-zero serial a real one.
_SEPARATORS = str.maketrans("", "", " \t_-.:")

# Placeholders StickShredder itself has written (the CLI's "N/A") and the
# history text, so a value read back is recognised as "not reported" too.
_PLACEHOLDERS = {"n/a", NOT_REPORTED_DE.casefold(), NOT_REPORTED_EN.casefold()}


def serial_missing(serial) -> bool:
    """True when ``serial`` is empty, only spaces or only zeros."""
    text = str(serial or "").strip()
    if text.casefold() in _PLACEHOLDERS:
        return True
    digits = text.translate(_SEPARATORS)
    return not digits or set(digits) == {"0"}


def serial_text(serial, lang: str = "en") -> str:
    """The serial to show: the value itself, or "not reported" in ``lang``.

    ``lang`` is "de", "en" or "both" (German / English).
    """
    if not serial_missing(serial):
        return str(serial).strip()
    if lang == "de":
        return NOT_REPORTED_DE
    if lang == "en":
        return NOT_REPORTED_EN
    return f"{NOT_REPORTED_DE} / {NOT_REPORTED_EN}"


def serial_for_filename(serial) -> str:
    """The serial part of a certificate file name."""
    return FILENAME_PLACEHOLDER if serial_missing(serial) else str(serial).strip()


def history_serial(serial) -> str:
    """The ``serial_number`` cell of the wipe history (CSV is English)."""
    return serial_text(serial, "en")
