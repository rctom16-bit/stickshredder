"""The Zertifikate page: every wipe from the history with its certificate.

A table of the wipe history (newest first) with a search box, „PDF öffnen“,
„Ordner öffnen“ and „CSV exportieren“, the state of the history's hash chain,
and „Zertifikat prüfen“: pick any certificate PDF and see in plain words
whether it is genuine (same three levels as the CLI and the verify page).
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cert.payload import RESULT_LABELS
from cert.verify_cert import TrustStore, VerifyOutcome, extract_token_from_pdf, verify_token
from core.serial import serial_for_filename, serial_text
from core.log import (
    CSV_HEADERS,
    audit_log,
    history_result_status,
    read_wipe_history,
    verify_wipe_history,
)
from gui import theme
from gui.i18n import language, language_half, pick, serial_label, tr
from gui.pages.wipe_views import open_path
from gui.widgets import ResultIcon, make_card, make_chip, make_label, set_prop, uppercase

COLUMNS = ("date", "device", "serial", "method", "result", "cert")
RESULT_COLUMN_WIDTH = 176  # room for the widest chip („FEHLGESCHLAGEN“)
_CHIP_TONES = {"ok": "accent", "limited": "warning", "failed": "danger"}
_VERIFY_TONES = {"trusted": "ok", "unknown_key": "limited", "invalid": "failed"}

# Reasons for outcomes found here (not by verify_token); "Deutsch / English" like the
# reasons verify_token gives, shown through language_half().
NO_TOKEN_REASON = (
    "Kein Prüfcode im PDF gefunden — das Zertifikat ist nicht signiert oder kein "
    "StickShredder-Zertifikat / No verification code found in the PDF — the certificate "
    "is unsigned or not a StickShredder certificate"
)
UNREADABLE_REASON = "Datei nicht lesbar: {error} / File not readable: {error}"


# -- Helpers --------------------------------------------------------------------------


def result_chip(raw) -> tuple[str, str]:
    """Chip text and tone for a history ``result`` value (2.0 or legacy SUCCESS/FAILED)."""
    status = history_result_status(raw)
    if status is None:
        return (str(raw or "").strip() or "—"), "neutral"
    return tr(f"certs.result.{status}"), _CHIP_TONES[status]


def cert_label(value) -> str:
    """``SS-000042`` — the number as on the PDF and its file name; empty if none."""
    try:
        return f"SS-{int(str(value).strip()):06d}"
    except (TypeError, ValueError):
        return str(value or "").strip()


def format_date(value) -> str:
    """History date (``2026-11-18 14:05:12``) the way the UI language writes it."""
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            moment = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return moment.strftime("%Y-%m-%d %H:%M" if language() == "en" else "%d.%m.%Y %H:%M")
    return text


def find_certificate_pdf(folder, number, serial: str = "") -> Path | None:
    """The certificate PDF ``SS-<number>_*.pdf`` in ``folder`` (the serial decides ties)."""
    try:
        n = int(str(number).strip())
    except (TypeError, ValueError):
        return None
    base = Path(folder)
    if not base.is_dir():
        return None
    matches = sorted(base.glob(f"SS-{n:06d}_*.pdf"))
    # "noserial" since 2.0; older files carry the raw value (e.g. zeros).
    names = {f"_{serial_for_filename(serial)}_"}
    if str(serial or "").strip():
        names.add(f"_{str(serial).strip()}_")
    for match in matches:
        if any(name in match.name for name in names):
            return match
    return matches[0] if matches else None


def _trust_store() -> TrustStore | None:
    import core.config as cfg

    try:
        return TrustStore.load(cfg.TRUST_FILE)
    except ValueError as exc:
        audit_log(f"Trusted key list not readable: {exc}")
        return None


def check_certificate(path) -> VerifyOutcome:
    """Check a certificate PDF against this PC's trusted keys (as ``verify-cert`` does)."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return VerifyOutcome("invalid", None, None, UNREADABLE_REASON.format(error=exc))
    token = extract_token_from_pdf(data)
    if not token:
        return VerifyOutcome("invalid", None, None, NO_TOKEN_REASON)
    store = _trust_store()
    return verify_token(token, store.as_dict() if store is not None else {})


def trusted_name(fingerprint: str | None) -> str:
    store = _trust_store()
    if store is None or not fingerprint:
        return ""
    from gui.pages.keys_page import display_key_name

    return display_key_name(store.as_dict().get(fingerprint.upper(), ""))


def pick_pdf(parent, title: str, file_filter: str) -> str:
    path, _ = QFileDialog.getOpenFileName(parent, title, "", file_filter)
    return path


def pick_save_path(parent, title: str, default: str, file_filter: str) -> str:
    path, _ = QFileDialog.getSaveFileName(parent, title, default, file_filter)
    return path


