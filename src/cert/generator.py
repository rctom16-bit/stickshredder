"""PDF certificate generator for DIN 66399 / ISO 21964 deletion certificates."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from typing import TYPE_CHECKING

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cert.payload import RESULT_LABELS, build_payload, nist_category, result_status
from core.config import APP_VERSION
from core.log import audit_log
from core.protection_class import class3_note

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from cert.signing import SigningKey

# PDF Keywords marker the verify page and ``cert.verify_cert`` look for.
TOKEN_MARKER = "stickshredder-token:"

# Signed certificates carry their data as a machine-readable PDF attachment.
ATTACHMENT_NAME = "stickshredder-certificate.json"
ATTACHMENT_FORMAT = "stickshredder-certificate"

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
DARK_BLUE = colors.HexColor("#1a365d")
LIGHT_BLUE_BG = colors.HexColor("#e2e8f0")
TABLE_BORDER = colors.HexColor("#cbd5e0")
PASS_GREEN = colors.HexColor("#276749")
FAIL_RED = colors.HexColor("#c53030")
LIMITED_AMBER = colors.HexColor("#975a16")
TEXT_GRAY = colors.HexColor("#4a5568")

PAGE_WIDTH, PAGE_HEIGHT = A4


# ---------------------------------------------------------------------------
# XML-escape helper for user-supplied text
# ---------------------------------------------------------------------------
def _safe(text: str) -> str:
    """Escape XML-reserved characters in user-supplied text before Paragraph rendering.

    ReportLab parses Paragraph content as XML/HTML; without escaping, user input
    can inject styling (color, font) or, in older reportlab versions, file:// URIs.
    """
    if text is None:
        return ""
    return _xml_escape(str(text), {"'": "&apos;", '"': "&quot;"})


# ---------------------------------------------------------------------------
# Data transfer object
# ---------------------------------------------------------------------------
@dataclass
class CertificateData:
    """All data required to render a deletion certificate."""

    cert_number: int
    date: datetime
    operator: str
    client_reference: str  # optional, can be empty
    asset_tag: str  # optional, can be empty

    # Device
    device_model: str
    device_manufacturer: str
    serial_number: str
    capacity_bytes: int
    filesystem: str
    connection_type: str

    # Wipe
    wipe_method: str
    sicherheitsstufe: str  # e.g. "H-4"
    schutzklasse: int  # 1, 2 or 3
    passes: int
    start_time: datetime
    end_time: datetime

    # Verification
    verification_passed: bool
    sectors_checked: int
    verification_hash: str

    # Company / operator
    company_name: str
    company_address: str
    company_logo_path: str  # optional, can be empty
    language: str  # "de", "en", or "both"

    # Verification (new for v1.1) — optional, defaults keep backward compat
    verify_method: str = "sample"  # "none" | "sample" | "full"
    verify_bytes: int = 0  # bytes verified (0 for sample mode)
    verify_pattern: str = ""  # human-readable, e.g. "zeros", "0xFF", "non-zero (random)"
    verify_error_count: int = 0
    verify_mismatch_offsets: list[int] = field(default_factory=list)
    verify_duration_seconds: float = 0.0

    # Reformat (new for v1.1) — optional, defaults keep backward compat
    reformat_performed: bool = False
    reformat_filesystem: str = ""  # "FAT32" | "exFAT" | "NTFS" or ""
    reformat_label: str = ""

    # Bad sectors (new for v1.2) — optional, section only rendered when count > 0.
    # count = number of unwritable regions (blocks); bytes = their total size;
    # offsets = first 100 byte offsets.
    bad_sector_count: int = 0
    bad_sector_bytes: int = 0
    bad_sector_offsets: list[int] = field(default_factory=list)

    # Hidden areas — HPA/DCO (new for v1.2). Section rendered when probed and a
    # hidden area was found. Bytes = hidden sectors * 512.
    hidden_area_probed: bool = False
    hpa_present: bool = False
    hpa_hidden_bytes: int = 0
    dco_present: bool = False
    dco_hidden_bytes: int = 0

    # Secure erase (new for v1.2) — set when the wipe was performed via the
    # experimental ATA Secure Erase path instead of an overwrite.
    secure_erase_used: bool = False
    secure_erase_method: str = ""  # "ata-secure-erase" | "ata-enhanced"

    # Three-level result (v2.0). ``wipe_success`` is False when the overwrite
    # or erase itself failed; ``hidden_area_removed`` is True once a found
    # HPA/DCO has been removed and wiped as well.
    wipe_success: bool = True
    hidden_area_removed: bool = False

    # Media type of the wiped disk ("ssd" | "flash" | "hdd" | "unknown", or
    # "" when not known). With protection class 3 an SSD or flash medium gets
    # the note that only physical destruction is enough. Not signed.
    media_type: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_capacity(size_bytes: int) -> str:
    """Return a human-readable capacity string, e.g. ``32212254720`` -> ``"32.00 GB"``."""
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size_bytes)
    for unit in units:
        if abs(value) < 1024.0 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024.0
    # Unreachable, but keeps type checkers happy.
    return f"{value:.2f} PB"  # pragma: no cover


def format_duration(start: datetime, end: datetime) -> str:
    """Return *HH:MM:SS* duration between two datetimes."""
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_seconds(seconds: float) -> str:
    """Return *HH:MM:SS* from a float seconds value (e.g. 1234.5 -> "00:20:34")."""
    total = int(seconds) if seconds > 0 else 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_offsets_hex(offsets: list[int], limit: int = 10) -> str:
    """Return first N offsets as comma-separated uppercase hex.

    Example: ``[0x400000, 0x10000200]`` -> ``"0x00400000, 0x10000200"``.
    """
    shown = offsets[:limit]
    return ", ".join(f"0x{off:08X}" for off in shown)


# ---------------------------------------------------------------------------
# Label helper — bilingual or single-language
# ---------------------------------------------------------------------------
def _label(de: str, en: str, lang: str) -> str:
    """Return the label text according to the language setting."""
    if lang == "de":
        return de
    if lang == "en":
        return en
    return f"{de} / {en}"


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def _build_styles() -> dict[str, ParagraphStyle]:
    """Create all paragraph styles used in the certificate."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "CertTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=19,
            textColor=DARK_BLUE,
            alignment=TA_CENTER,
            spaceAfter=1 * mm,
        ),
        "subtitle": ParagraphStyle(
            "CertSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=12,
            textColor=DARK_BLUE,
            alignment=TA_CENTER,
            spaceAfter=2 * mm,
        ),
        "company": ParagraphStyle(
            "Company",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=DARK_BLUE,
            alignment=TA_CENTER,
        ),
        "company_addr": ParagraphStyle(
            "CompanyAddr",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=TEXT_GRAY,
            alignment=TA_CENTER,
            spaceAfter=2 * mm,
        ),
        "section": ParagraphStyle(
            "SectionHead",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=DARK_BLUE,
        ),
        "cell_label": ParagraphStyle(
            "CellLabel",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=TEXT_GRAY,
        ),
        "cell_value": ParagraphStyle(
            "CellValue",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10,
            textColor=colors.black,
        ),
        "result_pass": ParagraphStyle(
            "ResultPass",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=PASS_GREEN,
        ),
        "result_fail": ParagraphStyle(
            "ResultFail",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=FAIL_RED,
        ),
        "result_skipped": ParagraphStyle(
            "ResultSkipped",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=TEXT_GRAY,
        ),
        "result_limited": ParagraphStyle(
            "ResultLimited",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=12,
            textColor=LIMITED_AMBER,
        ),
        "auth_text": ParagraphStyle(
            "AuthText",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            textColor=TEXT_GRAY,
        ),
        "auth_fingerprint": ParagraphStyle(
            "AuthFingerprint",
            parent=base["Normal"],
            fontName="Courier-Bold",
            fontSize=9,
            leading=11,
            textColor=colors.black,
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=9,
            textColor=TEXT_GRAY,
            alignment=TA_CENTER,
        ),
        "sig_label": ParagraphStyle(
            "SigLabel",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=TEXT_GRAY,
            alignment=TA_CENTER,
        ),
        "sig_date": ParagraphStyle(
            "SigDate",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9,
            textColor=TEXT_GRAY,
            alignment=TA_CENTER,
        ),
    }


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------
_TABLE_WIDTH = PAGE_WIDTH - 2 * 2 * cm  # usable width inside margins

