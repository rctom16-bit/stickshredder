"""One-page "key card" PDF — the trust anchor for signed certificates.

A fingerprint printed on a certificate proves nothing on its own (a forger can
print their own). The key card is filed once with the IT documentation or handed
to the auditor; only a certificate whose fingerprint matches the card is
trustworthy. Its QR code opens the verify page and adds the key as trusted.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from cert.generator import (
    DARK_BLUE,
    LIGHT_BLUE_BG,
    TABLE_BORDER,
    TEXT_GRAY,
    _build_styles,
    _label,
    _safe,
)
from cert.signing import SigningKey, b64url, keycard_url
from core.log import audit_log

_LANGUAGES = ("de", "en", "both")
_MARGIN = 2 * cm
_CONTENT_WIDTH = A4[0] - 2 * _MARGIN
_QR_SIZE = 5.5 * cm


def _styles() -> dict[str, ParagraphStyle]:
    styles = _build_styles()
    styles.update(
        {
            "kc_company": ParagraphStyle(
                "KcCompany",
                parent=styles["title"],
                fontSize=18,
                leading=22,
                spaceAfter=0,
            ),
            "kc_label": ParagraphStyle(
                "KcLabel",
                parent=styles["sig_label"],
                fontSize=9,
                leading=11,
            ),
            "kc_fingerprint": ParagraphStyle(
                "KcFingerprint",
                parent=styles["title"],
                fontName="Courier-Bold",
                fontSize=17,
                leading=21,
                spaceAfter=0,
            ),
            "kc_small": ParagraphStyle(
                "KcSmall",
                parent=styles["sig_label"],
                fontSize=8,
                leading=10,
            ),
            "kc_mono": ParagraphStyle(
                "KcMono",
                parent=styles["sig_label"],
                fontName="Courier",
                fontSize=8,
                leading=10,
            ),
            "kc_body": ParagraphStyle(
                "KcBody",
                parent=styles["cell_value"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                textColor=DARK_BLUE,
                alignment=TA_LEFT,
                leftIndent=12,
                bulletIndent=0,
                spaceAfter=2 * mm,
            ),
            "kc_heading": ParagraphStyle(
                "KcHeading",
                parent=styles["subtitle"],
                fontName="Helvetica-Bold",
                fontSize=10,
                alignment=TA_LEFT,
                spaceAfter=2 * mm,
            ),
        }
    )
    return styles


def _qr_drawing(value: str) -> Drawing:
    widget = QrCodeWidget(value, barLevel="M")
    x1, y1, x2, y2 = widget.getBounds()
    width, height = x2 - x1, y2 - y1
    drawing = Drawing(
        _QR_SIZE, _QR_SIZE, transform=[_QR_SIZE / width, 0, 0, _QR_SIZE / height, 0, 0]
    )
    drawing.add(widget)
    return drawing


def _boxed(flowable, background=LIGHT_BLUE_BG) -> Table:
    table = Table([[flowable]], colWidths=[_CONTENT_WIDTH])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.75, TABLE_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]
        )
    )
    return table


def _signature_block(lang: str, styles: dict[str, ParagraphStyle]) -> Table:
    col = (_CONTENT_WIDTH - 1.5 * cm) / 2
    table = Table(
        [
            ["", "", ""],
            [
                Paragraph(_label("Ort, Datum", "Place, date", lang), styles["sig_label"]),
                "",
                Paragraph(
                    _label("Unterschrift IT-Leitung", "Signature, Head of IT", lang),
                    styles["sig_label"],
                ),
            ],
        ],
        colWidths=[col, 1.5 * cm, col],
        rowHeights=[1.6 * cm, None],
    )
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (0, 0), 0.75, DARK_BLUE),
                ("LINEBELOW", (2, 0), (2, 0), 0.75, DARK_BLUE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _instructions(lang: str) -> list[tuple[str, str]]:
    return [
        (
            "Diese Karte mit der IT-Dokumentation ablegen bzw. dem Prüfer oder "
            "Datenschutzbeauftragten übergeben.",
            "File this card with the IT documentation or hand it to the auditor or "
            "data protection officer.",
        ),
        (
            "Jedes Löschzertifikat dieser Installation trägt einen Fingerabdruck. Nur wenn er "
            "genau mit dem Fingerabdruck auf dieser Karte übereinstimmt, stammt das "
            "Zertifikat von diesem Schlüssel.",
            "Every deletion certificate from this installation carries a fingerprint. Only "
            "if it matches the fingerprint on this card exactly does the certificate come "
            "from this key.",
        ),
        (
            "QR-Code einmal mit dem Gerät des Prüfers scannen: Die Prüfseite merkt sich den "
            "Schlüssel und zeigt passende Zertifikate danach als „Echt — Schlüssel bekannt“ an.",
            "Scan the QR code once on the auditor's device: the verify page remembers the "
            "key and then shows matching certificates as “Genuine — key known”.",
        ),
        (
            "Die Signatur beweist, dass ein Zertifikat seit der Erstellung unverändert ist. "
            "Sie beweist nicht, dass physisch gelöscht wurde.",
            "The signature proves that a certificate is unchanged since it was created. It "
            "does not prove that data was physically erased.",
        ),
    ]


def generate_keycard(
    key: SigningKey, company: str, output_path: Path, language: str = "de"
) -> Path:
    """Render the key card for ``key`` and return the written PDF path.

    ``language`` is ``"de"``, ``"en"`` or ``"both"``.
    """
    if language not in _LANGUAGES:
        raise ValueError(f"language must be one of {_LANGUAGES}, got {language!r}")
    lang = language
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    styles = _styles()
    public_key = key.public_key_bytes()
    fingerprint = key.fingerprint()
    today = date.today()
    date_text = today.strftime("%Y-%m-%d") if lang == "en" else today.strftime("%d.%m.%Y")
    title = _label(
        "Prüfschlüssel für Löschzertifikate", "Verification key for deletion certificates", lang
    )

    qr_caption = _label(
        "Scannen, um diesen Schlüssel auf der Prüfseite als vertraut zu hinterlegen",
        "Scan to add this key as trusted on the verify page",
        lang,
    )
    qr_table = Table(
        [[_qr_drawing(keycard_url(public_key, company))], [Paragraph(qr_caption, styles["kc_small"])]],
        colWidths=[_CONTENT_WIDTH],
    )
    qr_table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))

    elements = [
        Paragraph(_safe(title), styles["title"]),
        Paragraph("StickShredder", styles["subtitle"]),
        Spacer(1, 4 * mm),
        Paragraph(_safe(company), styles["kc_company"]),
        Spacer(1, 6 * mm),
        Paragraph(_label("Fingerabdruck des Schlüssels", "Key fingerprint", lang), styles["kc_label"]),
        Spacer(1, 1.5 * mm),
        _boxed(Paragraph(fingerprint, styles["kc_fingerprint"])),
        Spacer(1, 5 * mm),
        qr_table,
        Spacer(1, 3 * mm),
        Paragraph(
            _label("Öffentlicher Schlüssel", "Public key", lang) + ":", styles["kc_small"]
        ),
        Paragraph(b64url(public_key), styles["kc_mono"]),
        Spacer(1, 1.5 * mm),
        Paragraph(f"{_label('Erstellt am', 'Created on', lang)}: {date_text}", styles["kc_small"]),
        Spacer(1, 5 * mm),
        Paragraph(_label("So wird die Karte verwendet", "How to use this card", lang), styles["kc_heading"]),
    ]
    for de, en in _instructions(lang):
        elements.append(Paragraph(_safe(_label(de, en, lang)), styles["kc_body"], bulletText="•"))
    elements += [Spacer(1, 4 * mm), _signature_block(lang, styles)]

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title=title,
        author=company or "StickShredder",
        subject=f"StickShredder key card {fingerprint}",
        keywords=f"stickshredder-key:{b64url(public_key)}",
    )
    doc.build(elements)

    audit_log(f"Key card generated: fingerprint={fingerprint} path={out.resolve()}")
    return out
