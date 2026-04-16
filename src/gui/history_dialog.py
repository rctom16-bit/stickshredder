"""Wipe history viewer dialog — shows CSV log with filtering and export."""

from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path

from datetime import datetime

from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from core.config import AppConfig
from core.log import read_wipe_history

# Display column headers (order matches CSV_HEADERS in core.log)
_DISPLAY_HEADERS = [
    "Date",
    "Device Model",
    "Serial Number",
    "Capacity (bytes)",
    "Method",
    "Passes",
    "Operator",
    "Start Time",
    "End Time",
    "Duration (s)",
    "Result",
    "Verification",
    "Cert #",
]

_CSV_KEYS = [
    "date",
    "device_model",
    "serial_number",
    "capacity_bytes",
    "method",
    "passes",
    "operator",
    "start_time",
    "end_time",
    "duration_seconds",
    "result",
    "verification",
    "cert_number",
]


class HistoryDialog(QDialog):
    """Wipe history browser with search/filter and CSV export."""

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Wipe History / Löschprotokoll")
        self.setMinimumSize(900, 520)
        self._build_ui()
        self._load_data()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Filter bar
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Search / Suchen...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit)

        clear_filter_btn = QPushButton("Clear filter")
        clear_filter_btn.setToolTip("Clear search filter / Filter zuruecksetzen")
        clear_filter_btn.clicked.connect(self._clear_filter)
        filter_row.addWidget(clear_filter_btn)

        filter_row.addWidget(QLabel("Column:"))
        self.column_combo = QComboBox()
        self.column_combo.addItem("All Columns", -1)
        for i, header in enumerate(_DISPLAY_HEADERS):
            self.column_combo.addItem(header, i)
        self.column_combo.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.column_combo)

        layout.addLayout(filter_row)

        # Table
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(_DISPLAY_HEADERS)

        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)  # all columns

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_double_click)

        header = self.table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
            header.setSectionResizeMode(
                QHeaderView.ResizeMode.ResizeToContents
            )

        layout.addWidget(self.table)

        # Bottom row: counter + buttons
        btn_row = QHBoxLayout()

        self.total_label = QLabel("Total wipes / Gesamt: 0")
        self.total_label.setStyleSheet("color: #4a5568; font-weight: bold;")
        btn_row.addWidget(self.total_label)

        btn_row.addStretch()

        self.export_btn = QPushButton("\u2913 Export CSV...")
        self.export_btn.setToolTip("Export filtered view as CSV file")
        self.export_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(self.export_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load_data)
        btn_row.addWidget(refresh_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    # ── Data ──────────────────────────────────────────────────────────────

    # Column indices for special formatting
    _COL_DATE = 0
    _COL_RESULT = 10
    _COL_VERIFICATION = 11

    _GREEN_BG = QColor("#c6f6d5")
    _RED_BG = QColor("#fed7d7")

    def _load_data(self) -> None:
        self.model.removeRows(0, self.model.rowCount())
        history = read_wipe_history()

        for record in history:
            row_items: list[QStandardItem] = []
            for col_idx, key in enumerate(_CSV_KEYS):
                value = record.get(key, "")

                # Format date column to DD.MM.YYYY HH:MM
                if col_idx == self._COL_DATE and value:
                    value = self._format_date(str(value))

                item = QStandardItem(str(value))
                item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                )

                # Color-code result and verification columns
                if col_idx in (self._COL_RESULT, self._COL_VERIFICATION):
                    lower = str(value).lower().strip()
                    if lower in ("success", "passed", "pass", "ok"):
                        item.setBackground(QBrush(self._GREEN_BG))
                    elif lower in ("failed", "fail", "error"):
                        item.setBackground(QBrush(self._RED_BG))

                row_items.append(item)
            self.model.appendRow(row_items)

        # Update total counter
        total = self.model.rowCount()
        if hasattr(self, "total_label"):
            self.total_label.setText(f"Total wipes / Gesamt: {total}")

        # Set reasonable column widths
        self._set_column_widths()

    @staticmethod
    def _format_date(value: str) -> str:
        """Try to reformat a date string to DD.MM.YYYY HH:MM."""
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt)
                return dt.strftime("%d.%m.%Y %H:%M")
            except ValueError:
                continue
        return value  # return original if unparseable

    def _set_column_widths(self) -> None:
        """Set practical default column widths."""
        header = self.table.horizontalHeader()
        if header is None:
            return
        # Switch to interactive so we can set specific widths
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        widths = {
            0: 130,   # Date
            1: 160,   # Device Model
            2: 130,   # Serial Number
            3: 100,   # Capacity
            4: 80,    # Method
            5: 55,    # Passes
            6: 100,   # Operator
            7: 70,    # Start Time
            8: 70,    # End Time
            9: 80,    # Duration
            10: 70,   # Result
            11: 85,   # Verification
        }
        for col, width in widths.items():
            if col < self.model.columnCount():
                self.table.setColumnWidth(col, width)

    def _clear_filter(self) -> None:
        """Reset the search field and column selector."""
        self.filter_edit.clear()
        self.column_combo.setCurrentIndex(0)

    def _apply_filter(self) -> None:
        text = self.filter_edit.text()
        col_data = self.column_combo.currentData()
        column = col_data if col_data is not None else -1

        self.proxy.setFilterKeyColumn(column)
        self.proxy.setFilterFixedString(text)

    # ── Actions ───────────────────────────────────────────────────────────

    def _on_double_click(self, index) -> None:
        """Try to open the certificate PDF for the selected row."""
        source_index = self.proxy.mapToSource(index)
        row = source_index.row()

        cert_num_item = self.model.item(row, 12)  # cert_number column
        if cert_num_item is None:
            return
        cert_num = cert_num_item.text().strip()
        if not cert_num:
            return

        # Search for the PDF in the cert output directory
        cert_dir = Path(self.config.cert_output_dir)
        if not cert_dir.is_dir():
            QMessageBox.information(
                self,
                "Certificate Not Found",
                f"Certificate directory does not exist:\n{cert_dir}",
            )
            return

        # Look for file matching the cert number pattern
        pattern = f"SS-{int(cert_num):06d}_*"
        matches = list(cert_dir.glob(pattern))
        if not matches:
            QMessageBox.information(
                self,
                "Certificate Not Found",
                f"No certificate PDF found for #{cert_num} in:\n{cert_dir}",
            )
            return

        # Open the first match with the default system viewer
        pdf_path = str(matches[0])
        try:
            os.startfile(pdf_path)  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["xdg-open", pdf_path])
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Error",
                f"Could not open certificate:\n{exc}",
            )

    def _export_csv(self) -> None:
        """Export the currently filtered view to a new CSV file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Wipe History",
            "wipe_history_export.csv",
            "CSV files (*.csv)",
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(_DISPLAY_HEADERS)

                for row in range(self.proxy.rowCount()):
                    row_data: list[str] = []
                    for col in range(self.proxy.columnCount()):
                        idx = self.proxy.index(row, col)
                        row_data.append(idx.data() or "")
                    writer.writerow(row_data)

            QMessageBox.information(
                self,
                "Export Complete",
                f"Exported {self.proxy.rowCount()} rows to:\n{path}",
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Export Error",
                f"Could not write file:\n{exc}",
            )