_COMMON_TABLE_STYLE = [
    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("GRID", (0, 0), (-1, -1), 0.4, TABLE_BORDER),
    ("TOPPADDING", (0, 0), (-1, -1), 2),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
]


def _section_header(text: str, styles: dict[str, ParagraphStyle]) -> Table:
    """A full-width coloured bar acting as a section title."""
    tbl = Table(
        [[Paragraph(text, styles["section"])]],
        colWidths=[_TABLE_WIDTH],
    )
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE_BG),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return tbl


def _kv_table(
    rows: list[tuple[str, str]],
    styles: dict[str, ParagraphStyle],
) -> Table:
    """Two-column label/value table."""
    label_w = _TABLE_WIDTH * 0.42
    value_w = _TABLE_WIDTH * 0.58
    data = [
        [
            Paragraph(label, styles["cell_label"]),
            Paragraph(value, styles["cell_value"]),
        ]
        for label, value in rows
    ]
    tbl = Table(data, colWidths=[label_w, value_w])
    tbl.setStyle(TableStyle(_COMMON_TABLE_STYLE))
    return tbl


# ---------------------------------------------------------------------------
# Verification section builder (split out for testability)
# ---------------------------------------------------------------------------
def _build_verification_elements(
    data: CertificateData,
    styles: dict[str, ParagraphStyle],
    lang: str,
) -> list:
    """Build the flowables for the Verification section.

    Branches on ``data.verify_method``:

    * ``"sample"`` — sectors checked + SHA-256 hash (legacy behaviour)
    * ``"full"`` — verified bytes, expected pattern, duration, error count,
      plus first 10 mismatch offsets when errors occurred
    * ``"none"`` — a single "Skipped" row rendered in gray
    """
    elements: list = []

    # Section header (always)
    elements.append(
        _section_header(
            _label("Verifizierung", "Verification", lang),
            styles,
        )
    )

    # -------- Branch: "none" (verification skipped) --------
    if data.verify_method == "none":
        skipped_label = _label("Ergebnis", "Result", lang)
        skipped_text = _label("Nicht durchgeführt", "Skipped", lang)
        skipped_row = [
            [
                Paragraph(skipped_label, styles["cell_label"]),
                Paragraph(skipped_text, styles["result_skipped"]),
            ]
        ]
        skipped_tbl = Table(
            skipped_row,
            colWidths=[_TABLE_WIDTH * 0.42, _TABLE_WIDTH * 0.58],
        )
        skipped_tbl.setStyle(TableStyle(_COMMON_TABLE_STYLE))
        elements.append(skipped_tbl)

        # Also show the verification mode row so reviewers see "none"
        mode_label = _label("Verifizierungsmodus", "Verification Mode", lang)
        mode_value = _label("Nicht durchgeführt", "Skipped", lang)
        elements.append(_kv_table([(mode_label, mode_value)], styles))
        elements.append(Spacer(1, 4 * mm))
        return elements

    # -------- Common: Result PASS/FAIL row --------
    if data.verification_passed:
        result_text = _label("BESTANDEN", "PASSED", lang)
        result_style = styles["result_pass"]
    else:
        result_text = _label("NICHT BESTANDEN", "FAILED", lang)
        result_style = styles["result_fail"]

    result_label = _label("Ergebnis", "Result", lang)
    result_row_data = [
        [
            Paragraph(result_label, styles["cell_label"]),
            Paragraph(result_text, result_style),
        ]
    ]
    result_tbl = Table(
        result_row_data,
        colWidths=[_TABLE_WIDTH * 0.42, _TABLE_WIDTH * 0.58],
    )
    result_tbl.setStyle(TableStyle(_COMMON_TABLE_STYLE))
    elements.append(result_tbl)

    # -------- Common: Verification Mode --------
    mode_label = _label("Verifizierungsmodus", "Verification Mode", lang)
    if data.verify_method == "full":
        mode_value = _label(
            "Vollständig (alle Sektoren)", "Full (all sectors)", lang
        )
    else:  # sample
        mode_value = _label(
            "Probe (Zufallsprüfung)", "Sample (random)", lang
        )

    verif_rows: list[tuple[str, str]] = [(mode_label, mode_value)]

    # -------- Branch: "full" --------
    if data.verify_method == "full":
        verif_rows.append(
            (
                _label("Geprüfte Datenmenge", "Verified Bytes", lang),
                format_capacity(data.verify_bytes),
            )
        )
        verif_rows.append(
            (
                _label("Geprüfte Sektoren", "Sectors Checked", lang),
                f"{data.sectors_checked:,}".replace(",", "."),
            )
        )
        verif_rows.append(
            (
                _label("Erwartetes Muster", "Expected Pattern", lang),
                _safe(data.verify_pattern) if data.verify_pattern else "-",
            )
        )
        verif_rows.append(
            (
                _label("Dauer", "Duration", lang),
                _format_seconds(data.verify_duration_seconds),
            )
        )
        verif_rows.append(
            (
                _label("Anzahl Fehler", "Error Count", lang),
                f"{data.verify_error_count:,}".replace(",", "."),
            )
        )
        if data.verify_error_count > 0 and data.verify_mismatch_offsets:
            verif_rows.append(
                (
                    _label(
                        "Erste Fehler-Offsets",
                        "First Mismatch Offsets",
                        lang,
                    ),
                    _format_offsets_hex(data.verify_mismatch_offsets, limit=10),
                )
            )

    # -------- Branch: "sample" (default, backward compat) --------
    else:
        verif_rows.append(
            (
                _label("Geprüfte Sektoren", "Sectors Checked", lang),
                f"{data.sectors_checked:,}".replace(",", "."),
            )
        )
        verif_rows.append(
            (
                _label("Prüf-Hash (SHA-256)", "Verification Hash (SHA-256)", lang),
                data.verification_hash or "N/A",
            )
        )

    elements.append(_kv_table(verif_rows, styles))
    elements.append(Spacer(1, 4 * mm))
    return elements


