"""The two later states of the Löschen page: wipe running and result."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui.i18n import fmt_duration, fmt_size, tr
from gui.widgets import (
    PhaseStepper,
    QrCode,
    ResultIcon,
    StripedProgressBar,
    Tile,
    make_card,
    make_label,
    set_prop,
    uppercase,
)
from gui.wipe_options import FS_NAMES, WipeOptions, fs_label, method_short
from wipe.device import DeviceInfo

STEPS = ("write", "verify", "format", "cert")
MAX_LOG_LINES = 300


def target_name(device: DeviceInfo) -> str:
    """How the page names a disk: its drive letters, else its model."""
    letters = device.disk_letters or ([device.drive_letter] if device.drive_letter else [])
    return ", ".join(letters) if letters else (device.model or device.device_id)


def short_fingerprint(fingerprint: str) -> str:
    """Fingerprint on two lines of four groups (fits the 300 px card)."""
    groups = fingerprint.split("-")
    if len(groups) <= 4:
        return fingerprint
    return "-".join(groups[:4]) + "-\n" + "-".join(groups[4:])


def open_path(path: str) -> bool:
    """Open a file or folder with its Windows default program."""
    try:
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 — user action
        return True
    except (AttributeError, OSError):
        return False


def read_certificate(cert_path: str) -> tuple[str, dict | None, str]:
    """Token, signed payload and key fingerprint of a certificate PDF."""
    from cert.verify_cert import extract_token_from_pdf, verify_token

    try:
        data = Path(cert_path).read_bytes()
    except OSError:
        return "", None, ""
    token = extract_token_from_pdf(data) or ""
    if not token:
        return "", None, ""
    outcome = verify_token(token, trusted={})
    if outcome.status == "invalid":
        return token, None, ""
    return token, outcome.payload, outcome.fingerprint or ""


# -- Running ---------------------------------------------------------------------------


class RunningView(QWidget):
    """„F: wird gelöscht“: stepper, big percentage, striped bar, tiles, protocol."""

    cancel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PageRoot")
        root = QHBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(24)

        card = make_card("surface")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(22)

        header = QHBoxLayout()
        header.setSpacing(16)
        self.title_label = make_label("", "h1-running", wrap=True)
        self.subtitle_label = make_label("", "muted")
        header.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignBottom)
        header.addWidget(self.subtitle_label, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(header)

        self.stepper = PhaseStepper()
        layout.addWidget(self.stepper)

        numbers = QHBoxLayout()
        numbers.setSpacing(28)
        percent = QHBoxLayout()
        percent.setSpacing(0)
        self.percent_label = make_label("0", "percent")
        self.percent_unit = make_label(" %", "percent-unit")
        percent.addWidget(self.percent_label, 0, Qt.AlignmentFlag.AlignBottom)
        percent.addWidget(self.percent_unit, 0, Qt.AlignmentFlag.AlignBottom)
        numbers.addLayout(percent)
        tiles_box = QVBoxLayout()
        tiles_box.addStretch(1)
        tiles = QGridLayout()
        tiles.setHorizontalSpacing(12)
        self.speed_tile = Tile(mono=True, compact=True)
        self.eta_tile = Tile(mono=True, compact=True)
        self.bad_tile = Tile(mono=True, compact=True)
        for column, tile in enumerate((self.speed_tile, self.eta_tile, self.bad_tile)):
            tiles.addWidget(tile, 0, column)
            tiles.setColumnStretch(column, 1)
        tiles_box.addLayout(tiles)
        numbers.addLayout(tiles_box, 1)
        layout.addLayout(numbers)

        self.progress_bar = StripedProgressBar()
        layout.addWidget(self.progress_bar)

        self.stall_banner = make_card("banner")
        banner = QVBoxLayout(self.stall_banner)
        banner.setContentsMargins(14, 10, 14, 10)
        self.stall_label = make_label("", "banner", wrap=True)
        banner.addWidget(self.stall_label)
        self.stall_banner.hide()
        layout.addWidget(self.stall_banner)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.hint_label = make_label("", "hint", wrap=True)
        self.cancel_button = QPushButton()
        self.cancel_button.setProperty("variant", "danger-outline")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.cancel_requested)
        footer.addWidget(self.hint_label, 1)
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        main = QVBoxLayout()
        main.addWidget(card)
        main.addStretch(1)
        root.addLayout(main, 1)

        log_card = make_card("log")
        log_card.setFixedWidth(320)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(20, 20, 20, 20)
        log_layout.setSpacing(10)
        self.protocol_title = make_label("", "section")
        log_layout.addWidget(self.protocol_title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("ScrollBody")
        self._log_layout = QVBoxLayout(body)
        self._log_layout.setContentsMargins(0, 0, 0, 0)
        self._log_layout.setSpacing(3)
        self._log_layout.addStretch(1)
        scroll.setWidget(body)
        self._log_scroll = scroll
        bar = scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _low, high: bar.setValue(high))
        log_layout.addWidget(scroll, 1)
        root.addWidget(log_card)

        self._log_labels = []
        self._device: DeviceInfo | None = None
        self.retranslate()

    # -- content --

    def retranslate(self) -> None:
        self.stepper.set_labels([tr(f"step.{s}") for s in STEPS])
        self.speed_tile.set_label(tr("tile.speed"))
        self.eta_tile.set_label(tr("tile.eta"))
        self.bad_tile.set_label(tr("tile.bad"))
        self.cancel_button.setText(tr("running.cancel"))
        self.protocol_title.setText(tr("protocol.title"))
        if self._device is not None:
            self._set_header(self._device)

    def _set_header(self, device: DeviceInfo) -> None:
        self.title_label.setText(tr("running.title", target=target_name(device)))
        self.subtitle_label.setText(f"{device.model} · {fmt_size(device.capacity_bytes)}")

    def start(self, device: DeviceInfo, options: WipeOptions) -> None:
        self._device = device
        self._set_header(device)
        self.stepper.reset()
        self.stepper.set_step(0, "active", 0.0)
        if options.verify_mode == "none":
            self.stepper.set_step(1, "skipped")
        self.set_percent(0.0)
        self.progress_bar.setTone("accent")
        self.progress_bar.setAnimated(True)
        self.speed_tile.set_values("–")
        self.eta_tile.set_values(tr("eta.calculating"))
        self.bad_tile.set_values("—", tr("tile.bad_pending"))
        self.hint_label.setText(tr("running.hint_write"))
        self.stall_banner.hide()
        self.cancel_button.setEnabled(True)
        for label in self._log_labels:
            label.deleteLater()
        self._log_labels = []

    def stop(self) -> None:
        self.progress_bar.setAnimated(False)
        self.cancel_button.setEnabled(False)

    def set_percent(self, fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        self.percent_label.setText(str(int(fraction * 100 + 1e-6)))
        self.progress_bar.setValue(fraction)

    def show_stall(self, seconds: int) -> None:
        self.stall_label.setText(tr("running.stall", s=seconds))
        self.stall_banner.show()
        self.progress_bar.setTone("warning")

    def add_log(self, text: str, tone: str = "") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        label = make_label(f"{stamp} {text}", "log-line", wrap=True)
        if tone:
            set_prop(label, "tone", tone)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._log_layout.insertWidget(self._log_layout.count() - 1, label)
        self._log_labels.append(label)
        while len(self._log_labels) > MAX_LOG_LINES:
            self._log_labels.pop(0).deleteLater()

    def protocol_texts(self) -> list[str]:
        return [label.text() for label in self._log_labels]


# -- Result ---------------------------------------------------------------------------


@dataclass
class WipeOutcome:
    device: DeviceInfo
    status: str                       # ok | limited | failed
    cert_path: str = ""
    payload: dict | None = None
    token: str = ""
    fingerprint: str = ""
    duration_seconds: float = 0.0
    method_key: str = "zero"
    custom_passes: int = 3
    custom_pattern: str = "zero"
    verify_mode: str = "full"
    reformat: str = "none"
    reformat_label: str = ""
    reformat_state: str = ""          # "" (infer) | done | skipped
    error: str = ""
    cancelled: bool = False
    history_entry: int = 0
    chain_text: str = ""

    def options(self) -> WipeOptions:
        return WipeOptions(method_key=self.method_key, custom_passes=self.custom_passes,
                           custom_pattern=self.custom_pattern, verify_mode=self.verify_mode,
                           reformat=self.reformat, reformat_label=self.reformat_label)


class ResultView(QWidget):
    """Three-level result with summary, actions and the signed certificate."""

    next_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self.cert_path = ""
        self._outcome: WipeOutcome | None = None
        root = QHBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(24)

        main = QVBoxLayout()
        main.setSpacing(20)
        self.panel = make_card("result", tone="ok")
        panel = QVBoxLayout(self.panel)
        panel.setContentsMargins(32, 32, 32, 32)
        panel.setSpacing(18)
        head = QHBoxLayout()
        head.setSpacing(18)
        self.icon = ResultIcon()
        head.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.eyebrow_label = uppercase(make_label("", "result-eyebrow", tone="ok"))
        self.title_label = make_label("", "h1-result", wrap=True)
        titles.addWidget(self.eyebrow_label)
        titles.addWidget(self.title_label)
        head.addLayout(titles, 1)
        panel.addLayout(head)
        self._rows_grid = QGridLayout()
        self._rows_grid.setHorizontalSpacing(24)
        self._rows_grid.setVerticalSpacing(10)
        self._rows_grid.setColumnStretch(1, 1)
        panel.addLayout(self._rows_grid)
        self._row_labels: list[tuple] = []
        main.addWidget(self.panel)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.open_cert_button = QPushButton()
        self.open_cert_button.setProperty("variant", "accent")
        self.open_folder_button = QPushButton()
        self.open_folder_button.setProperty("variant", "large")
        self.next_button = QPushButton()
        self.next_button.setProperty("variant", "large")
        for button in (self.open_cert_button, self.open_folder_button, self.next_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        actions.addStretch(1)
        main.addLayout(actions)
        self.open_cert_button.clicked.connect(self._open_cert)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.next_button.clicked.connect(self.next_requested)

        self.note_card = make_card("note")
        note = QVBoxLayout(self.note_card)
        note.setContentsMargins(18, 16, 18, 16)
        self.note_label = make_label("", "note", wrap=True)
        note.addWidget(self.note_label)
        main.addWidget(self.note_card)
        main.addStretch(1)
        root.addLayout(main, 1)

        aside = QVBoxLayout()
        aside.setSpacing(16)
        self.cert_card = make_card("cert")
        self.cert_card.setFixedWidth(320)
        cert = QVBoxLayout(self.cert_card)
        cert.setContentsMargins(24, 24, 24, 24)
        cert.setSpacing(14)
        self.cert_eyebrow = uppercase(make_label("", "cert-eyebrow"))
        self.cert_number_label = make_label("", "cert-number")
        self.cert_lines_label = make_label("", "cert-lines", wrap=True)
        self.qr = QrCode(placeholder_size=112, module_px=2)
        self.qr_text_label = make_label("", "cert-body", wrap=True)
        self.fingerprint_label = make_label("", "cert-mono")
        self.fingerprint_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for widget in (self.cert_eyebrow, self.cert_number_label, self.cert_lines_label):
            cert.addWidget(widget)
        cert.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignLeft)
        cert.addWidget(self.qr_text_label)
        cert.addWidget(self.fingerprint_label)
        aside.addWidget(self.cert_card)
        self.history_card = make_card("small")
        self.history_card.setFixedWidth(320)
        history = QVBoxLayout(self.history_card)
        history.setContentsMargins(18, 16, 18, 16)
        self.history_label = make_label("", "note", wrap=True)
        history.addWidget(self.history_label)
        aside.addWidget(self.history_card)
        aside.addStretch(1)
        root.addLayout(aside)
        self.retranslate()

    # -- content --

    def retranslate(self) -> None:
        self.open_cert_button.setText(tr("result.open_cert"))
        self.open_folder_button.setText(tr("result.open_folder"))
        self.next_button.setText(tr("result.next"))
        self.cert_eyebrow.setText(tr("result.cert_eyebrow"))
        self.qr_text_label.setText(tr("result.qr_text"))
        if self._outcome is not None:
            self.show_outcome(self._outcome)

    def show_outcome(self, outcome: WipeOutcome) -> None:
        self._outcome = outcome
        self.cert_path = outcome.cert_path
        status = outcome.status if outcome.status in ("ok", "limited", "failed") else "failed"
        target = target_name(outcome.device)
        set_prop(self.panel, "tone", status)
        set_prop(self.eyebrow_label, "tone", status)
        self.icon.set_tone(status)
        self.eyebrow_label.setText(tr(f"result.eyebrow.{status}"))
        title_key = "result.title.cancelled" if outcome.cancelled else f"result.title.{status}"
        self.title_label.setText(tr(title_key, target=target))
        self._fill_rows(outcome, status)

        note = {"limited": tr("result.note_limited"),
                "failed": tr("result.note_failed")}.get(status, "")
        self.note_label.setText(note)
        self.note_card.setVisible(bool(note))

        payload = outcome.payload
        self.cert_eyebrow.setText(tr("result.cert_eyebrow" if payload
                                     else "result.cert_eyebrow_none"))
        self.open_cert_button.setEnabled(bool(outcome.cert_path))
        self.open_folder_button.setEnabled(bool(outcome.cert_path))
        if payload:
            self.cert_number_label.setText(tr("result.cert_number", n=f"{int(payload.get('n', 0)):04d}"))
            lines = [
                f"{payload.get('dm') or outcome.device.model} · SN {payload.get('ds') or '—'}",
                tr("result.cert_class", level=payload.get("sl") or "—", category="Clear"),
            ]
            if payload.get("op"):
                lines.append(tr("result.operator", name=payload["op"]))
            self.cert_lines_label.setText("\n".join(lines))
            self.qr.set_text(_verify_link(outcome.token) if outcome.token else None)
            self.qr.setVisible(self.qr.has_code())
            self.qr_text_label.setVisible(self.qr.has_code())
            self.fingerprint_label.setText(
                tr("cert.key", fingerprint=short_fingerprint(outcome.fingerprint)))
            self.fingerprint_label.setVisible(bool(outcome.fingerprint))
            self.history_label.setText(tr("result.history", n=outcome.history_entry,
                                          chain=outcome.chain_text))
            self.history_card.setVisible(bool(outcome.history_entry))
        else:
            self.cert_number_label.setText("—")
            self.cert_lines_label.setText(tr("result.no_cert"))
            self.qr.set_text(None)
            self.qr.hide()
            self.qr_text_label.hide()
            self.fingerprint_label.hide()
            self.history_card.hide()

    def _fill_rows(self, outcome: WipeOutcome, status: str) -> None:
        for key_label, value_label in self._row_labels:
            key_label.deleteLater()
            value_label.deleteLater()
        self._row_labels = []
        payload = outcome.payload or {}
        rows: list[tuple[str, str]] = []
        if payload:
            passes = int(payload.get("p", 1) or 1)
            passes_text = tr("val.pass_one") if passes == 1 else tr("val.passes", n=passes)
            size = fmt_size(int(payload.get("dc") or outcome.device.capacity_bytes))
            rows.append((tr("row.written"),
                         f"{size} · {passes_text} {method_short(outcome.options())}"))
            mismatches = int(payload.get("ve", 0) or 0)
            vm = payload.get("vm", outcome.verify_mode)
            verified = {"full": tr("val.verified_full", n=mismatches),
                        "sample": tr("val.verified_sample", n=mismatches)}.get(
                vm, tr("val.verified_none"))
            rows.append((tr("row.verified"), verified))
            bad = int(payload.get("bs", 0) or 0)
            rows.append((tr("row.bad"), tr("val.bad_none") if bad == 0
                         else tr("val.bad_some", n=bad)))
            rows.append((tr("row.formatted"), self._format_text(outcome, status)))
        if outcome.error:
            rows.append((tr("row.error"), outcome.error))
        rows.append((tr("row.duration"), fmt_duration(outcome.duration_seconds)))
        for index, (key, value) in enumerate(rows):
            key_label = make_label(key, "result-key", tone=status)
            value_label = make_label(value, "result-value", wrap=True)
            self._rows_grid.addWidget(key_label, index, 0, Qt.AlignmentFlag.AlignTop)
            self._rows_grid.addWidget(value_label, index, 1)
            self._row_labels.append((key_label, value_label))

    @staticmethod
    def _format_text(outcome: WipeOutcome, status: str) -> str:
        if outcome.reformat not in FS_NAMES:
            return tr("val.not_formatted")
        if outcome.reformat_state == "skipped":
            return tr("val.format_skipped")
        done = outcome.reformat_state == "done" or (
            outcome.reformat_state == "" and status != "failed")
        if not done:
            return tr("val.not_formatted")
        return tr("val.formatted", fs=FS_NAMES[outcome.reformat],
                  label=fs_label(outcome.options()))

    def summary_rows(self) -> dict[str, str]:
        return {key.text(): value.text() for key, value in self._row_labels}

    def _open_cert(self) -> None:
        if self.cert_path:
            open_path(self.cert_path)

    def _open_folder(self) -> None:
        if self.cert_path:
            open_path(str(Path(self.cert_path).parent))


def _verify_link(token: str) -> str:
    from cert.signing import verify_url

    return verify_url(token)