def _button(variant: str = "") -> QPushButton:
    button = QPushButton()
    if variant:
        button.setProperty("variant", variant)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


# -- Page -------------------------------------------------------------------------------


class CertificatesPage(QWidget):
    """History table, chain status, open/export, „Zertifikat prüfen“."""

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setObjectName("PageRoot")
        self._entries: list[dict] = []      # newest first, one per table row
        self._verify: tuple[str, VerifyOutcome] | None = None
        self._status: tuple[str, dict, str] | None = None  # key, values, tone
        self._loaded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.title_label = make_label("", "h1")
        self.subtitle_label = make_label("", "muted", wrap=True)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        head.addLayout(titles, 1)
        self.verify_button = _button("accent")
        self.verify_button.clicked.connect(lambda: self.verify_certificate())
        head.addWidget(self.verify_button, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(head)

        root.addWidget(self._build_verify_card())

        strip = QHBoxLayout()
        strip.setSpacing(16)
        self.count_label = make_label("", "section")
        self.chain_label = make_label("", "status", wrap=True)
        strip.addWidget(self.count_label)
        strip.addWidget(self.chain_label, 1)
        root.addLayout(strip)

        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.open_pdf_button = _button()
        self.open_pdf_button.clicked.connect(self.open_selected_pdf)
        self.open_folder_button = _button()
        self.open_folder_button.clicked.connect(self.open_folder)
        self.export_button = _button()
        self.export_button.clicked.connect(lambda: self.export_csv())
        tools.addWidget(self.search_edit, 1)
        for button in (self.open_pdf_button, self.open_folder_button, self.export_button):
            tools.addWidget(button)
        root.addLayout(tools)

        self.status_label = make_label("", "status", wrap=True)
        self.status_label.hide()
        root.addWidget(self.status_label)

        self.table_card = make_card("surface")
        table_layout = QVBoxLayout(self.table_card)
        table_layout.setContentsMargins(8, 8, 8, 8)
        self.table = self._build_table()
        table_layout.addWidget(self.table)
        self.no_match_label = make_label("", "hint")
        self.no_match_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_match_label.hide()
        table_layout.addWidget(self.no_match_label)
        root.addWidget(self.table_card, 1)

        self.empty_card = make_card("surface")
        empty = QVBoxLayout(self.empty_card)
        empty.setContentsMargins(28, 28, 28, 28)
        empty.setSpacing(10)
        self.empty_title = make_label("", "placeholder-title")
        self.empty_text = make_label("", "muted", wrap=True)
        empty.addWidget(self.empty_title)
        empty.addWidget(self.empty_text)
        root.addWidget(self.empty_card)
        self._empty_stretch = QWidget()
        root.addWidget(self._empty_stretch, 1)

        self.retranslate()
        self.refresh()

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMNS))
        table.setProperty("role", "history")
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        table.verticalHeader().setDefaultSectionSize(46)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for index, key in enumerate(COLUMNS):
            mode = {"device": QHeaderView.ResizeMode.Stretch,
                    "result": QHeaderView.ResizeMode.Fixed}.get(
                key, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(index, mode)
        table.setColumnWidth(COLUMNS.index("result"), RESULT_COLUMN_WIDTH)
        table.itemSelectionChanged.connect(self._sync_buttons)
        table.cellDoubleClicked.connect(lambda _r, _c: self.open_selected_pdf())
        return table

    def _build_verify_card(self) -> QWidget:
        self.verify_card = make_card("result", tone="ok")
        layout = QHBoxLayout(self.verify_card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(18)
        self.verify_icon = ResultIcon()
        layout.addWidget(self.verify_icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        self.verify_eyebrow = uppercase(make_label("", "result-eyebrow", tone="ok"))
        self.verify_title = make_label("", "verify-title", wrap=True)
        self.verify_text = make_label("", "result-value", wrap=True)
        self.verify_details = make_label("", "verify-details", wrap=True)
        self.verify_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.verify_note = make_label("", "hint", wrap=True)
        for widget in (self.verify_eyebrow, self.verify_title, self.verify_text,
                       self.verify_details, self.verify_note):
            texts.addWidget(widget)
        layout.addLayout(texts, 1)
        self.verify_close_button = _button("small")
        self.verify_close_button.clicked.connect(self.close_verify)
        layout.addWidget(self.verify_close_button, 0, Qt.AlignmentFlag.AlignTop)
        self.verify_card.hide()
        return self.verify_card

    # ── Data ──────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Read the history and its chain again."""
        try:
            history = read_wipe_history()
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            audit_log(f"History not readable: {exc}")
            history = []
        self._entries = list(reversed(history))
        self._loaded = True
        self._fill_table()
        self._update_chain()

    def _fill_table(self) -> None:
        table = self.table
        table.setRowCount(0)
        table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            values = {
                "date": format_date(entry.get("date")),
                "device": str(entry.get("device_model") or ""),
                "serial": serial_text(entry.get("serial_number"), language()),
                "method": str(entry.get("method") or ""),
                "cert": cert_label(entry.get("cert_number")),
            }
            for column, key in enumerate(COLUMNS):
                if key == "result":
                    text, tone = result_chip(entry.get("result"))
                    item = QTableWidgetItem("")  # the chip shows it
                    item.setData(Qt.ItemDataRole.UserRole, text)
                    table.setItem(row, column, item)
                    holder = QWidget()
                    box = QHBoxLayout(holder)
                    box.setContentsMargins(8, 0, 8, 0)
                    box.addWidget(make_chip(text, tone), 0, Qt.AlignmentFlag.AlignVCenter)
                    box.addStretch(1)
                    table.setCellWidget(row, column, holder)
                    continue
                item = QTableWidgetItem(values[key])
                if key in ("serial", "cert"):
                    item.setData(Qt.ItemDataRole.UserRole, "mono")
                    font = item.font()
                    font.setFamily(theme.fonts().mono)
                    item.setFont(font)
                table.setItem(row, column, item)
        has_rows = bool(self._entries)
        self.table_card.setVisible(has_rows)
        self.empty_card.setVisible(not has_rows)
        self._empty_stretch.setVisible(not has_rows)
        self.search_edit.setEnabled(has_rows)
        self.count_label.setText(
            tr("history.count_one") if len(self._entries) == 1
            else tr("history.count", n=len(self._entries)) if self._entries else "")
        self._apply_filter()

    def _update_chain(self) -> None:
        try:
            check = verify_wipe_history()
        except Exception as exc:  # noqa: BLE001 — a damaged file must not break the page
            audit_log(f"History check failed: {exc}")
            self.chain_label.setText(tr("history.unreadable"))
            set_prop(self.chain_label, "tone", "danger")
            self.chain_label.show()
            return
        if check.total == 0:
            self.chain_label.hide()
            return
        if check.ok:
            text = (tr("certs.chain_ok_one") if check.checked == 1
                    else tr("certs.chain_ok", n=check.checked))
            if check.legacy:
                text += " · " + tr("certs.chain_legacy", n=check.legacy)
            tone = "ok"
        else:
            text = tr("certs.chain_broken", n=check.broken_row,
                      reason=language_half(check.reason))
            tone = "danger"
        self.chain_label.setText(text)
        set_prop(self.chain_label, "tone", tone)
        self.chain_label.show()

    # ── Table access ──────────────────────────────────────────────────────────

    def cell_text(self, row: int, column: str) -> str:
        item = self.table.item(row, COLUMNS.index(column))
        if item is None:
            return ""
        if column == "result":
            return str(item.data(Qt.ItemDataRole.UserRole) or "")
        return item.text()

    def chip_at(self, row: int) -> tuple[str, str]:
        holder = self.table.cellWidget(row, COLUMNS.index("result"))
        chip = holder.layout().itemAt(0).widget()
        return chip.text(), chip.property("tone")

    def visible_rows(self) -> list[int]:
        return [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]

    def entries(self) -> list[dict]:
        return list(self._entries)

    def selected_entry(self) -> dict | None:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        rows = [r for r in rows if not self.table.isRowHidden(r)]
        return self._entries[rows[0]] if rows else None

    def _apply_filter(self) -> None:
        needle = self.search_edit.text().strip().lower()
        for row, entry in enumerate(self._entries):
            hay = " ".join([
                *(self.cell_text(row, key) for key in COLUMNS),
                str(entry.get("date") or ""), str(entry.get("operator") or ""),
            ]).lower()
            self.table.setRowHidden(row, bool(needle) and needle not in hay)
        visible = self.visible_rows()
        self.no_match_label.setVisible(bool(self._entries) and not visible)
        self.export_button.setEnabled(bool(visible))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        entry = self.selected_entry()
        self.open_pdf_button.setEnabled(entry is not None and bool(entry.get("cert_number")))

    # ── Actions ───────────────────────────────────────────────────────────────

    def _show_status(self, key: str, tone: str = "ok", **values) -> None:
        self._status = (key, values, tone)
        self.status_label.setText(tr(key, **values))
        set_prop(self.status_label, "tone", tone)
        self.status_label.show()

    def open_selected_pdf(self) -> bool:
        entry = self.selected_entry()
        if entry is None:
            return False
        number = entry.get("cert_number")
        if not str(number or "").strip():
            self._show_status("certs.no_cert", "danger")
            return False
        path = find_certificate_pdf(self.config.cert_output_dir, number,
                                    str(entry.get("serial_number") or ""))
        if path is None:
            self._show_status("certs.pdf_missing", "danger", cert=cert_label(number))
            return False
        if not open_path(str(path)):
            self._show_status("certs.open_failed", "danger", path=str(path))
            return False
        self.status_label.hide()
        return True

    def open_folder(self) -> bool:
        folder = self.config.cert_output_dir
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not open_path(folder):
            self._show_status("certs.open_failed", "danger", path=folder)
            return False
        return True

    def export_csv(self, path: str | None = None) -> int | None:
        """Write the rows the search shows (all columns, as ``history --export``)."""
        if path is None:
            path = pick_save_path(self, tr("certs.export_title"), "stickshredder-verlauf.csv",
                                  tr("certs.csv_filter"))
            if not path:
                return None
        rows = [self._entries[r] for r in self.visible_rows()]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
                writer.writeheader()
                for entry in rows:
                    writer.writerow({h: entry.get(h) or "" for h in CSV_HEADERS})
        except OSError as exc:
            self._show_status("certs.export_failed", "danger", error=str(exc))
            return None
        if len(rows) == 1:
            self._show_status("certs.exported_one", "ok", path=str(path))
        else:
            self._show_status("certs.exported", "ok", n=len(rows), path=str(path))
        return len(rows)

    def verify_certificate(self, path: str | None = None) -> VerifyOutcome | None:
        """„Zertifikat prüfen“: check a PDF and show the outcome on the page."""
        if path is None:
            path = pick_pdf(self, tr("certs.pick_title"), tr("certs.pdf_filter"))
            if not path:
                return None
        outcome = check_certificate(path)
        self._verify = (Path(path).name, outcome)
        self._render_verify()
        self.verify_card.show()
        return outcome

    def close_verify(self) -> None:
        self._verify = None
        self.verify_card.hide()

    def _render_verify(self) -> None:
        if self._verify is None:
            return
        file_name, outcome = self._verify
        tone = _VERIFY_TONES[outcome.status]
        set_prop(self.verify_card, "tone", tone)
        set_prop(self.verify_eyebrow, "tone", tone)
        self.verify_icon.set_tone(tone)
        self.verify_eyebrow.setText(tr("certs.verify.eyebrow"))
        self.verify_title.setText(tr(f"certs.verify.{outcome.status}"))
        if outcome.status == "trusted":
            name = trusted_name(outcome.fingerprint) or outcome.fingerprint or ""
            self.verify_text.setText(tr("certs.verify.trusted_text", name=name))
        elif outcome.status == "unknown_key":
            self.verify_text.setText(tr("certs.verify.unknown_text"))
        else:
            self.verify_text.setText(tr("certs.verify.invalid_text",
                                        reason=language_half(outcome.reason)))
        lines = (file_name, self._payload_text(outcome))
        self.verify_details.setText("\n".join(line for line in lines if line))
        self.verify_note.setVisible(outcome.status != "invalid")

    @staticmethod
    def _payload_text(outcome: VerifyOutcome) -> str:
        if outcome.status == "invalid" or not outcome.payload:
            return ""
        payload = outcome.payload
        parts = [cert_label(payload.get("n"))]
        if payload.get("dm"):
            parts.append(str(payload["dm"]))
        parts.append(serial_label(payload.get("ds")))
        stamp = str(payload.get("t") or "")[:10]
        if stamp:
            parts.append(format_date(stamp).split(" ")[0])
        if payload.get("r") in RESULT_LABELS:
            parts.append(pick(*RESULT_LABELS[payload["r"]]))
        lines = [" · ".join(p for p in parts if p)]
        if outcome.fingerprint:
            lines.append(tr("cert.key", fingerprint=outcome.fingerprint))
        return "\n".join(lines)

    # ── Language and theme ────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self.title_label.setText(tr("certs.title"))
        self.subtitle_label.setText(tr("certs.subtitle"))
        self.verify_button.setText(tr("certs.verify_button"))
        self.search_edit.setPlaceholderText(tr("certs.search"))
        self.open_pdf_button.setText(tr("certs.open_pdf"))
        self.open_folder_button.setText(tr("certs.open_folder"))
        self.export_button.setText(tr("certs.export"))
        self.table.setHorizontalHeaderLabels([tr(f"certs.col.{key}") for key in COLUMNS])
        self.no_match_label.setText(tr("certs.no_match"))
        self.empty_title.setText(tr("history.empty"))
        self.empty_text.setText(tr("certs.empty_text"))
        self.verify_close_button.setText(tr("certs.verify.close"))
        self.verify_note.setText(tr("certs.verify.note"))
        if self._status is not None and self.status_label.isVisibleTo(self):
            key, values, tone = self._status
            self._show_status(key, tone, **values)
        if self._loaded:
            selected = self.selected_entry()
            self._fill_table()
            if selected is not None:
                self.table.selectRow(self._entries.index(selected))
            self._update_chain()
        self._render_verify()

    def on_theme_changed(self) -> None:
        for widget in self.findChildren(QWidget):
            widget.update()
