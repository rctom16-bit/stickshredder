"""PDF certificate generator for DIN 66399 / ISO 21964 deletion certificates."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.config import APP_VERSION
from core.log import audit_log

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
DARK_BLUE = colors.HexColor("#1a365d")
LIGHT_BLUE_BG = colors.HexColor("#e2e8f0")
TABLE_BORDER = colors.HexColor("#cbd5e0")
PASS_GREEN = colors.HexColor("#276749")
FAIL_RED = colors.HexColor("#c53030")
TEXT_GRAY = colors.HexColor("#4a5568")

PAGE_WIDTH, PAGE_HEIGHT = A4


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
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
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
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
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
                _label("Erwartetes Muster", "Expected Pattern", lang),
                data.verify_pattern or "-",
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
# PDF generation
# ---------------------------------------------------------------------------
def generate_certificate(data: CertificateData, output_path: str) -> str:
    """Generate a DIN 66399 / ISO 21964 deletion certificate PDF.

    Parameters
    ----------
    data:
        All certificate fields.
    output_path:
        Destination file path for the generated PDF.

    Returns
    -------
    str
        The absolute path of the written PDF file.
    """
    lang = data.language
    styles = _build_styles()
    elements: list = []

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
        elements.append(Paragraph(data.company_name, styles["company"]))
    if data.company_address:
        # Replace newlines with <br/> for multi-line addresses
        addr = data.company_address.replace("\n", "<br/>")
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
            data.operator,
        ),
    ]
    if data.client_reference:
        meta_rows.append(
            (
                _label("Auftraggeber", "Client Reference", lang),
                data.client_reference,
            )
        )
    if data.asset_tag:
        meta_rows.append(
            (
                _label("Asset-Tag / Ticket-Nummer", "Asset Tag / Ticket Number", lang),
                data.asset_tag,
            )
        )

    elements.append(_kv_table(meta_rows, styles))
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
            f"{data.device_manufacturer}  {data.device_model}",
        ),
        (
            _label("Seriennummer", "Serial Number", lang),
            data.serial_number,
        ),
        (
            _label("Kapazität", "Capacity", lang),
            format_capacity(data.capacity_bytes),
        ),
        (
            _label("Dateisystem vor Löschung", "Filesystem Before Wipe", lang),
            data.filesystem,
        ),
        (
            _label("Verbindungstyp", "Connection Type", lang),
            data.connection_type,
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
            data.wipe_method,
        ),
        (
            _label("DIN 66399 Sicherheitsstufe", "DIN 66399 Security Level", lang),
            data.sicherheitsstufe,
        ),
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
    elements.append(Spacer(1, 2 * mm))

    # ------------------------------------------------------------------
    # 5. Verification
    # ------------------------------------------------------------------
    elements.extend(_build_verification_elements(data, styles, lang))

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
    elements.append(sig_tbl)
    elements.append(Spacer(1, 4 * mm))

    # ------------------------------------------------------------------
    # 7. Footer
    # ------------------------------------------------------------------
    elements.append(
        Paragraph(
            f"Erstellt mit StickShredder v{APP_VERSION}",
            styles["footer"],
        )
    )
    elements.append(Spacer(1, 1 * mm))

    if lang == "de":
        disclaimer = (
            "Erstellt nach DIN 66399 / ISO 21964 Richtlinien. "
            "Software nicht DEKRA-zertifiziert."
        )
    elif lang == "en":
        disclaimer = (
            "Generated according to DIN 66399 / ISO 21964 guidelines. "
            "Software not DEKRA-certified."
        )
    else:
        disclaimer = (
            "Erstellt nach DIN 66399 / ISO 21964 Richtlinien. "
            "Software nicht DEKRA-zertifiziert.<br/>"
            "Generated according to DIN 66399 / ISO 21964 guidelines. "
            "Software not DEKRA-certified."
        )
    elements.append(Paragraph(disclaimer, styles["footer"]))

    # ------------------------------------------------------------------
    # Build the PDF
    # ------------------------------------------------------------------
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
    )
    doc.build(elements)

    abs_path = str(out.resolve())
    audit_log(
        f"Certificate generated: #{data.cert_number:06d} "
        f"serial={data.serial_number} "
        f"result={'PASSED' if data.verification_passed else 'FAILED'} "
        f"path={abs_path}"
    )

    return abs_path