# ---------------------------------------------------------------------------
# Bad-sector section builder (v1.2)
# ---------------------------------------------------------------------------
def _build_bad_sector_elements(
    data: CertificateData,
    styles: dict[str, ParagraphStyle],
    lang: str,
) -> list:
    """Build the flowables for the Bad Sectors section.

    Only call this when ``data.bad_sector_count > 0``. Renders the number of
    unwritable regions, the affected data volume, an approximate sector count,
    the first 10 byte offsets, and a prominent warning that those sectors may
    still hold old data.
    """
    elements: list = []
    elements.append(
        _section_header(_label("Defekte Sektoren", "Bad Sectors", lang), styles)
    )

    approx_sectors = data.bad_sector_bytes // 512
    rows: list[tuple[str, str]] = [
        (
            _label("Nicht beschreibbare Bereiche", "Unwritable Regions", lang),
            f"{data.bad_sector_count:,}".replace(",", "."),
        ),
        (
            _label("Betroffene Datenmenge", "Affected Data", lang),
            format_capacity(data.bad_sector_bytes),
        ),
        (
            _label("Betroffene Sektoren (ca.)", "Affected Sectors (approx.)", lang),
            f"{approx_sectors:,}".replace(",", "."),
        ),
    ]
    if data.bad_sector_offsets:
        rows.append(
            (
                _label("Erste Offsets", "First Offsets", lang),
                _format_offsets_hex(data.bad_sector_offsets, limit=10),
            )
        )
    elements.append(_kv_table(rows, styles))

    warning = _label(
        "Achtung: Diese Sektoren konnten nicht überschrieben werden und können "
        "Altdaten enthalten. Für hochsensible Daten wird physische Vernichtung "
        "empfohlen.",
        "Warning: these sectors could not be overwritten and may still contain "
        "old data. Physical destruction is recommended for high-sensitivity data.",
        lang,
    )
    elements.append(Paragraph(warning, styles["result_fail"]))
    elements.append(Spacer(1, 4 * mm))
    return elements


