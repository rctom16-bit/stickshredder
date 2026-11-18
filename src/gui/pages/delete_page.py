"""The Löschen page: choose a disk, confirm, watch the wipe, see the result.

Three states in one stack: selection (hero card, tiles, other disks,
certificate preview), running (:class:`RunningView`) and result
(:class:`ResultView`). The wipe itself runs in the existing
:class:`gui.wipe_worker.WipeWorker`; this page only uses its constructor and
its public signals, handled the same way the old main window handles them.
"""

from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.log import audit_log, verify_wipe_history
from gui import theme
from gui.dialogs import (
    WipeOptionsDialog,
    ask_confirmation,
    ask_yes_no,
    show_message,
)
from gui.i18n import (
    fmt_duration,
    fmt_estimate,
    fmt_eta,
    fmt_size,
    fmt_speed,
    language,
    language_half,
    pick,
    tr,
)
from gui.pages.wipe_views import (
    ResultView,
    RunningView,
    WipeOutcome,
    confirm_hardware_erase,
    erase_method_text,
    read_certificate,
    short_fingerprint,
    target_name,
)
from gui.widgets import (
    QrCode,
    Tile,
    icon_pixmap,
    make_card,
    make_chip,
    make_label,
    set_prop,
    uppercase,
)
from gui.wipe_options import (
    MULTI_PASS_KEYS,
    WipeOptions,
    build_method,
    estimate_seconds,
    hardware_erase_fits,
    hardware_erase_hint,
    method_label,
    run_options,
)
from gui.wipe_worker import UnlockWorker, WipeWorker
from wipe.demo import DEFAULT_DEMO_SIZE, create_demo_device
from wipe.device import DeviceInfo, is_safe_to_wipe, list_devices, unique_disks
from wipe.hardware_erase import TEMP_PASSWORD
from wipe.recommend import media_type_of, recommend

_MiB = 1024 * 1024

# Demo mode's second disk: an SSD that offers the hardware erase (Beta). The
# worker fakes the drive's erase for it; nothing real is touched.
DEMO_SSD_ID = "\\\\.\\DemoDevice1"
# Refusals that will not change by trying again: hide the offer for that disk.
_FINAL_REFUSALS = ("no_path", "unsupported", "locked")
_UNLOCK_KEYS = {f"unlock.state.{s}" for s in (
    "unlocked", "no_password", "frozen", "expired", "wrong_password", "unsupported", "error")}


def disk_key(device: DeviceInfo) -> tuple[str, str]:
    """A disk's identity: its number can pass to another disk after replugging."""
    return device.device_id, (device.serial_number or "").strip()


def is_demo(device: DeviceInfo) -> bool:
    return device.device_id.startswith("\\\\.\\DemoDevice")


def create_demo_ssd() -> DeviceInfo:
    """The demo SSD: fake, 10 MB, SATA, offers the hardware erase."""
    return DeviceInfo(
        drive_letter="",
        device_id=DEMO_SSD_ID,
        model="StickShredder Demo SSD",
        serial_number=f"DEMO-SSD-{os.urandom(2).hex().upper()}",
        capacity_bytes=DEFAULT_DEMO_SIZE,
        filesystem="RAW",
        connection_type="SATA",
        is_removable=False,
        is_system_drive=False,
        is_internal=False,
        friendly_name="Demo SSD",
    )


def is_selectable(device: DeviceInfo, allow_internal: bool) -> bool:
    """The system disk never; internal disks only after the opt-in."""
    if device.is_system_drive:
        return False
    if device.is_internal and not allow_internal:
        return False
    return True


def first_letter(device: DeviceInfo) -> str:
    letters = device.disk_letters or ([device.drive_letter] if device.drive_letter else [])
    return letters[0] if letters else "—"


def type_text(device: DeviceInfo, media: str) -> str:
    """Gerätetyp as the spec lists it (USB-Stick, USB-SSD, SATA-HDD, NVMe-SSD …)."""
    if is_demo(device):
        return tr("type.demo_ssd") if device.device_id == DEMO_SSD_ID else tr("type.demo")
    connection = (device.connection_type or "").upper()
    if connection == "USB":
        return {"flash": tr("type.usb_stick"), "hdd": tr("type.usb_hdd"),
                "ssd": tr("type.usb_ssd")}.get(media, tr("type.disk", connection="USB"))
    if connection == "NVME":
        return tr("type.nvme")
    if connection in ("SATA", "ATA", "IDE"):
        return {"hdd": tr("type.sata_hdd"), "ssd": tr("type.sata_ssd")}.get(
            media, tr("type.disk", connection=device.connection_type))
    if connection in ("SD", "MMC"):
        return tr("type.sd")
    return tr("type.disk", connection=device.connection_type or "?")


# -- Worker status messages (English) -> protocol lines ----------------------------------

_STATUS = [
    (re.compile(r"^Processing device (\d+)/(\d+): (.*)$"), "ws.processing", ("i", "n", "name")),
    (re.compile(r"^Demo Mode: creating virtual disk file\.\.\.$"), "ws.demo_create", ()),
    (re.compile(r"^Demo drive size: (.*)$"), "ws.demo_size", ("size",)),
    (re.compile(r"^Demo: wiping virtual disk with (.*)\.\.\.$"), "ws.demo_wipe", ("method",)),
    (re.compile(r"^Checking and dismounting (.*)\.\.\.$"), "ws.check", ("letters",)),
    (re.compile(r"^Opening (.*)\.\.\.$"), "ws.open", ("device",)),
    (re.compile(r"^Drive size: (.*)$"), "ws.size", ("size",)),
    (re.compile(r"^Wiping (.*) with (.*)\.\.\.$"), "ws.wipe", ("name", "method")),
    (re.compile(r"^Verifying (.*)\.\.\.$"), "ws.verify", ("name",)),
    (re.compile(r"^Reformatting (.*) to (.*)\.\.\.$"), "ws.reformat", ("letter", "fs")),
    (re.compile(r"^Demo: reformatted virtual disk to (.*)$"), "ws.demo_reformat", ("fs",)),
    (re.compile(r"^Generating certificate\.\.\.$"), "ws.cert", ()),
    (re.compile(r"^Checking hardware erase support\.\.\.$"), "ws.hw_check", ()),
    (re.compile(r"^Device (.*) completed successfully\.$"), "ws.done_ok", ("name",)),
    (re.compile(r"^Device (.*) completed with verification errors\.$"), "ws.done_errors",
     ("name",)),
    (re.compile(r"^Wipe cancelled( by user)?\.$"), "ws.cancelled", ("by",)),
    (re.compile(r"^Error on (.*?): (.*)$"), "ws.error", ("name", "message")),
]
# Shown through the page's own lines already (phase changes, error signal).
_QUIET_STATUS = {"ws.verify", "ws.error"}


