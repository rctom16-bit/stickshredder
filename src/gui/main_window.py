"""Main application window for StickShredder."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig
from core.log import audit_log, read_wipe_history
from gui.history_dialog import HistoryDialog
from gui.settings_dialog import SettingsDialog
from gui.wipe_worker import WipeWorker
from wipe.demo import create_demo_device
from wipe.device import DeviceInfo, list_devices
from wipe.methods import BsiVsitr, CustomWipe, RandomThreePass, ZeroFill, WipeMethod


# ── Constants ────────────────────────────────────────────────────────────

_DEVICE_COLUMNS = [
    "",  # checkbox
    "Drive",
    "Model",
    "Serial Number",
    "Capacity",
    "Filesystem",
    "Connection",
    "Status",
]

_WIPE_METHODS = [
    ("Quick / Zero-Fill (1 pass)", "quick"),
    ("Standard / 3-Pass Random", "standard"),
    ("BSI VSITR / 7-Pass", "bsi"),
    ("Custom / Benutzerdefiniert", "custom"),
]

_SK_INFO = {
    1: "Schutzklasse 1 \u2014 Normaler Schutzbedarf (normal protection)",
    2: "Schutzklasse 2 \u2014 Hoher Schutzbedarf (high protection)",
    3: "Schutzklasse 3 \u2014 Sehr hoher Schutzbedarf (very high protection)",
}

_ESTIMATE_MBPS = 80.0  # conservative MB/s estimate for time calculations


class MainWindow(QMainWindow):
    """StickShredder main window."""

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.devices: list[DeviceInfo] = []
        self.worker: WipeWorker | None = None
        self._device_checkboxes: list[QCheckBox] = []

        self.setWindowTitle("StickShredder v1.0.0 \u2014 Secure USB Wipe Tool")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 740)

        self._build_menu_bar()
        self._build_central_widget()
        self._build_status_bar()

        # Initial device scan (deferred so the window shows first)
        QTimer.singleShot(200, self._refresh_devices)

    # ══════════════════════════════════════════════════════════════════════
    #  Menu bar
    # ══════════════════════════════════════════════════════════════════════

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        # File
        file_menu = menu_bar.addMenu("File")

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)

        demo_action = QAction("Demo Mode / Testmodus", self)
        demo_action.triggered.connect(self._activate_demo_mode)
        file_menu.addAction(demo_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # View
        view_menu = menu_bar.addMenu("View")

        history_action = QAction("Wipe History...", self)
        history_action.triggered.connect(self._open_history)
        view_menu.addAction(history_action)

        # Help
        help_menu = menu_bar.addMenu("Help")

        about_action = QAction("About StickShredder", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ══════════════════════════════════════════════════════════════════════
    #  Central widget
    # ══════════════════════════════════════════════════════════════════════

    def _build_central_widget(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Left: device panel + progress
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        left_layout.addWidget(self._build_device_panel())
        left_layout.addWidget(self._build_progress_panel())

        splitter.addWidget(left_widget)

        # Right: wipe controls
        splitter.addWidget(self._build_control_panel())

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

    # ── Device panel ─────────────────────────────────────────────────────

    def _build_device_panel(self) -> QGroupBox:
        group = QGroupBox("Detected Devices / Erkannte Datentrager")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Header row with refresh button
        header_row = QHBoxLayout()
        device_label = QLabel("Select devices to wipe:")
        device_label.setObjectName("sectionLabel")
        header_row.addWidget(device_label)
        header_row.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("refreshButton")
        self.refresh_btn.clicked.connect(self._refresh_devices)
        header_row.addWidget(self.refresh_btn)
        layout.addLayout(header_row)

        # Device table
        self.device_table = QTableWidget()
        self.device_table.setColumnCount(len(_DEVICE_COLUMNS))
        self.device_table.setHorizontalHeaderLabels(_DEVICE_COLUMNS)
        self.device_table.setAlternatingRowColors(True)
        self.device_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.device_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.device_table.verticalHeader().setVisible(False)

        header = self.device_table.horizontalHeader()
        if header is not None:
            # Checkbox column: fixed narrow
            header.setSectionResizeMode(
                0, QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(0, 32)
            # Drive letter: fixed narrow
            header.setSectionResizeMode(
                1, QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(1, 52)
            # Model: stretch (takes available space)
            header.setSectionResizeMode(
                2, QHeaderView.ResizeMode.Stretch
            )
            # Serial Number: interactive, wider default
            header.setSectionResizeMode(
                3, QHeaderView.ResizeMode.Interactive
            )
            header.resizeSection(3, 140)
            # Capacity: fixed
            header.setSectionResizeMode(
                4, QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(4, 80)
            # Filesystem: fixed
            header.setSectionResizeMode(
                5, QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(5, 80)
            # Connection: fixed
            header.setSectionResizeMode(
                6, QHeaderView.ResizeMode.Fixed
            )
            header.resizeSection(6, 90)
            # Status: stretch to fill remaining
            header.setSectionResizeMode(
                7, QHeaderView.ResizeMode.Stretch
            )

        # Empty state overlay label
        self._empty_state_label = QLabel(
            "No removable devices detected.\n"
            "Connect a USB drive and click Refresh.",
            self.device_table,
        )
        self._empty_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state_label.setStyleSheet(
            "color: #718096; font-size: 14px; background: transparent;"
            " padding: 40px;"
        )
        self._empty_state_label.setWordWrap(True)
        self._empty_state_label.hide()

        layout.addWidget(self.device_table)

        return group

    # ── Control panel ────────────────────────────────────────────────────

    def _build_control_panel(self) -> QGroupBox:
        group = QGroupBox("Wipe Configuration / Loschoptionen")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        # Wipe method
        form = QFormLayout()
        form.setSpacing(8)

        self.method_combo = QComboBox()
        for label, key in _WIPE_METHODS:
            self.method_combo.addItem(label, key)
        self.method_combo.currentIndexChanged.connect(self._on_method_changed)

        # Set default from config
        method_map = {"quick": 0, "standard": 1, "bsi": 2, "custom": 3}
        self.method_combo.setCurrentIndex(
            method_map.get(self.config.default_wipe_method, 1)
        )
        form.addRow("Wipe Method:", self.method_combo)

        # Method info card
        self.method_info = QLabel()
        self.method_info.setWordWrap(True)
        self.method_info.setStyleSheet(
            "QLabel {"
            "  color: #2d3748;"
            "  font-size: 11px;"
            "  padding: 8px 10px;"
            "  background-color: #f7fafc;"
            "  border: 1px solid #e2e8f0;"
            "  border-radius: 4px;"
            "  line-height: 1.4;"
            "}"
        )
        form.addRow("", self.method_info)

        # Estimated time
        self.estimate_label = QLabel("Select a device to see estimate")
        self.estimate_label.setStyleSheet("color: #4a5568; font-size: 11px;")
        form.addRow("Est. Time:", self.estimate_label)

        layout.addLayout(form)

        # Custom options (hidden by default)
        self.custom_group = QGroupBox("Custom Options")
        custom_layout = QFormLayout(self.custom_group)

        self.custom_passes_spin = QSpinBox()
        self.custom_passes_spin.setRange(1, 35)
        self.custom_passes_spin.setValue(3)
        custom_layout.addRow("Passes:", self.custom_passes_spin)

        self.custom_pattern_combo = QComboBox()
        self.custom_pattern_combo.addItem("Zero (0x00)", "zero")
        self.custom_pattern_combo.addItem("Ones (0xFF)", "ones")
        self.custom_pattern_combo.addItem("Random", "random")
        custom_layout.addRow("Pattern:", self.custom_pattern_combo)

        self.custom_group.setVisible(False)
        layout.addWidget(self.custom_group)

        # Schutzklasse
        sk_group = QGroupBox("Schutzklasse / Protection Class")
        sk_layout = QVBoxLayout(sk_group)

        self.sk_combo = QComboBox()
        self.sk_combo.addItem(
            "Klasse 1 \u2014 Normaler Schutzbedarf", 1
        )
        self.sk_combo.addItem(
            "Klasse 2 \u2014 Hoher Schutzbedarf", 2
        )
        self.sk_combo.addItem(
            "Klasse 3 \u2014 Sehr hoher Schutzbedarf", 3
        )
        self.sk_combo.setCurrentIndex(self.config.default_schutzklasse - 1)
        sk_layout.addWidget(self.sk_combo)

        self.sk_desc = QLabel()
        self.sk_desc.setWordWrap(True)
        self.sk_desc.setStyleSheet("color: #4a5568; font-size: 11px;")
        sk_layout.addWidget(self.sk_desc)
        self.sk_combo.currentIndexChanged.connect(self._on_sk_changed)
        self._on_sk_changed()

        layout.addWidget(sk_group)

        # Operator
        operator_layout = QFormLayout()
        self.operator_edit = QLineEdit()
        self.operator_edit.setText(self.config.operator_name)
        self.operator_edit.setPlaceholderText("Operator name")
        operator_layout.addRow("Operator:", self.operator_edit)
        layout.addLayout(operator_layout)

        # Verification options
        verify_group = QGroupBox("Verification / Verifikation")
        verify_layout = QVBoxLayout(verify_group)
        verify_layout.setSpacing(4)

        self.full_verify_cb = QCheckBox(
            "Vollst\u00e4ndige Verifikation (verdoppelt Laufzeit) / "
            "Full verification (doubles runtime)"
        )
        self.full_verify_cb.setChecked(False)
        self.full_verify_cb.setToolTip(
            "Liest nach dem \u00dcberschreiben jeden Sektor erneut und "
            "vergleicht mit dem erwarteten Muster. Erkennt stumm "
            "fehlschlagende Sektoren. Verdoppelt etwa die Laufzeit.\n\n"
            "Reads every sector after wiping and compares against the "
            "expected pattern. Detects silently failing sectors. Roughly "
            "doubles runtime."
        )
        self.full_verify_cb.stateChanged.connect(self._update_time_estimate)
        verify_layout.addWidget(self.full_verify_cb)

        verify_hint = QLabel(
            "Standard: Stichprobenpr\u00fcfung (100 Sektoren) / "
            "Default: sample check (100 sectors)"
        )
        verify_hint.setWordWrap(True)
        verify_hint.setStyleSheet("color: #718096; font-size: 10px;")
        verify_layout.addWidget(verify_hint)

        layout.addWidget(verify_group)

        layout.addStretch()

        # Wipe button
        self.wipe_btn = QPushButton("\u26a0 L\u00f6schen starten / Start Wipe")
        self.wipe_btn.setObjectName("wipeButton")
        self.wipe_btn.setMinimumHeight(42)
        self.wipe_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.wipe_btn.setEnabled(False)  # disabled until devices are checked
        self.wipe_btn.clicked.connect(self._on_wipe_clicked)
        layout.addWidget(self.wipe_btn)

        # Update method info
        self._on_method_changed()

        return group

    # ── Progress panel ───────────────────────────────────────────────────

    def _build_progress_panel(self) -> QGroupBox:
        group = QGroupBox("Progress / Fortschritt")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Phase badge — shows Idle / Wiping / Verifying / Complete
        self.phase_label = QLabel(self._phase_text("idle"))
        self.phase_label.setObjectName("phaseLabel")
        self.phase_label.setStyleSheet(self._phase_style("idle"))
        layout.addWidget(self.phase_label)

        # Current device info
        self.progress_device_label = QLabel("No operation in progress.")
        self.progress_device_label.setObjectName("statusLabel")
        layout.addWidget(self.progress_device_label)

        # Per-device progress
        self.device_progress = QProgressBar()
        self.device_progress.setRange(0, 100)
        self.device_progress.setValue(0)
        self.device_progress.setFormat("Device: %p%")
        layout.addWidget(self.device_progress)

        # Progress details
        details_row = QHBoxLayout()
        self.pass_label = QLabel("Pass: \u2013/\u2013")
        self.pass_label.setStyleSheet("font-weight: bold;")
        details_row.addWidget(self.pass_label)
        details_row.addSpacing(12)
        self.speed_label = QLabel("Speed: \u2013")
        details_row.addWidget(self.speed_label)
        details_row.addSpacing(12)
        self.eta_label = QLabel("ETA: \u2013")
        details_row.addWidget(self.eta_label)
        details_row.addStretch()
        layout.addLayout(details_row)

        # Verify progress bar (hidden until verify phase begins)
        self.verify_progress_bar = QProgressBar()
        self.verify_progress_bar.setRange(0, 100)
        self.verify_progress_bar.setValue(0)
        self.verify_progress_bar.setFormat("Verify: %p%")
        self.verify_progress_bar.setVisible(False)
        layout.addWidget(self.verify_progress_bar)

        # Batch progress
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 100)
        self.batch_progress.setValue(0)
        self.batch_progress.setFormat("Overall: %p%")
        layout.addWidget(self.batch_progress)

        # Cancel button
        self.cancel_btn = QPushButton("Cancel / Abbrechen")
        self.cancel_btn.setObjectName("cancelButton")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(self.cancel_btn)

        return group

    # ── Phase helpers ────────────────────────────────────────────────────

    @staticmethod
    def _phase_text(phase: str) -> str:
        """DE/EN human label for a worker phase. Module-level testable."""
        return {
            "idle": "Bereit / Ready",
            "wiping": "\u00dcberschreiben... / Wiping...",
            "verifying": "Verifikation... / Verifying...",
            "done": "Fertig / Complete",
        }.get(phase, phase)

    @staticmethod
    def _phase_style(phase: str) -> str:
        """Qt stylesheet for the phase badge. Color-coded per phase."""
        base = (
            "QLabel#phaseLabel {"
            " padding: 4px 10px;"
            " border-radius: 4px;"
            " font-weight: bold;"
            " font-size: 12px;"
            "}"
        )
        color = {
            "idle": "QLabel#phaseLabel { background-color: #edf2f7; color: #4a5568; }",
            "wiping": "QLabel#phaseLabel { background-color: #fefcbf; color: #744210; }",
            "verifying": "QLabel#phaseLabel { background-color: #bee3f8; color: #2c5282; }",
            "done": "QLabel#phaseLabel { background-color: #c6f6d5; color: #22543d; }",
        }.get(phase, "QLabel#phaseLabel { background-color: #edf2f7; color: #4a5568; }")
        return base + "\n" + color

    def _set_phase(self, phase: str) -> None:
        """Update the phase badge label and style."""
        self.phase_label.setText(self._phase_text(phase))
        self.phase_label.setStyleSheet(self._phase_style(phase))

    # ── Status bar ───────────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

        self.status_label = QLabel("Ready")
        status_bar.addWidget(self.status_label, 1)

        self.wipe_count_label = QLabel()
        status_bar.addPermanentWidget(self.wipe_count_label)
        self._update_wipe_count()

    def _update_wipe_count(self) -> None:
        try:
            history = read_wipe_history()
            count = len(history)
        except Exception:
            count = 0
        self.wipe_count_label.setText(f"Total wipes: {count}")

    # ══════════════════════════════════════════════════════════════════════
    #  Device management
    # ══════════════════════════════════════════════════════════════════════

    def _refresh_devices(self) -> None:
        self.status_label.setText("Scanning devices...")
        self.refresh_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            self.devices = list_devices()
        except Exception as exc:
            audit_log(f"Device scan failed: {exc}")
            self.devices = []
            QMessageBox.warning(
                self,
                "Device Scan Error",
                f"Could not enumerate devices:\n{exc}\n\n"
                "Make sure you are running as Administrator.",
            )

        self._populate_device_table()
        self.refresh_btn.setEnabled(True)
        self.status_label.setText(
            f"Found {len(self.devices)} device(s). Ready."
        )

    def _populate_device_table(self) -> None:
        self.device_table.setRowCount(0)
        self._device_checkboxes.clear()

        # Show/hide empty state overlay
        if not self.devices:
            self._empty_state_label.show()
            self._resize_empty_state()
            return
        self._empty_state_label.hide()

        for device in self.devices:
            row = self.device_table.rowCount()
            self.device_table.insertRow(row)

            # Checkbox
            cb = QCheckBox()
            if device.is_system_drive:
                cb.setEnabled(False)
                cb.setToolTip("System drive \u2014 cannot be wiped")
            elif device.has_bitlocker:
                cb.setToolTip(
                    "BitLocker encrypted \u2014 will need to unlock first"
                )

            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.device_table.setCellWidget(row, 0, cb_widget)
            self._device_checkboxes.append(cb)
            cb.stateChanged.connect(self._on_selection_changed)

            # Drive letter
            self.device_table.setItem(
                row, 1, QTableWidgetItem(device.drive_letter)
            )

            # Model
            self.device_table.setItem(
                row, 2, QTableWidgetItem(device.model)
            )

            # Serial
            self.device_table.setItem(
                row, 3, QTableWidgetItem(device.serial_number)
            )

            # Capacity
            cap_text = f"{device.capacity_gb} GB"
            self.device_table.setItem(
                row, 4, QTableWidgetItem(cap_text)
            )

            # Filesystem
            self.device_table.setItem(
                row, 5, QTableWidgetItem(device.filesystem)
            )

            # Connection
            self.device_table.setItem(
                row, 6, QTableWidgetItem(device.connection_type)
            )

            # Status -- colored indicators
            status_parts: list[str] = []
            if device.is_system_drive:
                status_parts.append("SYSTEM")
            if device.has_bitlocker:
                status_parts.append("\U0001F512 BitLocker")
            if device.is_internal and not device.is_system_drive:
                status_parts.append("INTERNAL")
            if device.has_active_processes:
                status_parts.append("In Use")

            status_text = (
                " | ".join(status_parts) if status_parts else "OK"
            )
            status_item = QTableWidgetItem(status_text)

            # Color-code the status text
            if device.is_system_drive:
                status_item.setForeground(QBrush(QColor("#c53030")))
                sf = status_item.font()
                sf.setBold(True)
                status_item.setFont(sf)
            elif device.is_internal:
                status_item.setForeground(QBrush(QColor("#c05621")))
                sf = status_item.font()
                sf.setBold(True)
                status_item.setFont(sf)
            elif device.has_bitlocker:
                status_item.setForeground(QBrush(QColor("#b7791f")))
            else:
                status_item.setForeground(QBrush(QColor("#276749")))
                sf = status_item.font()
                sf.setBold(True)
                status_item.setFont(sf)

            self.device_table.setItem(row, 7, status_item)

            # System drive rows: gray + italic across all data cells
            if device.is_system_drive:
                gray_brush = QBrush(QColor("#a0aec0"))
                italic_font = QFont()
                italic_font.setItalic(True)
                for col in range(len(_DEVICE_COLUMNS)):
                    item = self.device_table.item(row, col)
                    if item is not None:
                        item.setForeground(gray_brush)
                        item.setFont(italic_font)
                        item.setFlags(
                            item.flags() & ~Qt.ItemFlag.ItemIsSelectable
                        )
                # Keep status column distinct: red bold italic
                si = self.device_table.item(row, 7)
                if si is not None:
                    si.setForeground(QBrush(QColor("#c53030")))
                    bold_italic = QFont()
                    bold_italic.setItalic(True)
                    bold_italic.setBold(True)
                    si.setFont(bold_italic)

    def _resize_empty_state(self) -> None:
        """Position the empty-state label to fill the table viewport."""
        self._empty_state_label.setGeometry(self.device_table.rect())

    def resizeEvent(self, event) -> None:
        """Keep the empty-state label sized correctly on window resize."""
        super().resizeEvent(event)
        if hasattr(self, "_empty_state_label") and self._empty_state_label.isVisible():
            self._resize_empty_state()

    def _selected_devices(self) -> list[DeviceInfo]:
        """Return the list of devices whose checkboxes are checked."""
        selected: list[DeviceInfo] = []
        for i, cb in enumerate(self._device_checkboxes):
            if cb.isChecked() and i < len(self.devices):
                selected.append(self.devices[i])
        return selected

    def _on_selection_changed(self) -> None:
        """Update time estimate and wipe button state when selection changes."""
        selected = self._selected_devices()
        self.wipe_btn.setEnabled(len(selected) > 0)
        self._update_time_estimate()

    # ══════════════════════════════════════════════════════════════════════
    #  Wipe method controls
    # ══════════════════════════════════════════════════════════════════════

    def _on_method_changed(self) -> None:
        # This handler can fire while the control panel is still being built
        # (method_combo.setCurrentIndex() triggers currentIndexChanged before
        # the sibling widgets below are created). Guard accordingly.
        if not hasattr(self, "custom_group") or not hasattr(self, "method_info"):
            return
        method_key = self.method_combo.currentData()
        self.custom_group.setVisible(method_key == "custom")

        info_map = {
            "quick": (
                "\u26a1 <b>Zero-Fill</b> &mdash; 1 pass &nbsp;|&nbsp; "
                "Security: \u2605\u2606\u2606 &nbsp;|&nbsp; Speed: fastest<br/>"
                "<span style='color:#718096;'>DIN 66399 Sicherheitsstufe 1\u20132</span><br/>"
                "<i>Recommended for internal reuse of non-sensitive media.</i>"
            ),
            "standard": (
                "\U0001f6e1 <b>3-Pass Random</b> &mdash; 3 passes &nbsp;|&nbsp; "
                "Security: \u2605\u2605\u2606 &nbsp;|&nbsp; Speed: moderate<br/>"
                "<span style='color:#718096;'>DIN 66399 Sicherheitsstufe 3</span><br/>"
                "<i>Recommended for DSGVO-compliant disposal of standard office data.</i>"
            ),
            "bsi": (
                "\U0001f512 <b>BSI VSITR</b> &mdash; 7 passes &nbsp;|&nbsp; "
                "Security: \u2605\u2605\u2605 &nbsp;|&nbsp; Speed: slow<br/>"
                "<span style='color:#718096;'>DIN 66399 Sicherheitsstufe 4+</span><br/>"
                "<i>Recommended for classified data requiring highest-level sanitization.</i>"
            ),
            "custom": (
                "\u2699 <b>Custom</b> &mdash; user-defined passes and pattern<br/>"
                "<i>Configure pass count and pattern below.</i>"
            ),
        }
        self.method_info.setTextFormat(Qt.TextFormat.RichText)
        self.method_info.setText(info_map.get(method_key, ""))
        self._update_time_estimate()

    def _on_sk_changed(self) -> None:
        sk_value = self.sk_combo.currentData()
        if sk_value is not None:
            self.sk_desc.setText(_SK_INFO.get(sk_value, ""))

    def _get_wipe_method(self) -> WipeMethod:
        """Create the WipeMethod instance from the current UI selection."""
        method_key = self.method_combo.currentData()
        match method_key:
            case "quick":
                return ZeroFill()
            case "standard":
                return RandomThreePass()
            case "bsi":
                return BsiVsitr()
            case "custom":
                return CustomWipe(
                    passes=self.custom_passes_spin.value(),
                    pattern=self.custom_pattern_combo.currentData() or "zero",
                )
            case _:
                return RandomThreePass()

    def _update_time_estimate(self) -> None:
        selected = self._selected_devices()
        if not selected:
            self.estimate_label.setText("Select a device to see estimate")
            return

        method = self._get_wipe_method()
        total_bytes = sum(d.capacity_bytes for d in selected)
        total_mb = total_bytes / (1024 * 1024)
        total_seconds = (total_mb / _ESTIMATE_MBPS) * method.passes

        # Full verification reads the drive once more at roughly the same speed.
        verify_full = (
            hasattr(self, "full_verify_cb") and self.full_verify_cb.isChecked()
        )
        if verify_full:
            total_seconds += total_mb / _ESTIMATE_MBPS

        if total_seconds < 60:
            time_str = f"~{int(total_seconds)}s"
        elif total_seconds < 3600:
            time_str = f"~{int(total_seconds / 60)}min"
        else:
            hours = int(total_seconds // 3600)
            mins = int((total_seconds % 3600) // 60)
            time_str = f"~{hours}h {mins}min"

        verify_suffix = " + full verify" if verify_full else ""
        self.estimate_label.setText(
            f"{time_str} for {len(selected)} device(s), "
            f"{method.passes} pass(es){verify_suffix}"
        )

    # ══════════════════════════════════════════════════════════════════════
    #  Wipe execution
    # ══════════════════════════════════════════════════════════════════════

    def _on_wipe_clicked(self) -> None:
        selected = self._selected_devices()
        if not selected:
            QMessageBox.information(
                self,
                "No Devices Selected",
                "Please select at least one device to wipe.",
            )
            return

        # Safety check: SSD / internal drive warning
        non_removable = [d for d in selected if not d.is_removable]
        if non_removable and self.config.show_ssd_warning:
            names_html = "".join(
                f"<li><b>{d.friendly_name}</b> ({d.connection_type})</li>"
                for d in non_removable
            )
            ssd_box = QMessageBox(self)
            ssd_box.setIcon(QMessageBox.Icon.Warning)
            ssd_box.setWindowTitle("SSD / Internal Drive Warning")
            ssd_box.setTextFormat(Qt.TextFormat.RichText)
            ssd_box.setText(
                "<b>The following device(s) appear to be non-removable "
                "(SSD/internal):</b>"
            )
            ssd_box.setInformativeText(
                f"<ul>{names_html}</ul>"
                "<p><b>Why does this matter?</b> SSDs use wear-leveling "
                "algorithms that remap data blocks internally. Software "
                "overwrites cannot guarantee every physical cell is erased. "
                "Some residual data may remain recoverable by forensic tools.</p>"
                "<p>For complete SSD sanitization, <b>ATA Secure Erase</b> or "
                "<b>NVMe Format</b> (issued via manufacturer tooling) is "
                "recommended instead.</p>"
                "<p>Continue with software wipe anyway?</p>"
            )
            ssd_box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            ssd_box.setDefaultButton(QMessageBox.StandardButton.No)
            if ssd_box.exec() != QMessageBox.StandardButton.Yes:
                return

        # Confirmation dialog 1 -- bold device names, Warning icon
        device_list_html = "".join(
            f"<li><b>{d.drive_letter} &mdash; {d.model}</b> "
            f"(SN: {d.serial_number}, {d.capacity_gb} GB)</li>"
            for d in selected
        )
        confirm_box = QMessageBox(self)
        confirm_box.setIcon(QMessageBox.Icon.Warning)
        confirm_box.setWindowTitle("\u26a0 Confirm Data Destruction")
        confirm_box.setTextFormat(Qt.TextFormat.RichText)
        confirm_box.setText(
            "<span style='font-size:14px;'><b>\u26a0 WARNING: "
            "This will permanently destroy ALL data on the "
            "following device(s):</b></span>"
        )
        confirm_box.setInformativeText(
            f"<ul>{device_list_html}</ul>"
            "<p style='color:#c53030;'><b>This operation CANNOT be undone.</b></p>"
            "<p>Are you absolutely sure you want to proceed?</p>"
        )
        confirm_box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        confirm_box.setDefaultButton(QMessageBox.StandardButton.No)
        if confirm_box.exec() != QMessageBox.StandardButton.Yes:
            return

        # Confirmation dialog 2: type DELETE -- prominent custom dialog
        delete_dialog = QDialog(self)
        delete_dialog.setWindowTitle("\U0001f6d1 Final Confirmation")
        delete_dialog.setMinimumWidth(420)
        dl = QVBoxLayout(delete_dialog)
        dl.setSpacing(12)
        dl.setContentsMargins(20, 20, 20, 20)

        dl.addWidget(QLabel(
            '<p style="font-size:13px;">'
            'Type <b style="color:#c53030; font-size:16px;">DELETE</b> '
            "in the field below to confirm the wipe operation:</p>"
        ))

        delete_input = QLineEdit()
        delete_input.setPlaceholderText("Type DELETE here")
        delete_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        delete_input.setMinimumHeight(44)
        delete_input.setStyleSheet(
            "QLineEdit {"
            "  font-size: 18px;"
            "  font-weight: bold;"
            "  letter-spacing: 4px;"
            "  border: 2px solid #e2e8f0;"
            "  border-radius: 4px;"
            "  padding: 6px;"
            "}"
            "QLineEdit:focus {"
            "  border-color: #c53030;"
            "}"
        )
        dl.addWidget(delete_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_del_btn = QPushButton("Cancel")
        cancel_del_btn.clicked.connect(delete_dialog.reject)
        btn_row.addWidget(cancel_del_btn)
        confirm_del_btn = QPushButton("Confirm Wipe")
        confirm_del_btn.setDefault(True)
        confirm_del_btn.setStyleSheet(
            "QPushButton { background-color: #c53030; color: white; "
            "padding: 6px 18px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background-color: #9b2c2c; }"
        )
        confirm_del_btn.clicked.connect(delete_dialog.accept)
        btn_row.addWidget(confirm_del_btn)
        dl.addLayout(btn_row)

        if delete_dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_label.setText("Wipe cancelled.")
            return
        if delete_input.text().strip().upper() != "DELETE":
            self.status_label.setText(
                "Wipe cancelled \u2014 confirmation text did not match."
            )
            return

        # Start the wipe
        self._start_wipe(selected)

    def _start_wipe(self, devices: list[DeviceInfo]) -> None:
        """Launch the wipe worker thread."""
        method = self._get_wipe_method()
        sk_value = self.sk_combo.currentData() or 2
        verify_mode = "full" if self.full_verify_cb.isChecked() else "sample"

        # Track the exact subset being wiped so progress signals can look
        # up the correct DeviceInfo. self.devices contains ALL discovered
        # devices, but the worker emits indices into its own (selected)
        # list — indexing into self.devices would show the wrong name.
        self._wiping_devices: list[DeviceInfo] = list(devices)

        self.worker = WipeWorker(
            devices=devices,
            wipe_method=method,
            config=self.config,
            schutzklasse=sk_value,
            operator=self.operator_edit.text().strip(),
            verify_mode=verify_mode,
            parent=self,
        )
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.verify_progress.connect(self._on_verify_progress)
        self.worker.phase_changed.connect(self._on_phase_changed)
        self.worker.device_completed.connect(self._on_device_completed)
        self.worker.all_completed.connect(self._on_all_completed)
        self.worker.error.connect(self._on_wipe_error)
        self.worker.status_message.connect(self._on_status_message)

        # Disable controls during wipe
        self._set_controls_enabled(False)
        self.cancel_btn.setEnabled(True)

        self.device_progress.setValue(0)
        self.device_progress.setStyleSheet("")  # reset color from prior run
        self.verify_progress_bar.setValue(0)
        self.verify_progress_bar.setVisible(False)
        self.batch_progress.setValue(0)
        self.batch_progress.setMaximum(len(devices) * 100)

        self._completed_count = 0
        self._total_devices = len(devices)
        self._verify_mode = verify_mode

        audit_log(
            f"Starting batch wipe: {len(devices)} device(s), "
            f"method={method.name}, schutzklasse={sk_value}, "
            f"verify_mode={verify_mode}"
        )

        self.worker.start()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.wipe_btn.setEnabled(enabled)
        self.method_combo.setEnabled(enabled)
        self.sk_combo.setEnabled(enabled)
        self.custom_group.setEnabled(enabled)
        self.refresh_btn.setEnabled(enabled)
        self.operator_edit.setEnabled(enabled)
        if hasattr(self, "full_verify_cb"):
            self.full_verify_cb.setEnabled(enabled)
        for cb in self._device_checkboxes:
            cb.setEnabled(enabled and not self.devices[
                self._device_checkboxes.index(cb)
            ].is_system_drive)

    # ── Worker signal handlers ───────────────────────────────────────────

    @Slot(int, int, int, int, int, float)
    def _on_progress(
        self,
        device_index: int,
        pass_num: int,
        total_passes: int,
        bytes_written: int,
        total_bytes: int,
        speed: float,
    ) -> None:
        # Show device name next to progress bar. Use the wipe-specific
        # list (the selected subset passed to the worker), NOT self.devices
        # (the full discovered list) — the indices come from the worker.
        wiping = getattr(self, "_wiping_devices", self.devices)
        if device_index < len(wiping):
            dev = wiping[device_index]
            device_label = f"{dev.model} ({dev.drive_letter})"
        else:
            device_label = f"Device {device_index}"

        if total_bytes > 0:
            pct = int((bytes_written / total_bytes) * 100)
            self.device_progress.setValue(min(pct, 100))
            self.device_progress.setFormat(f"{device_label}: %p%")
        else:
            pct = 0

        self.progress_device_label.setText(f"Wiping: {device_label}")

        # Pass info
        self.pass_label.setText(f"Pass {pass_num}/{total_passes}")

        # Format speed nicely
        if speed >= 1024:
            self.speed_label.setText(f"{speed / 1024:.1f} GB/s")
        elif speed >= 1:
            self.speed_label.setText(f"{speed:.1f} MB/s")
        elif speed > 0:
            self.speed_label.setText(f"{speed * 1024:.0f} KB/s")
        else:
            self.speed_label.setText("-- MB/s")

        # ETA in human-readable format
        if speed > 0 and total_bytes > 0:
            remaining_bytes = total_bytes - bytes_written
            remaining_this_pass = remaining_bytes
            remaining_future_passes = (
                (total_passes - pass_num) * total_bytes
            )
            total_remaining = remaining_this_pass + remaining_future_passes
            eta_seconds = total_remaining / (speed * 1024 * 1024)
            if eta_seconds < 60:
                self.eta_label.setText(f"~{int(eta_seconds)} sec remaining")
            elif eta_seconds < 3600:
                mins = int(eta_seconds // 60)
                self.eta_label.setText(
                    f"~{mins} min remaining"
                )
            else:
                h = int(eta_seconds // 3600)
                m = int((eta_seconds % 3600) // 60)
                self.eta_label.setText(f"~{h}h {m}min remaining")
        else:
            self.eta_label.setText("Calculating...")

        # Batch progress
        if hasattr(self, "_completed_count") and hasattr(self, "_total_devices"):
            batch_pct = int(
                (self._completed_count * 100 + pct)
            )
            self.batch_progress.setValue(
                min(batch_pct, self._total_devices * 100)
            )

    @Slot(int, str)
    def _on_phase_changed(self, device_index: int, phase: str) -> None:
        """Update the phase badge and verify-bar visibility as the worker
        transitions between wipe and verify phases."""
        self._set_phase(phase)
        if phase == "verifying":
            # Reset verify bar to 0 and show it.
            self.verify_progress_bar.setValue(0)
            self.verify_progress_bar.setVisible(True)
            # Sample verify produces no progress ticks, so mark
            # indeterminate by showing the label + 0%. Full verify
            # will animate via _on_verify_progress.
            if getattr(self, "_verify_mode", "sample") == "sample":
                self.verify_progress_bar.setFormat("Verify (sample): running...")
            else:
                self.verify_progress_bar.setFormat("Verify (full): %p%")
        elif phase == "wiping":
            # Hide the verify bar at the start of each device.
            self.verify_progress_bar.setVisible(False)
        elif phase == "done":
            # Leave the verify bar visible so the operator can see its final state.
            pass

    @Slot(int, float, int, int, float)
    def _on_verify_progress(
        self,
        device_index: int,
        fraction: float,
        bytes_done: int,
        total_bytes: int,
        speed: float,
    ) -> None:
        """Drive the verify progress bar during full verification."""
        pct = int(max(0.0, min(1.0, fraction)) * 100)
        self.verify_progress_bar.setValue(pct)

        if speed >= 1024:
            speed_str = f"{speed / 1024:.1f} GB/s"
        elif speed >= 1:
            speed_str = f"{speed:.1f} MB/s"
        elif speed > 0:
            speed_str = f"{speed * 1024:.0f} KB/s"
        else:
            speed_str = "-- MB/s"
        self.speed_label.setText(speed_str)

        # ETA for verify pass.
        if speed > 0 and total_bytes > 0:
            remaining = total_bytes - bytes_done
            eta_seconds = remaining / (speed * 1024 * 1024)
            if eta_seconds < 60:
                self.eta_label.setText(f"~{int(eta_seconds)} sec remaining")
            elif eta_seconds < 3600:
                self.eta_label.setText(f"~{int(eta_seconds // 60)} min remaining")
            else:
                h = int(eta_seconds // 3600)
                m = int((eta_seconds % 3600) // 60)
                self.eta_label.setText(f"~{h}h {m}min remaining")

    @Slot(int, bool, str)
    def _on_device_completed(
        self, device_index: int, success: bool, cert_path: str
    ) -> None:
        self._completed_count = getattr(self, "_completed_count", 0) + 1

        wiping = getattr(self, "_wiping_devices", self.devices)
        device_name = (
            wiping[device_index].friendly_name
            if device_index < len(wiping)
            else f"Device {device_index}"
        )

        # Pull the final verify result from the worker (if available) so
        # we can show a more specific message when full-verify found errors.
        worker = self.worker
        verify_result = getattr(worker, "_last_verify_result", None) if worker else None
        verify_method = getattr(verify_result, "method", "") or ""
        verify_errors = int(getattr(verify_result, "error_count", 0) or 0)

        if success:
            # Green progress bar for successful verification
            self.device_progress.setStyleSheet(
                "QProgressBar::chunk { background-color: #38a169; }"
            )
            method_note = ""
            if verify_method == "full":
                method_note = " (full verify passed)"
            elif verify_method == "sample":
                method_note = " (sample verify passed)"
            self.progress_device_label.setText(
                f"\u2705 {device_name}: Completed successfully{method_note}. "
                f"Certificate: {os.path.basename(cert_path)}"
            )
        else:
            # Red progress bar for failed verification
            self.device_progress.setStyleSheet(
                "QProgressBar::chunk { background-color: #e53e3e; }"
            )
            if verify_method == "full" and verify_errors > 0:
                self.progress_device_label.setText(
                    f"\u274c {device_name}: FULL VERIFY FAILED \u2014 "
                    f"{verify_errors} sector(s) mismatch. "
                    "See certificate for details."
                )
            else:
                self.progress_device_label.setText(
                    f"\u274c {device_name}: VERIFICATION FAILED"
                )

    @Slot()
    def _on_all_completed(self) -> None:
        self._set_controls_enabled(True)
        self.cancel_btn.setEnabled(False)
        self._set_phase("done")
        self.worker = None

        completed = getattr(self, "_completed_count", 0)
        total = getattr(self, "_total_devices", 0)

        self.status_label.setText(
            f"Batch complete: {completed}/{total} device(s) processed."
        )
        self.progress_device_label.setText("All operations completed.")
        self.device_progress.setValue(100)
        self.batch_progress.setValue(self.batch_progress.maximum())
        self._update_wipe_count()

        QMessageBox.information(
            self,
            "Wipe Complete",
            f"Batch wipe finished.\n"
            f"{completed} of {total} device(s) processed.\n\n"
            "Certificates have been saved to:\n"
            f"{self.config.cert_output_dir}",
        )

    @Slot(int, str)
    def _on_wipe_error(self, device_index: int, message: str) -> None:
        wiping = getattr(self, "_wiping_devices", self.devices)
        device_name = (
            wiping[device_index].friendly_name
            if device_index < len(wiping)
            else f"Device {device_index}"
        )
        self.progress_device_label.setText(
            f"Error on {device_name}: {message}"
        )

    @Slot(str)
    def _on_status_message(self, message: str) -> None:
        self.status_label.setText(message)
        self.progress_device_label.setText(message)

    def _on_cancel_clicked(self) -> None:
        if self.worker is not None:
            result = QMessageBox.question(
                self,
                "Cancel Wipe?",
                "Are you sure you want to cancel the current wipe operation?\n\n"
                "Note: The device may be left in a partially wiped state.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result == QMessageBox.StandardButton.Yes:
                self.worker.cancel()
                self.cancel_btn.setEnabled(False)
                self.status_label.setText("Cancelling...")

    # ══════════════════════════════════════════════════════════════════════
    #  Demo mode
    # ══════════════════════════════════════════════════════════════════════

    def _activate_demo_mode(self) -> None:
        """Show an explanation dialog, then inject a virtual demo device."""
        reply = QMessageBox.information(
            self,
            "Demo Mode / Testmodus",
            "Demo Mode creates a small virtual disk file (10 MB) and runs "
            "a full wipe cycle.\n\n"
            "This lets you test the entire workflow without a real USB "
            "device. No actual drives are affected.\n\n"
            "A demo device will be added to the device table. Select it "
            "and click 'Start Wipe' to run the full process.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        demo_device = create_demo_device()
        self.devices.append(demo_device)
        self._add_demo_device_row(demo_device)
        self.status_label.setText(
            "Demo device added. Select it and click Start Wipe."
        )
        self._empty_state_label.hide()

    def _add_demo_device_row(self, device: DeviceInfo) -> None:
        """Append a visually distinct demo device row to the device table."""
        row = self.device_table.rowCount()
        self.device_table.insertRow(row)

        # Checkbox -- pre-checked
        cb = QCheckBox()
        cb.setChecked(True)
        cb_widget = QWidget()
        cb_layout = QHBoxLayout(cb_widget)
        cb_layout.addWidget(cb)
        cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cb_layout.setContentsMargins(0, 0, 0, 0)
        self.device_table.setCellWidget(row, 0, cb_widget)
        self._device_checkboxes.append(cb)
        cb.stateChanged.connect(self._on_selection_changed)

        # Cell values
        values = [
            "",  # col 0 is the checkbox widget
            device.drive_letter,
            device.model,
            device.serial_number,
            f"{device.capacity_gb} GB",
            device.filesystem,
            device.connection_type,
        ]
        blue_brush = QBrush(QColor("#2b6cb0"))
        bold_font = QFont()
        bold_font.setBold(True)

        for col in range(1, 7):
            item = QTableWidgetItem(values[col])
            item.setForeground(blue_brush)
            item.setFont(bold_font)
            self.device_table.setItem(row, col, item)

        # Status column -- blue "DEMO" label
        status_item = QTableWidgetItem("DEMO")
        status_item.setForeground(QBrush(QColor("#ffffff")))
        status_item.setBackground(QBrush(QColor("#3182ce")))
        sf = status_item.font()
        sf.setBold(True)
        status_item.setFont(sf)
        self.device_table.setItem(row, 7, status_item)

        # Trigger selection update so wipe button enables
        self._on_selection_changed()

    # ══════════════════════════════════════════════════════════════════════
    #  Dialogs
    # ══════════════════════════════════════════════════════════════════════

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Reload config changes
            self.operator_edit.setText(self.config.operator_name)
            method_map = {"quick": 0, "standard": 1, "bsi": 2, "custom": 3}
            self.method_combo.setCurrentIndex(
                method_map.get(self.config.default_wipe_method, 1)
            )
            self.sk_combo.setCurrentIndex(
                self.config.default_schutzklasse - 1
            )
            self.status_label.setText("Settings saved.")

    def _open_history(self) -> None:
        dialog = HistoryDialog(self.config, self)
        dialog.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About StickShredder",
            "<h2>StickShredder v1.0.0</h2>"
            "<p><b>Secure USB Wipe Tool</b></p>"
            "<p>Generates deletion certificates structured according to "
            "DIN 66399 / ISO 21964 conventions for verifiable, "
            "auditable data destruction.</p>"
            "<hr>"
            "<p style='color:#c53030; font-weight:bold;'>"
            "\u26a0 This software is NOT officially certified by DEKRA, DIN, "
            "or any other certification body. It is an open-source tool "
            "that generates reports <i>structured according to</i> "
            "DIN 66399 / ISO 21964 guidelines.</p>"
            "<p>Designed for German IT administrators (Systemadministratoren) "
            "who need standard-compliant media sanitization with full "
            "audit trail and certificate generation.</p>"
            "<table style='margin-top:8px;'>"
            "<tr><td><b>Standards:</b></td>"
            "<td>DIN 66399, ISO 21964, BSI VSITR</td></tr>"
            "<tr><td><b>Compliance:</b></td>"
            "<td>DSGVO / GDPR, BSI Grundschutz</td></tr>"
            "<tr><td><b>Certification:</b></td>"
            "<td style='color:#c53030;'>Not DEKRA/DIN certified</td></tr>"
            "<tr><td><b>License:</b></td>"
            "<td>MIT License (Open Source)</td></tr>"
            "<tr><td><b>Version:</b></td>"
            "<td>1.0.0</td></tr>"
            "</table>"
            "<p style='margin-top:12px; color:#718096; font-size:11px;'>"
            "Copyright 2026 Robin Oertel</p>",
        )

    # ══════════════════════════════════════════════════════════════════════
    #  Window events
    # ══════════════════════════════════════════════════════════════════════

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            result = QMessageBox.warning(
                self,
                "Wipe In Progress",
                "A wipe operation is currently running.\n"
                "Closing now may leave devices in a partially wiped state.\n\n"
                "Are you sure you want to quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)

        event.accept()