# ---------------------------------------------------------------------------
# Hidden-area section builder (v1.2)
# ---------------------------------------------------------------------------
def _build_hidden_area_elements(
    data: CertificateData,
    styles: dict[str, ParagraphStyle],
    lang: str,
) -> list:
    """Build the Hidden Areas (HPA/DCO) section.

    Only call when ``data.hidden_area_probed`` is True and at least one of
    ``hpa_present`` / ``dco_present`` is set. These areas are hidden from the
    operating system and are not reached by an overwrite wipe.
    """
    elements: list = []
    elements.append(
        _section_header(
            _label("Versteckte Bereiche (HPA/DCO)", "Hidden Areas (HPA/DCO)", lang),
            styles,
        )
    )
    rows: list[tuple[str, str]] = []
    if data.hpa_present:
        rows.append(
            (
                "Host Protected Area (HPA)",
                _label(
                    f"vorhanden — {format_capacity(data.hpa_hidden_bytes)} versteckt",
                    f"present — {format_capacity(data.hpa_hidden_bytes)} hidden",
                    lang,
                ),
            )
        )
    if data.dco_present:
        rows.append(
            (
                "Device Configuration Overlay (DCO)",
                _label(
                    f"vorhanden — {format_capacity(data.dco_hidden_bytes)} versteckt",
                    f"present — {format_capacity(data.dco_hidden_bytes)} hidden",
                    lang,
                ),
            )
        )
    elements.append(_kv_table(rows, styles))

    warning = _label(
        "Achtung: Diese versteckten Bereiche werden durch Überschreiben NICHT "
        "erreicht und können Altdaten enthalten. Für hochsensible Daten wird "
        "physische Vernichtung empfohlen.",
        "Warning: these hidden areas are NOT reached by an overwrite wipe and "
        "may still contain old data. Physical destruction is recommended for "
        "high-sensitivity data.",
        lang,
    )
    elements.append(Paragraph(warning, styles["result_fail"]))
    elements.append(Spacer(1, 4 * mm))
    return elements


# ---------------------------------------------------------------------------
# Overall result row and authenticity section (v2.0)
# ---------------------------------------------------------------------------
_RESULT_STYLE = {"ok": "result_pass", "limited": "result_limited", "failed": "result_fail"}

_QR_SIZE = 3.4 * cm
# Smallest printed QR module a phone camera reads reliably.
QR_MIN_MODULE = 0.5 * mm