def translate_status(message: str) -> tuple[str, str]:
    """``(key, text)`` for a worker status message; key "" when unknown."""
    for pattern, key, names in _STATUS:
        match = pattern.match(message.strip())
        if match:
            values = {name: (value or "") for name, value in zip(names, match.groups())}
            return key, tr(key, **values)
    return "", language_half(message)


# -- Disk rows ------------------------------------------------------------------------


class DiskRow(QFrame):
    """One physical disk in „Weitere Datenträger“."""

    clicked = Signal(object)

    def __init__(self, device: DeviceInfo, media: str, allow_internal: bool,
                 parent=None, *, hardware: bool = False) -> None:
        super().__init__(parent)
        self.device = device
        self._selectable = is_selectable(device, allow_internal)
        self.setProperty("card", "row")
        self.setProperty("selectable", "true" if self._selectable else "false")
        if self._selectable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        locked = "false" if self._selectable else "true"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)
        letter = make_label(first_letter(device), "row-letter", locked=locked)
        letter.setMinimumWidth(36)
        size = fmt_size(device.capacity_bytes)
        text = make_label(f"{device.model} · {type_text(device, media)} · {size}",
                          "row-text", locked=locked)
        text.setWordWrap(True)
        layout.addWidget(letter)
        layout.addWidget(text, 1)
        self._chips = []
        for chip_text, tone in self._chip_specs(device, allow_internal, hardware):
            chip = make_chip(chip_text, tone)
            self._chips.append(chip)
            layout.addWidget(chip)
        tip = []
        if device.serial_number:
            tip.append(tr("serial", serial=device.serial_number))
        tip.append(device.device_id)
        self.setToolTip(" · ".join(tip))
        self.setAccessibleName(f"{first_letter(device)} {device.model} {size}")

    @staticmethod
    def _chip_specs(device: DeviceInfo, allow_internal: bool,
                    hardware: bool = False) -> list[tuple[str, str]]:
        chips = []
        if device.is_system_drive:
            chips.append((tr("chip.system"), "danger"))
        elif device.is_internal:
            chips.append((tr("chip.internal") if allow_internal else tr("chip.internal_locked"),
                          "warning"))
        if is_demo(device):
            chips.append((tr("chip.demo"), "accent"))
        if device.has_bitlocker:
            chips.append((tr("chip.bitlocker"), "warning"))
        if device.has_active_processes:
            chips.append((tr("chip.in_use"), "neutral"))
        if hardware:
            chips.append((tr("chip.hardware"), "accent"))
        return chips

    def is_selectable(self) -> bool:
        return self._selectable

    def chip_texts(self) -> list[str]:
        return [chip.text() for chip in self._chips]

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt name
        if event.button() == Qt.MouseButton.LeftButton and self._selectable:
            self.clicked.emit(self.device)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._selectable and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                                Qt.Key.Key_Space):
            self.clicked.emit(self.device)
            return
        super().keyPressEvent(event)


# -- Run state ------------------------------------------------------------------------


@dataclass
class _Run:
    device: DeviceInfo
    options: WipeOptions
    started: float = field(default_factory=time.monotonic)
    phase: str = "write"           # write | verify | format | cert | done
    passes: int = 1
    current_pass: int = 0
    write_frac: float = 0.0
    verify_frac: float = 0.0
    milestones: set = field(default_factory=set)
    reformat_state: str = ""
    error: str = ""
    cancelled: bool = False
    hardware_report: object | None = None   # wipe.hardware_erase.EraseReport