def _build_result_row(
    data: CertificateData,
    styles: dict[str, ParagraphStyle],
    lang: str,
) -> Table:
    """One-row table with the three-level overall result, coloured."""
    status = result_status(data)
    de, en = RESULT_LABELS[status]
    tbl = Table(
        [
            [
                Paragraph(_label("Gesamtergebnis", "Overall Result", lang), styles["cell_label"]),
                Paragraph(_label(de, en, lang), styles[_RESULT_STYLE[status]]),
            ]
        ],
        colWidths=[_TABLE_WIDTH * 0.42, _TABLE_WIDTH * 0.58],
    )
    tbl.setStyle(TableStyle(_COMMON_TABLE_STYLE))
    return tbl


def qr_module_count(widget: QrCodeWidget) -> int:
    """Modules per side of ``widget``'s code, quiet zone included."""
    widget.draw()   # builds widget.qr
    return widget.qr.getModuleCount() + 2 * int(widget.barBorder)


def _qr_drawing(value: str, size: float = _QR_SIZE) -> Drawing:
    """QR code of ``value``, at least ``size`` wide and never finer than
    QR_MIN_MODULE per module: the signed verify link (~700 characters) needs
    ~97 modules, which at 3.4 cm printed 0.35 mm modules — too fine to scan."""
    # Level L keeps the long verify link as coarse as possible.
    widget = QrCodeWidget(value, barLevel="L")
    size = max(size, qr_module_count(widget) * QR_MIN_MODULE)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


def _build_authenticity_elements(
    token: str,
    fingerprint: str,
    styles: dict[str, ParagraphStyle],
    lang: str,
) -> list:
    """Section „Echtheit / Authenticity“: QR code of the verify link + fingerprint."""
    from cert.signing import verify_url

    text = _label(
        "Echtheit prüfen: QR-Code scannen oder PDF in StickShredder öffnen. "
        "Fingerabdruck mit der Schlüsselkarte Ihrer IT vergleichen.",
        "Check authenticity: scan the QR code or open the PDF in StickShredder. "
        "Compare the fingerprint with your IT department's key card.",
        lang,
    )
    if lang == "both":
        # One sentence per language reads better than "de / en" run together.
        text = text.replace(" / Check authenticity", "<br/>Check authenticity")
    right = [
        Paragraph(
            _label("Fingerabdruck des Schlüssels", "Key fingerprint", lang),
            styles["cell_label"],
        ),
        Paragraph(fingerprint, styles["auth_fingerprint"]),
        Spacer(1, 2 * mm),
        Paragraph(text, styles["auth_text"]),
    ]
    qr = _qr_drawing(verify_url(token))
    tbl = Table(
        [[qr, right]],
        colWidths=[qr.width + 4 * mm, _TABLE_WIDTH - qr.width - 4 * mm],
    )
    tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOX", (0, 0), (-1, -1), 0.4, TABLE_BORDER),
                ("LEFTPADDING", (0, 0), (0, 0), 2 * mm),
                ("RIGHTPADDING", (0, 0), (0, 0), 2 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 * mm),
                ("LEFTPADDING", (1, 0), (1, 0), 4 * mm),
            ]
        )
    )
    # Header and code stay on one page (the header alone ended page 1).
    return [
        KeepTogether([_section_header(_label("Echtheit", "Authenticity", lang), styles), tbl]),
        Spacer(1, 2 * mm),
    ]


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------
def _footer_lines(lang: str) -> list[str]:
    """Footer text: program version and what the certificate is (not)."""
    de = (f"Erstellt mit StickShredder v{APP_VERSION} nach DIN 66399 / ISO 21964 "
          "Richtlinien. Software nicht DEKRA-zertifiziert.")
    en = (f"Created with StickShredder v{APP_VERSION} according to DIN 66399 / "
          "ISO 21964 guidelines. Software not DEKRA-certified.")
    return {"de": [de], "en": [en]}.get(lang, [de, en])


# Footer baselines, from the bottom edge of the page: inside the 1.5 cm
# margin, above what printers leave blank (~5 mm).
_FOOTER_FIRST_BASELINE = 10 * mm
_FOOTER_LEADING = 9