class DeletePage(QWidget):
    """Selection → confirmation → running → result."""

    busy_changed = Signal(bool)

    def __init__(self, config, *, list_devices_fn=None, media_fn=None, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._list_devices = list_devices_fn or (lambda: list_devices())
        self._media_of = media_fn or (lambda device: media_type_of(device))
        self.options = WipeOptions.from_config(config)
        self.worker: WipeWorker | None = None
        self._devices: list[DeviceInfo] = []
        self._media: dict[tuple[str, str], str] = {}   # disk_key -> media type
        self._selected: DeviceInfo | None = None
        self._rows: list[DiskRow] = []
        self._run: _Run | None = None
        self._fingerprint: str | None = None
        # Hardware erase (Beta): the disk it was confirmed for, and disks whose
        # drive said a final "no" in this session (disk_key -> reason).
        self._hardware_key: tuple[str, str] | None = None
        self._no_hardware: dict[tuple[str, str], str] = {}
        self.unlock_worker: UnlockWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.select_view = self._build_select_view()
        self.running_view = RunningView()
        self.result_view = ResultView()
        self.running_view.cancel_requested.connect(self._on_cancel_clicked)
        self.result_view.next_requested.connect(self.back_to_selection)
        self.result_view.overwrite_requested.connect(self.overwrite_instead)
        self.result_view.unlock_requested.connect(self.unlock_disk)
        for view in (self.select_view, self.running_view, self.result_view):
            self.stack.addWidget(view)
        self.retranslate()

    # ── Selection view ────────────────────────────────────────────────────────

    def _build_select_view(self) -> QWidget:
        view = QWidget()
        view.setObjectName("PageRoot")
        root = QHBoxLayout(view)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("ScrollBody")
        columns = QHBoxLayout(body)
        columns.setContentsMargins(28, 28, 28, 28)
        columns.setSpacing(24)
        scroll.setWidget(body)
        root.addWidget(scroll)

        main = QVBoxLayout()
        main.setSpacing(20)
        columns.addLayout(main, 1)

        hero = make_card("surface")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(28, 28, 28, 28)
        hero_layout.setSpacing(18)
        head = QHBoxLayout()
        head.setSpacing(20)
        self.hero_letter = make_label("—", "hero-letter")
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.hero_title = make_label("", "h1", wrap=True)
        self.hero_subtitle = make_label("", "muted", wrap=True)
        self.hero_subtitle.setTextFormat(Qt.TextFormat.RichText)
        titles.addWidget(self.hero_title)
        titles.addWidget(self.hero_subtitle)
        self.choose_other_button = QPushButton()
        self.choose_other_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_other_button.clicked.connect(self._focus_other_disks)
        head.addWidget(self.hero_letter, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addLayout(titles, 1)
        head.addWidget(self.choose_other_button, 0, Qt.AlignmentFlag.AlignVCenter)
        hero_layout.addLayout(head)

        tiles = QGridLayout()
        tiles.setHorizontalSpacing(12)
        self.method_tile = Tile()
        self.verify_tile = Tile()
        self.class_tile = Tile()
        for column, tile in enumerate((self.method_tile, self.verify_tile, self.class_tile)):
            tiles.addWidget(tile, 0, column)
            tiles.setColumnStretch(column, 1)
        hero_layout.addLayout(tiles)

        # Hardware erase (Beta): only for disks it may fit (WMI facts only).
        self.hardware_card = make_card("tile")
        hardware = QVBoxLayout(self.hardware_card)
        hardware.setContentsMargins(14, 12, 14, 12)
        hardware.setSpacing(6)
        hardware_head = QHBoxLayout()
        hardware_head.setSpacing(10)
        self.hardware_title = make_label("", "tile-value")
        self.hardware_badge = make_chip("", "accent")
        self.hardware_button = QPushButton()
        self.hardware_button.setProperty("variant", "small")
        self.hardware_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hardware_button.clicked.connect(self._on_hardware_button)
        hardware_head.addWidget(self.hardware_title, 0, Qt.AlignmentFlag.AlignVCenter)
        hardware_head.addWidget(self.hardware_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        hardware_head.addStretch(1)
        hardware_head.addWidget(self.hardware_button)
        self.hardware_text = make_label("", "tile-note", wrap=True)
        hardware.addLayout(hardware_head)
        hardware.addWidget(self.hardware_text)
        hero_layout.addWidget(self.hardware_card)
        hint_row = QHBoxLayout()
        hint_row.setSpacing(12)
        self.hardware_hint = make_label("", "hint", wrap=True)
        # A disk the drive reported as password-locked: unlock it from here.
        self.unlock_link = QPushButton()
        self.unlock_link.setProperty("variant", "link")
        self.unlock_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unlock_link.clicked.connect(lambda: self._selected and self.unlock_disk(self._selected))
        hint_row.addWidget(self.hardware_hint, 1)
        hint_row.addWidget(self.unlock_link, 0, Qt.AlignmentFlag.AlignTop)
        hero_layout.addLayout(hint_row)

        under_tiles = QHBoxLayout()
        under_tiles.setSpacing(12)
        self.warning_label = make_label("", "warning", wrap=True)
        self.other_method_button = QPushButton()
        self.other_method_button.setProperty("variant", "link")
        self.other_method_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.other_method_button.clicked.connect(self.open_options)
        under_tiles.addWidget(self.warning_label, 1)
        under_tiles.addWidget(self.other_method_button, 0, Qt.AlignmentFlag.AlignTop)
        hero_layout.addLayout(under_tiles)

        self.delete_button = QPushButton()
        self.delete_button.setProperty("variant", "primary")
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.setIconSize(QSize(22, 22))
        self.delete_button.clicked.connect(self._on_delete_clicked)
        hero_layout.addWidget(self.delete_button)
        self.estimate_label = make_label("", "hint", wrap=True)
        self.estimate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self.estimate_label)
        main.addWidget(hero)

        others = QVBoxLayout()
        others.setSpacing(8)
        others_head = QHBoxLayout()
        self.others_title = make_label("", "section")
        self.refresh_button = QPushButton()
        self.refresh_button.setProperty("variant", "small")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setIconSize(QSize(16, 16))
        self.refresh_button.clicked.connect(self.refresh_devices)
        others_head.addWidget(self.others_title, 1, Qt.AlignmentFlag.AlignVCenter)
        others_head.addWidget(self.refresh_button)
        others.addLayout(others_head)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(8)
        others.addLayout(self._rows_layout)
        self.no_others_label = make_label("", "hint")
        others.addWidget(self.no_others_label)
        internal = QHBoxLayout()
        internal.setSpacing(12)
        self.allow_internal_checkbox = QCheckBox()
        self.allow_internal_checkbox.toggled.connect(self._on_allow_internal_toggled)
        self.allow_internal_hint = make_label("", "hint", wrap=True)
        internal.addWidget(self.allow_internal_checkbox)
        internal.addWidget(self.allow_internal_hint, 1)
        others.addSpacing(4)
        others.addLayout(internal)
        main.addLayout(others)
        main.addStretch(1)

        aside = QVBoxLayout()
        aside.setSpacing(16)
        columns.addLayout(aside)
        cert = make_card("cert")
        cert.setFixedWidth(300)
        cert_layout = QVBoxLayout(cert)
        cert_layout.setContentsMargins(22, 22, 22, 22)
        cert_layout.setSpacing(12)
        self.preview_eyebrow = uppercase(make_label("", "cert-eyebrow"))
        self.preview_title = make_label("", "cert-title", wrap=True)
        qr_row = QHBoxLayout()
        qr_row.setSpacing(14)
        self.preview_qr = QrCode(placeholder_size=96)
        self.preview_text = make_label("", "cert-body", wrap=True)
        qr_row.addWidget(self.preview_qr)
        qr_row.addWidget(self.preview_text, 1)
        self.preview_key = make_label("", "cert-mono")
        for widget in (self.preview_eyebrow, self.preview_title):
            cert_layout.addWidget(widget)
        cert_layout.addLayout(qr_row)
        cert_layout.addWidget(self.preview_key)
        aside.addWidget(cert)

        history = make_card("small")
        history.setFixedWidth(300)
        history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(18, 18, 18, 18)
        history_layout.setSpacing(6)
        self.history_title = make_label("", "history-label")
        self.history_count = make_label("", "history-value")
        self.history_status = make_label("", "history-status")
        for widget in (self.history_title, self.history_count, self.history_status):
            history_layout.addWidget(widget)
        aside.addWidget(history)
        aside.addStretch(1)
        return view

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def selected_device(self) -> DeviceInfo | None:
        return self._selected

    def current_view(self) -> str:
        return {self.select_view: "select", self.running_view: "running",
                self.result_view: "result"}[self.stack.currentWidget()]

    def is_busy(self) -> bool:
        wiping = self.worker is not None and self.worker.isRunning()
        unlocking = self.unlock_worker is not None and self.unlock_worker.isRunning()
        return wiping or unlocking

    def stop_work(self) -> None:
        """Quitting: cancel the wipe and wait for the threads to end."""
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5000)
        if self.unlock_worker is not None:
            self.unlock_worker.wait(5000)

    def disk_rows(self) -> list[DiskRow]:
        return list(self._rows)

    def row_for(self, device: DeviceInfo) -> DiskRow | None:
        for row in self._rows:
            if row.device.device_id == device.device_id:
                return row
        return None

    def refresh_devices(self) -> None:
        """Scan the disks again (WMI only)."""
        if self.is_busy():
            return
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText(tr("select.scanning"))
        QApplication.processEvents()
        try:
            found = self._list_devices()
        except Exception as exc:  # noqa: BLE001 — shown to the user
            audit_log(f"Device scan failed: {exc}")
            found = []
            show_message(self, tr("msg.scan_failed_title"),
                         tr("msg.scan_failed", error=exc), kind="warning")
        demos = [d for d in self._devices if is_demo(d)]
        self.set_devices(list(found) + demos)
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText(tr("select.refresh"))

    def set_devices(self, devices: list[DeviceInfo]) -> None:
        self._devices = unique_disks(list(devices))
        for device in self._devices:
            if disk_key(device) not in self._media:
                self._media[disk_key(device)] = self._media_for(device)
        allow = self.allow_internal_checkbox.isChecked()
        previous = self._selected
        self._selected = None
        if previous is not None:
            for device in self._devices:
                if device.device_id == previous.device_id and is_selectable(device, allow):
                    self._selected = device
                    break
        if self._selected is None:
            self._selected = next(
                (d for d in self._devices if is_selectable(d, False)), None)
        self._render_selection()

    def select_device(self, device: DeviceInfo) -> bool:
        if not is_selectable(device, self.allow_internal_checkbox.isChecked()):
            return False
        if all(d.device_id != device.device_id for d in self._devices):
            return False
        self._selected = device
        self._render_selection()
        return True

    def add_demo_device(self) -> DeviceInfo:
        """Add the demo stick (selected) and the demo SSD for the hardware erase."""
        demo = create_demo_device()
        self._devices.append(demo)
        self._media[disk_key(demo)] = "flash"
        if all(d.device_id != DEMO_SSD_ID for d in self._devices):
            ssd = create_demo_ssd()
            self._devices.append(ssd)
            self._media[disk_key(ssd)] = "ssd"
        self._selected = demo
        self._render_selection()
        return demo

    def set_options(self, options: WipeOptions) -> None:
        self.options = options
        self._render_selection()

    def open_options(self) -> None:
        recommended = self._recommendation().method_key if self._selected else "zero"
        dialog = WipeOptionsDialog(self.options, recommended_method=recommended, parent=self)
        if dialog.exec():
            chosen = dialog.options()
            if chosen.method_key != self.options.method_key:
                # Picking an overwrite method there means: overwrite only.
                chosen = replace(chosen, hardware_erase=False)
            self.set_options(chosen)

    def back_to_selection(self) -> None:
        self.stack.setCurrentWidget(self.select_view)
        self._update_preview()
        self.refresh_devices()

    def show_result(self, outcome: WipeOutcome) -> None:
        self.result_view.show_outcome(outcome)
        self.stack.setCurrentWidget(self.result_view)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _media_for(self, device: DeviceInfo) -> str:
        if is_demo(device):
            return "ssd" if device.device_id == DEMO_SSD_ID else "flash"
        try:
            return self._media_of(device)
        except Exception as exc:  # noqa: BLE001 — media type is best effort
            audit_log(f"Media type unknown for {device.device_id}: {exc}")
            return "unknown"

    def _recommendation(self):
        device = self._selected
        return recommend(device, self._media_type(device),
                         self._hardware_fits(device), self.options.schutzklasse)

    def _media_type(self, device: DeviceInfo) -> str:
        return self._media.get(disk_key(device), "unknown")

    def _beta_on(self) -> bool:
        return bool(getattr(self.config, "beta_features", False))

    def _hardware_possible(self, device: DeviceInfo | None) -> bool:
        """WMI facts and this session's refusals — never a drive command."""
        if device is None or disk_key(device) in self._no_hardware:
            return False
        return hardware_erase_fits(device, self._media_type(device),
                                   self.options.schutzklasse)

    def _hardware_fits(self, device: DeviceInfo | None) -> bool:
        """Offer the hardware erase? Only with „Beta-Funktionen“ switched on."""
        return self._beta_on() and self._hardware_possible(device)

    def _hardware_hint(self, device: DeviceInfo | None) -> str:
        if device is None:
            return ""
        reason = self._no_hardware.get(disk_key(device))
        if reason:
            return tr("hw.hint_refused", reason=tr(f"hw.short.{reason}"))
        if not self._beta_on() and self._hardware_possible(device):
            return tr("hw.hint_beta_off")
        return hardware_erase_hint(device, self._media_type(device))

    def _remember_refusal(self, device: DeviceInfo, reason: str) -> None:
        """The drive said a final "no": no offer for this disk in this session."""
        self._no_hardware[disk_key(device)] = reason

    def _forget_refusal(self, device: DeviceInfo) -> None:
        self._no_hardware.pop(disk_key(device), None)

    def _render_selection(self) -> None:
        device = self._selected
        if self.options.hardware_erase and (
                device is None or disk_key(device) != self._hardware_key
                or not self._hardware_fits(device)):
            # Chosen per disk, after its confirmation: another disk starts without.
            self.options = replace(self.options, hardware_erase=False)
        mono = theme.fonts().mono
        if device is None:
            self.hero_letter.setText("—")
            set_prop(self.hero_letter, "muted", "true")
            self.hero_title.setText(tr("select.no_disk_title"))
            self.hero_subtitle.setText(html.escape(tr("select.no_disk_hint")))
        else:
            self.hero_letter.setText(first_letter(device))
            set_prop(self.hero_letter, "muted", "false")
            self.hero_title.setText(device.model)
            media = self._media_type(device)
            serial = (tr("serial", serial=device.serial_number) if device.serial_number
                      else tr("no_serial"))
            self.hero_subtitle.setText(
                f"{html.escape(type_text(device, media))} · "
                f"{html.escape(fmt_size(device.capacity_bytes))} · "
                f"<span style=\"font-family:'{mono}'\">{html.escape(serial)}</span>")
        self._render_tiles()
        self._render_hardware()
        self._render_rows()
        self.delete_button.setEnabled(device is not None)
        self._update_preview()

    def _render_hardware(self) -> None:
        device = self._selected
        fits = self._hardware_fits(device)
        chosen = self.options.hardware_erase
        self.hardware_card.setVisible(fits)
        self.hardware_button.setText(tr("hw.use_overwrite") if chosen else tr("hw.use"))
        hint = "" if fits else self._hardware_hint(device)
        self.hardware_hint.setText(hint)
        self.hardware_hint.setVisible(bool(hint))
        locked = device is not None and self._no_hardware.get(disk_key(device)) == "locked"
        self.unlock_link.setVisible(locked)

    def _render_tiles(self) -> None:
        o = self.options
        device = self._selected
        rec = self._recommendation() if device is not None else None
        recommended_key = rec.method_key if rec else "zero"
        # For a fitting SSD the recommendation is the hardware erase + zeros (G6).
        hardware_recommended = bool(rec and rec.hardware_erase)
        if o.hardware_erase:
            note = tr("note.hardware_recommended" if hardware_recommended else "note.beta")
            tone = "accent"
        elif o.method_key == "custom":
            note, tone = tr("note.custom"), ""
        elif o.method_key in MULTI_PASS_KEYS:
            note, tone = tr("note.multi_pass"), "warning"
        elif o.method_key == recommended_key and not hardware_recommended:
            note, tone = tr("note.recommended"), "accent"
        else:
            note, tone = "", ""
        self.method_tile.set_values(method_label(o), note, tone)
        if run_options(o).verify_mode == "sample":
            self.verify_tile.set_values(tr("verify.sample"), tr("verify.sample_note"))
        else:
            self.verify_tile.set_values(tr("verify.full"), tr("verify.full_note"))
        category = "Purge" if o.hardware_erase else "Clear"
        self.class_tile.set_values(tr("class.value", category=category),
                                   tr("class.note", sk=o.schutzklasse))
        warnings = self._warnings_for(device) if device is not None else []
        self.warning_label.setText("\n".join(warnings))
        self.warning_label.setVisible(bool(warnings))
        if device is None:
            self.estimate_label.setText(tr("select.estimate_none"))
        else:
            seconds = estimate_seconds(o, device.capacity_bytes)
            key = "select.estimate_hardware" if o.hardware_erase else "select.estimate"
            self.estimate_label.setText(tr(key, time=fmt_estimate(seconds)))

    def _warnings_for(self, device: DeviceInfo) -> list[str]:
        media = self._media_type(device)
        fits = self._hardware_fits(device)
        chosen = fits and self.options.hardware_erase
        rec = recommend(device, media, chosen, self.options.schutzklasse)
        warnings = list(rec.warnings_en if language() == "en" else rec.warnings_de)
        if fits and not chosen:
            # The hardware erase is there, just not chosen: replace "no hardware
            # erase available" with the reason to choose it; keep the rest.
            plain = recommend(device, media, False, 1)
            unavailable = set(plain.warnings_en if language() == "en" else plain.warnings_de)
            warnings = [tr("hw.overwrite_only_warning")] + [
                w for w in warnings if w not in unavailable]
        if device.is_internal:
            warnings.append(tr("select.internal_warning"))
        return warnings

    def _render_rows(self) -> None:
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []
        allow = self.allow_internal_checkbox.isChecked()
        selected_id = self._selected.device_id if self._selected else None
        for device in self._devices:
            if device.device_id == selected_id:
                continue
            row = DiskRow(device, self._media_type(device), allow,
                          hardware=self._hardware_fits(device))
            row.clicked.connect(self.select_device)
            self._rows_layout.addWidget(row)
            self._rows.append(row)
        self.no_others_label.setVisible(not self._rows)

    def _update_preview(self) -> None:
        number = f"{self._next_cert_number():04d}"
        model = self._selected.model if self._selected else ""
        title = tr("cert_preview.title", n=number, model=model) if model else \
            tr("result.cert_number", n=number)
        self.preview_title.setText(title)
        fingerprint = self._key_fingerprint()
        self.preview_key.setText(tr("cert.key", fingerprint=short_fingerprint(fingerprint))
                                 if fingerprint else tr("cert.key_unreadable"))
        self._update_history()

    def _update_history(self) -> None:
        try:
            check = verify_wipe_history()
        except Exception as exc:  # noqa: BLE001 — a damaged history must not break the page
            audit_log(f"History check failed: {exc}")
            self.history_count.setText(tr("history.unreadable"))
            self.history_status.hide()
            return
        if check.total == 0:
            self.history_count.setText(tr("history.empty"))
            self.history_status.hide()
            return
        self.history_count.setText(tr("history.count_one") if check.total == 1
                                   else tr("history.count", n=check.total))
        self.history_status.show()
        if check.ok:
            self.history_status.setText(tr("history.chain_ok"))
            set_prop(self.history_status, "tone", "")
        else:
            self.history_status.setText(tr("history.chain_broken", n=check.broken_row))
            set_prop(self.history_status, "tone", "danger")

    @staticmethod
    def _next_cert_number() -> int:
        import core.config as cfg

        try:
            return int(cfg.CERT_COUNTER_FILE.read_text(encoding="utf-8").strip() or 0) + 1
        except (OSError, ValueError):
            return 1

    def _key_fingerprint(self) -> str:
        if self._fingerprint is None:
            import core.config as cfg

            try:
                self._fingerprint = cfg.load_signing_key().fingerprint()
            except Exception as exc:  # noqa: BLE001 — shown as "not readable"
                audit_log(f"Signing key not readable for the preview: {exc}")
                self._fingerprint = ""
        return self._fingerprint

    def _focus_other_disks(self) -> None:
        for row in self._rows:
            if row.is_selectable():
                row.setFocus(Qt.FocusReason.OtherFocusReason)
                return
        self.refresh_devices()

    def _on_allow_internal_toggled(self, checked: bool) -> None:
        if not checked and self._selected is not None and self._selected.is_internal:
            self._selected = next((d for d in self._devices if is_selectable(d, False)), None)
        self._render_selection()

    # ── Language and theme ────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self.choose_other_button.setText(tr("select.choose_other"))
        self.method_tile.set_label(tr("tile.method"))
        self.verify_tile.set_label(tr("tile.verify"))
        self.class_tile.set_label(tr("tile.class"))
        self.other_method_button.setText(tr("select.other_method"))
        self.delete_button.setText(tr("select.delete_button"))
        self.others_title.setText(tr("select.others"))
        self.refresh_button.setText(tr("select.refresh"))
        self.no_others_label.setText(tr("select.no_others"))
        self.allow_internal_checkbox.setText(tr("select.allow_internal"))
        self.allow_internal_hint.setText(tr("select.allow_internal_hint"))
        self.hardware_title.setText(tr("hw.card_title"))
        self.hardware_badge.setText(tr("note.beta"))
        self.hardware_text.setText(tr("hw.card_text"))
        self.unlock_link.setText(tr("unlock.link"))
        self.preview_eyebrow.setText(tr("cert_preview.eyebrow"))
        self.preview_qr.set_placeholder_text(tr("cert_preview.qr"))
        self.preview_text.setText(tr("cert_preview.text"))
        self.history_title.setText(tr("history.title"))
        self.running_view.retranslate()
        self.result_view.retranslate()
        self.on_theme_changed()
        self._render_selection()

    def on_theme_changed(self) -> None:
        p = theme.current()
        self.delete_button.setIcon(icon_pixmap("shred", 22, p.on_accent))
        self.refresh_button.setIcon(icon_pixmap("refresh", 16, p.text))
        for widget in self.findChildren(QWidget):
            widget.update()

    # ── Hardware erase (Beta) ─────────────────────────────────────────────────

    def _on_hardware_button(self) -> None:
        if self.options.hardware_erase:
            self.set_options(replace(self.options, hardware_erase=False))
            return
        device = self._selected
        if device is None or self.is_busy() or not self._hardware_fits(device):
            return
        if not confirm_hardware_erase(self, device):
            return
        self._hardware_key = disk_key(device)
        self.set_options(replace(self.options, hardware_erase=True))

    def unlock_disk(self, device: DeviceInfo) -> None:
        """„Gesperrte Platte entsperren“: ask, then one try in a worker thread."""
        if device is None or self.is_busy():
            return
        safe, reason = is_safe_to_wipe(device,
                                       allow_internal=self.allow_internal_checkbox.isChecked())
        if not safe:
            show_message(self, tr("msg.refused_title"),
                         f"{device.friendly_name or device.model}:\n\n{language_half(reason)}",
                         kind="error")
            return
        if not ask_yes_no(self, tr("unlock.title"),
                          tr("unlock.text", target=target_name(device), password=TEMP_PASSWORD)):
            return
        audit_log(f"Unlock requested (GUI): {device.device_id}")
        self.result_view.unlock_button.setEnabled(False)
        self.unlock_link.setEnabled(False)
        worker = UnlockWorker(device, parent=self)
        self.unlock_worker = worker
        # A slot of this page: the result arrives on the GUI thread.
        worker.unlock_finished.connect(self._on_unlock_finished)
        worker.start()

    @Slot(object)
    def _on_unlock_finished(self, result) -> None:
        worker, self.unlock_worker = self.unlock_worker, None
        if worker is None:
            return
        worker.wait(2000)
        device = worker.device
        self.result_view.unlock_button.setEnabled(True)
        self.unlock_link.setEnabled(True)
        state = result.state if f"unlock.state.{result.state}" in _UNLOCK_KEYS else "error"
        text = tr(f"unlock.state.{state}", error=language_half(result.error or "—"))
        if result.success:
            self._forget_refusal(device)
            self.result_view.unlock_done()
        show_message(self, tr("unlock.done_title"), text,
                     kind="info" if result.success else "warning")
        self._render_selection()

    def overwrite_instead(self) -> None:
        """After a refused or failed hardware erase: back to overwriting."""
        self.options = replace(self.options, hardware_erase=False)
        self.back_to_selection()

    # ── Wipe ──────────────────────────────────────────────────────────────────

    def _on_delete_clicked(self) -> None:
        device = self._selected
        if device is None or self.is_busy():
            return
        safe, reason = is_safe_to_wipe(device,
                                       allow_internal=self.allow_internal_checkbox.isChecked())
        if not safe:
            show_message(self, tr("msg.refused_title"),
                         f"{device.friendly_name or device.model}:\n\n{language_half(reason)}",
                         kind="error")
            return
        if not ask_confirmation(self, [device], self._warnings_for(device)):
            return
        self.start_wipe(device)

    def start_wipe(self, device: DeviceInfo) -> None:
        """Run the wipe of ``device`` with the current options (no more questions)."""
        o = run_options(self.options)
        method = build_method(o)
        self._run = _Run(device=device, options=o, passes=max(1, method.passes))
        self.worker = WipeWorker(
            devices=[device],
            wipe_method=method,
            config=self.config,
            schutzklasse=o.schutzklasse,
            operator=o.operator,
            verify_mode=o.verify_mode,
            reformat=o.reformat,
            reformat_label=o.reformat_label,
            hardware_erase=o.hardware_erase,
            parent=self,
        )
        if o.hardware_erase:
            self.worker.hardware_erase_started.connect(self._on_hardware_erase_started)
            self.worker.hardware_erase_finished.connect(self._on_hardware_erase_finished)
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.verify_progress.connect(self._on_verify_progress)
        self.worker.phase_changed.connect(self._on_phase_changed)
        self.worker.device_completed.connect(self._on_device_completed)
        self.worker.all_completed.connect(self._on_all_completed)
        self.worker.error.connect(self._on_wipe_error)
        self.worker.status_message.connect(self._on_status_message)
        self.worker.stall_detected.connect(self._on_stall_detected)

        self.running_view.start(device, o)
        if o.reformat == "none":
            self.running_view.set_step("format", "skipped")
        self.stack.setCurrentWidget(self.running_view)
        self.busy_changed.emit(True)
        audit_log(
            f"Starting wipe (new GUI): {device.device_id}, method={method.name}, "
            f"hardware_erase={o.hardware_erase}, schutzklasse={o.schutzklasse}, "
            f"verify_mode={o.verify_mode}, reformat={o.reformat}"
        )
        self.worker.start()

    def _update_overall(self) -> None:
        run = self._run
        if run is None:
            return
        if run.phase == "done":
            self.running_view.set_percent(1.0)
            return
        write_weight = float(max(1, run.passes))
        verify_weight = 1.0 if run.options.verify_mode == "full" else 0.0
        overall = (run.write_frac * write_weight + run.verify_frac * verify_weight) / (
            write_weight + verify_weight)
        self.running_view.set_percent(min(overall, 0.99))

    @Slot(int, int, int, int, int, float)
    def _on_progress(self, device_index: int, pass_num: int, total_passes: int,
                     bytes_written: int, total_bytes: int, speed: float) -> None:
        # Same guard as the old window: byte counts may arrive as negative
        # 32-bit values through Qt's int marshalling.
        if bytes_written < 0:
            bytes_written += 1 << 32
        if total_bytes < 0:
            total_bytes += 1 << 32
        run = self._run
        if run is None:
            return
        view = self.running_view
        if total_passes > 0:
            run.passes = total_passes
        if pass_num != run.current_pass:
            if run.current_pass:
                view.add_log(tr("log.pass_done", i=run.current_pass, n=run.passes,
                                size=fmt_size(total_bytes)))
            run.current_pass = pass_num
            view.add_log(tr("log.pass_started", i=pass_num, n=run.passes))
        fraction = min(1.0, bytes_written / total_bytes) if total_bytes > 0 else 0.0
        run.write_frac = ((max(1, pass_num) - 1) + fraction) / max(1, run.passes)
        if run.phase == "write":
            view.set_step("write", "active", run.write_frac)
        view.speed_tile.set_values(fmt_speed(speed))
        if speed > 0 and total_bytes > 0:
            remaining = (run.passes - pass_num) * total_bytes + (total_bytes - bytes_written)
            if run.options.verify_mode == "full":
                remaining += total_bytes
            view.eta_tile.set_values(fmt_eta(remaining / (speed * _MiB)))
        else:
            view.eta_tile.set_values(tr("eta.calculating"))
        self._update_overall()

    def _finish_write(self) -> None:
        run = self._run
        if run.phase == "write" and run.current_pass:
            self.running_view.add_log(tr("log.pass_done", i=run.current_pass, n=run.passes,
                                         size=fmt_size(run.device.capacity_bytes)))
        run.write_frac = 1.0
        self.running_view.set_step("write", "done")

    def _finish_verify(self) -> None:
        run = self._run
        run.verify_frac = 1.0
        state = "skipped" if run.options.verify_mode == "none" else "done"
        self.running_view.set_step("verify", state)

    @Slot(int, str)
    def _on_phase_changed(self, device_index: int, phase: str) -> None:
        run = self._run
        if run is None:
            return
        view = self.running_view
        if phase == "verifying" and run.phase == "write":
            self._finish_write()
            run.phase = "verify"
            view.set_step("verify", "active", 0.0)
            full = run.options.verify_mode == "full"
            view.add_log(tr("log.verify_full" if full else "log.verify_sample"), "accent")
            view.hint_label.setText(tr("running.hint_verify_full" if full
                                       else "running.hint_verify_sample"))
        elif phase == "reformatting" and run.phase in ("write", "verify"):
            if run.phase == "write":
                self._finish_write()
            self._finish_verify()
            run.phase = "format"
            view.set_step("format", "active", 0.5)
            view.add_log(tr("log.format"))
            view.hint_label.setText(tr("running.hint_format"))
        elif phase == "done" and run.phase != "done":
            if not run.error and not run.cancelled:
                if run.phase == "write":
                    self._finish_write()
                if run.phase in ("write", "verify"):
                    self._finish_verify()
                if run.phase == "format" and run.reformat_state != "skipped":
                    run.reformat_state = "done"
                view.set_step("format", "done" if run.reformat_state == "done" else "skipped")
                view.set_step("cert", "done")
                view.add_log(tr("log.done"), "accent")
            run.phase = "done"
        self._update_overall()

    @Slot(int, float, int, int, float)
    def _on_verify_progress(self, device_index: int, fraction: float, bytes_done: int,
                            total_bytes: int, speed: float) -> None:
        if bytes_done < 0:
            bytes_done += 1 << 32
        if total_bytes < 0:
            total_bytes += 1 << 32
        run = self._run
        if run is None:
            return
        if run.phase == "write":
            self._on_phase_changed(device_index, "verifying")
        if run.phase != "verify":
            return
        view = self.running_view
        run.verify_frac = max(0.0, min(1.0, fraction))
        view.set_step("verify", "active", run.verify_frac)
        percent = int(run.verify_frac * 100)
        for milestone in (25, 50, 75):
            if percent >= milestone and milestone not in run.milestones and percent < 100:
                run.milestones.add(milestone)
                view.add_log(tr("log.verify_progress", p=milestone))
        view.speed_tile.set_values(fmt_speed(speed))
        if speed > 0 and total_bytes > 0:
            view.eta_tile.set_values(fmt_eta((total_bytes - bytes_done) / (speed * _MiB)))
        self._update_overall()

    @Slot(str)
    def _on_status_message(self, message: str) -> None:
        run = self._run
        key, text = translate_status(message)
        lowered = message.lower()
        if run is not None and "reformat skipped" in lowered:
            run.reformat_state = "skipped"
        if run is not None and key == "ws.cert" and run.phase != "done":
            if run.phase == "write":
                self._finish_write()
            if run.phase in ("write", "verify"):
                self._finish_verify()
            if run.phase == "format" and run.reformat_state != "skipped":
                run.reformat_state = "done"
            self.running_view.set_step(
                "format", "done" if run.reformat_state == "done" else "skipped")
            self.running_view.set_step("cert", "active", 0.5)
            self.running_view.hint_label.setText(tr("running.hint_cert"))
            run.phase = "cert"
        if key in _QUIET_STATUS or not text:
            return
        tone = "danger" if key in ("ws.done_errors",) else ""
        self.running_view.add_log(text, tone)

    @Slot(int, str)
    def _on_wipe_error(self, device_index: int, message: str) -> None:
        run = self._run
        if run is None:
            return
        if message == "Cancelled by user":
            run.cancelled = True
            run.error = pick("Vom Benutzer abgebrochen", "Cancelled by the user")
            return
        run.error = language_half(message)
        self.running_view.add_log(tr("log.error", message=run.error), "danger")

    @Slot(int, str, int)
    def _on_hardware_erase_started(self, device_index: int, method: str,
                                   estimated_minutes: int) -> None:
        """The drive erases itself now: elapsed time, its estimate, no cancel."""
        run = self._run
        if run is None:
            return
        self.running_view.begin_hardware_erase(method, estimated_minutes)
        self.running_view.add_log(tr("log.hw_started", method=erase_method_text(method)),
                                  "accent")
        self.running_view.add_log(tr("log.hw_password", password=TEMP_PASSWORD))

    @Slot(int, object)
    def _on_hardware_erase_finished(self, device_index: int, report) -> None:
        run = self._run
        if run is None:
            return
        run.hardware_report = report
        erased = report.status == "erased"
        self.running_view.end_hardware_erase(erased)
        if erased:
            self.running_view.add_log(
                tr("log.hw_erased", time=fmt_duration(report.duration_seconds)), "accent")
            return
        self.running_view.add_log(
            tr("log.hw_refused" if report.status == "refused" else "log.hw_failed"), "danger")
        if report.status == "refused" and report.reason in _FINAL_REFUSALS:
            self._remember_refusal(run.device, report.reason)

    @Slot(int, int, str)
    def _on_stall_detected(self, device_index: int, seconds_stalled: int, hint: str) -> None:
        """No progress for a minute: warn visibly, never cancel on our own."""
        self.running_view.show_stall(seconds_stalled)
        self.running_view.add_log(tr("log.stall", s=seconds_stalled), "warning")

    @Slot(int, bool, str)
    def _on_device_completed(self, device_index: int, success: bool, cert_path: str) -> None:
        run = self._run
        if run is None:
            return
        token, payload, fingerprint = read_certificate(cert_path) if cert_path else ("", None, "")
        status = str(payload.get("r")) if payload else "failed"
        if status not in ("ok", "limited", "failed") or (not success and status == "ok"):
            status = "failed"
        entry, chain = 0, ""
        if payload:
            try:
                check = verify_wipe_history()
                entry = check.total
                chain = (tr("history.chain_ok") if check.ok
                         else tr("history.chain_broken", n=check.broken_row))
            except Exception as exc:  # noqa: BLE001
                audit_log(f"History check failed after wipe: {exc}")
        o = run.options
        outcome = WipeOutcome(
            device=run.device, status=status, cert_path=cert_path, payload=payload,
            token=token, fingerprint=fingerprint,
            duration_seconds=time.monotonic() - run.started,
            method_key=o.method_key, custom_passes=o.custom_passes,
            custom_pattern=o.custom_pattern, verify_mode=o.verify_mode,
            reformat=o.reformat, reformat_label=o.reformat_label,
            reformat_state=run.reformat_state, error=run.error, cancelled=run.cancelled,
            history_entry=entry, chain_text=chain,
            hardware_erase=o.hardware_erase, hardware=run.hardware_report,
        )
        self.running_view.stop()
        self.show_result(outcome)

    @Slot()
    def _on_all_completed(self) -> None:
        self.worker = None
        self._run = None
        self.running_view.stop()
        self.busy_changed.emit(False)
        self._update_preview()

    def _on_cancel_clicked(self) -> None:
        if self.worker is None:
            return
        if ask_yes_no(self, tr("running.cancel_title"), tr("running.cancel_text")):
            self.worker.cancel()
            self.running_view.cancel_button.setEnabled(False)
            self.running_view.add_log(tr("log.cancel_requested"), "warning")