def _draw_footer(canvas, lines: list[str]) -> None:
    """Draw the footer lines centred in the bottom margin of the current page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(TEXT_GRAY)
    width = canvas._pagesize[0]
    for i, line in enumerate(lines):
        canvas.drawCentredString(width / 2, _FOOTER_FIRST_BASELINE - i * _FOOTER_LEADING, line)
    canvas.restoreState()


def attachment_bytes(payload: dict, token: str) -> bytes:
    """Content of the PDF attachment: the signed payload (incl. ``k``) and its token."""
    return json.dumps(
        {"format": ATTACHMENT_FORMAT, "version": 1, "payload": payload, "token": token},
        indent=2, sort_keys=True, ensure_ascii=False,
    ).encode("utf-8")


def _embed_file(canvas, name: str, content: bytes) -> None:
    """Attach ``content`` to the PDF as an embedded file (PDF 1.7 §7.11.4).

    reportlab has no high-level API for attachments; its PDF object layer
    builds the EmbeddedFile stream, the file specification and the catalog's
    ``/Names /EmbeddedFiles`` entry. Called from the first page's callback.
    """
    from reportlab.pdfbase.pdfdoc import (
        PDFArray, PDFDictionary, PDFStream, PDFString, PDFZCompress,
    )

    document = canvas._doc
    stream = PDFStream(
        PDFDictionary({
            "Type": "/EmbeddedFile",
            "Subtype": "/application#2Fjson",  # MIME type "application/json"
            "Params": PDFDictionary({"Size": len(content)}),
        }),
        content,
        filters=[PDFZCompress],
    )
    filespec = PDFDictionary({
        "Type": "/Filespec",
        "F": PDFString(name),
        "UF": PDFString(name),
        "Desc": PDFString("StickShredder: signierte Zertifikatsdaten / signed certificate data"),
        "AFRelationship": "/Data",
        "EF": PDFDictionary({"F": document.Reference(stream)}),
    })
    names = PDFDictionary({
        "EmbeddedFiles": PDFDictionary({
            "Names": PDFArray([PDFString(name), document.Reference(filespec)]),
        }),
    })
    document.Catalog.Names = names


def generate_certificate(
    data: CertificateData,
    output_path: str,
    signing_key: SigningKey | None = None,
    history_hash: str = "",
) -> str:
    """Generate a DIN 66399 / ISO 21964 deletion certificate PDF.

    Parameters
    ----------
    data:
        All certificate fields.
    output_path:
        Destination file path for the generated PDF.
    signing_key:
        When given, the certificate data is signed: the token is stored in the
        PDF Keywords as ``stickshredder-token:<token>`` and an „Echtheit /
        Authenticity“ section shows the verify QR code and the key fingerprint.
    history_hash:
        Hash of this wipe's history entry; becomes payload field ``h``.

    Returns
    -------
    str
        The absolute path of the written PDF file.
    """
    lang = data.language
    styles = _build_styles()
    elements: list = []

    token = ""
    payload: dict = {}
    if signing_key is not None:
        payload = build_payload(data, history_hash)
        token = signing_key.sign_payload(payload)

    # Ensure output directory exists
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Header area
    # ------------------------------------------------------------------
    # Company logo (optional)
    if data.company_logo_path and os.path.isfile(data.company_logo_path):
        try:
            logo = Image(data.company_logo_path)
            # Scale to a max height of 2 cm, preserving aspect ratio
            max_h = 2 * cm
            aspect = logo.imageWidth / logo.imageHeight
            logo.drawHeight = max_h
            logo.drawWidth = max_h * aspect
            if logo.drawWidth > 6 * cm:
                logo.drawWidth = 6 * cm
                logo.drawHeight = logo.drawWidth / aspect
            logo.hAlign = "CENTER"
            elements.append(logo)
            elements.append(Spacer(1, 3 * mm))
        except Exception:
            pass  # Skip unreadable images silently

    # Company name and address
    if data.company_name:
        elements.append(Paragraph(_safe(data.company_name), styles["company"]))
    if data.company_address:
        # Escape FIRST, then substitute newlines with <br/> so the tag survives
        addr = _safe(data.company_address).replace("\n", "<br/>")
        elements.append(Paragraph(addr, styles["company_addr"]))

    elements.append(Spacer(1, 1 * mm))

    # Title
    if lang == "de":
        title_text = "Löschzertifikat"
    elif lang == "en":
        title_text = "Deletion Certificate"
    else:
        title_text = "Löschzertifikat / Deletion Certificate"
    elements.append(Paragraph(title_text, styles["title"]))

    # Subtitle
    elements.append(
        Paragraph("nach DIN 66399 / ISO 21964", styles["subtitle"])
    )

    # ------------------------------------------------------------------
    # 2. Certificate metadata
    # ------------------------------------------------------------------
    elements.append(
        _section_header(
            _label("Zertifikatsinformationen", "Certificate Information", lang),
            styles,
        )
    )

    meta_rows: list[tuple[str, str]] = [
        (
            _label("Zertifikatsnummer", "Certificate Number", lang),
            f"SS-{data.cert_number:06d}",
        ),
        (
            _label("Datum", "Date", lang),
            data.date.strftime("%d.%m.%Y  %H:%M:%S"),
        ),
        (
            _label("Bediener", "Operator", lang),
            _safe(data.operator),
        ),
    ]
    if data.client_reference:
        meta_rows.append(
            (
                _label("Auftraggeber", "Client Reference", lang),
                _safe(data.client_reference),
            )
        )
    if data.asset_tag:
        meta_rows.append(
            (
                _label("Asset-Tag / Ticket-Nummer", "Asset Tag / Ticket Number", lang),
                _safe(data.asset_tag),
            )
        )

    elements.append(_kv_table(meta_rows, styles))
    elements.append(_build_result_row(data, styles, lang))
    elements.append(Spacer(1, 2 * mm))

    # ------------------------------------------------------------------
    # 3. Device information
    # ------------------------------------------------------------------
    elements.append(
        _section_header(
            _label("Geräteinformationen", "Device Information", lang),
            styles,
        )
    )

    device_rows: list[tuple[str, str]] = [
        (
            _label("Hersteller / Modell", "Manufacturer / Model", lang),
            f"{_safe(data.device_manufacturer)}  {_safe(data.device_model)}",
        ),
        (
            _label("Seriennummer", "Serial Number", lang),
            _safe(data.serial_number),
        ),
        (
            _label("Kapazität", "Capacity", lang),
            format_capacity(data.capacity_bytes),
        ),
        (
            _label("Dateisystem vor Löschung", "Filesystem Before Wipe", lang),
            _safe(data.filesystem),
        ),
        (
            _label("Verbindungstyp", "Connection Type", lang),
            _safe(data.connection_type),
        ),
    ]
    elements.append(_kv_table(device_rows, styles))
    elements.append(Spacer(1, 2 * mm))

    # ------------------------------------------------------------------
    # 4. Wipe details
    # ------------------------------------------------------------------
    elements.append(
        _section_header(
            _label("Löschdetails", "Wipe Details", lang),
            styles,
        )
    )

    schutzklasse_display = f"{data.schutzklasse}"
    wipe_rows: list[tuple[str, str]] = [
        (
            _label("Löschmethode", "Deletion Method", lang),
            _safe(data.wipe_method),
        ),
        (
            _label("DIN 66399 Sicherheitsstufe", "DIN 66399 Security Level", lang),
            _safe(data.sicherheitsstufe),
        ),
        ("NIST SP 800-88", nist_category(data)),
        (
            _label("Schutzklasse", "Protection Class", lang),
            schutzklasse_display,
        ),
        (
            _label("Anzahl Durchgänge", "Number of Passes", lang),
            str(data.passes),
        ),
        (
            _label("Startzeit", "Start Time", lang),
            data.start_time.strftime("%d.%m.%Y  %H:%M:%S"),
        ),
        (
            _label("Endzeit", "End Time", lang),
            data.end_time.strftime("%d.%m.%Y  %H:%M:%S"),
        ),
        (
            _label("Dauer", "Duration", lang),
            format_duration(data.start_time, data.end_time),
        ),
    ]
    elements.append(_kv_table(wipe_rows, styles))

    # Hardware erase (Beta) note: name the procedure and say what the
    # verification below covers (spec G2).
    if data.secure_erase_used:
        if data.passes > 0:
            se_note = _label(
                "Hinweis: Hardware-Löschung (Beta) — das Laufwerk hat sich per "
                "ATA Secure Erase (Firmware-Befehl) selbst gelöscht, danach wurde "
                "es mit Nullen überschrieben. Die Prüfung unten bezieht sich auf "
                "das Überschreiben.",
                "Note: hardware erase (Beta) — the drive erased itself with ATA "
                "Secure Erase (a firmware command), then it was overwritten with "
                "zeros. The verification below covers the overwrite.",
                lang,
            )
        else:
            se_note = _label(
                "Hinweis: Hardware-Löschung (Beta) per ATA Secure Erase "
                "(Firmware-Befehl, EXPERIMENTELL — auf dieser Hardware nicht "
                "verifiziert). Kein sektorweises Überschreiben.",
                "Note: hardware erase (Beta) via ATA Secure Erase (a firmware "
                "command, EXPERIMENTAL — not verified on this hardware). This is "
                "not a sector-by-sector overwrite.",
                lang,
            )
        elements.append(Paragraph(se_note, styles["result_skipped"]))

    # Protection class 3 on an SSD or flash medium: overwriting can't reach
    # the reserve areas, so the certificate says physical destruction is needed.
    note = class3_note(data.media_type, data.schutzklasse, data.secure_erase_used)
    if note is not None:
        elements.append(Spacer(1, 1.5 * mm))
        elements.append(Paragraph(_safe(_label(note[0], note[1], lang)),
                                  styles["result_limited"]))
    elements.append(Spacer(1, 2 * mm))

    # ------------------------------------------------------------------
    # 5. Verification
    # ------------------------------------------------------------------
    elements.extend(_build_verification_elements(data, styles, lang))

    # --------------------------------------------------------------------------
    # 5c. Bad sectors (v1.2) — only when defects were recorded during the wipe.
    # --------------------------------------------------------------------------
    if data.bad_sector_count > 0:
        elements.extend(_build_bad_sector_elements(data, styles, lang))

    # --------------------------------------------------------------------------
    # 5d. Hidden areas (HPA/DCO) (v1.2) — only when a hidden area was found.
    # --------------------------------------------------------------------------
    if data.hidden_area_probed and (data.hpa_present or data.dco_present):
        elements.extend(_build_hidden_area_elements(data, styles, lang))

    # --------------------------------------------------------------------------
    # 5b. Reformat (v1.1)
    # --------------------------------------------------------------------------
    if data.reformat_performed:
        elements.append(
            _section_header(
                _label("Formatierung", "Reformat", lang),
                styles,
            )
        )
        reformat_rows = [
            (
                _label("Dateisystem", "Filesystem", lang),
                _safe(data.reformat_filesystem),
            ),
            (
                _label("Volumenbezeichnung", "Volume Label", lang),
                _safe(data.reformat_label),
            ),
            (
                _label("Ergebnis", "Result", lang),
                _label("Neu formatiert, einsatzbereit",
                       "Reformatted, ready for use", lang),
            ),
        ]
        elements.append(_kv_table(reformat_rows, styles))
        elements.append(Spacer(1, 2 * mm))

    # --------------------------------------------------------------------------
    # 5e. Authenticity (v2.0) — only on signed certificates.
    # --------------------------------------------------------------------------
    if token:
        elements.extend(
            _build_authenticity_elements(token, signing_key.fingerprint(), styles, lang)
        )

    # ------------------------------------------------------------------
    # 6. Signature area
    # ------------------------------------------------------------------
    sig_col_w = _TABLE_WIDTH / 2 - 4 * mm

    sig_line = "_" * 36
    sig_data = [
        [
            Paragraph(sig_line, styles["sig_label"]),
            Paragraph("", styles["sig_label"]),  # spacer column
            Paragraph(sig_line, styles["sig_label"]),
        ],
        [
            Paragraph(
                _label(
                    "Löschverantwortlicher",
                    "Responsible for Deletion",
                    lang,
                ),
                styles["sig_label"],
            ),
            Paragraph("", styles["sig_label"]),
            Paragraph(
                _label(
                    "Datenschutzbeauftragter",
                    "Data Protection Officer",
                    lang,
                ),
                styles["sig_label"],
            ),
        ],
        [
            Paragraph(
                _label("Datum", "Date", lang) + ": _______________",
                styles["sig_date"],
            ),
            Paragraph("", styles["sig_date"]),
            Paragraph(
                _label("Datum", "Date", lang) + ": _______________",
                styles["sig_date"],
            ),
        ],
    ]

    sig_tbl = Table(
        sig_data,
        colWidths=[sig_col_w, 8 * mm, sig_col_w],
    )
    sig_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    # A table splits by rows across pages: keep the signature lines together.
    elements.append(KeepTogether([sig_tbl]))

    # ------------------------------------------------------------------
    # 7. Footer — in the bottom margin of every page (_draw_footer). As the
    #    last paragraphs it used to spill onto an otherwise empty page.
    # ------------------------------------------------------------------
    footer = _footer_lines(lang)

    # ------------------------------------------------------------------
    # Build the PDF
    # ------------------------------------------------------------------
    doc_kwargs = {}
    if token:
        # The token alphabet ([A-Za-z0-9_-.]) needs no escaping in a PDF
        # string, so the raw file bytes contain it verbatim for the verifiers.
        doc_kwargs["keywords"] = f"{TOKEN_MARKER}{token}"
    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=title_text,
        author=data.company_name or "StickShredder",
        subject="DIN 66399 / ISO 21964 Deletion Certificate",
        **doc_kwargs,
    )
    attachment = attachment_bytes(payload, token) if token else None

    def first_page(canvas, _doc):
        if attachment is not None:
            _embed_file(canvas, ATTACHMENT_NAME, attachment)
        _draw_footer(canvas, footer)

    doc.build(
        elements,
        onFirstPage=first_page,
        onLaterPages=lambda canvas, _doc: _draw_footer(canvas, footer),
    )

    abs_path = str(out.resolve())
    signed = f" signed={signing_key.fingerprint()}" if token else " unsigned"
    audit_log(
        f"Certificate generated: #{data.cert_number:06d} "
        f"serial={data.serial_number} "
        f"result={'PASSED' if data.verification_passed else 'FAILED'} "
        f"status={result_status(data)}{signed} "
        f"path={abs_path}"
    )

    return abs_path
