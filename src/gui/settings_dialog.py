"""Settings dialog for StickShredder — company info, defaults, language."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig


class SettingsDialog(QDialog):
    """Application settings organized into tabs."""

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings / Einstellungen")
        self.setMinimumSize(560, 520)
        self._build_ui()
        self._load_from_config()

    # ── UI construction ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Company Info
        self.tabs.addTab(self._build_company_tab(), "Company / Firma")

        # Tab 2: Defaults
        self.tabs.addTab(self._build_defaults_tab(), "Defaults / Standards")

        # Tab 3: Language
        self.tabs.addTab(self._build_language_tab(), "Language / Sprache")

        # Save / Cancel
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._save)
        btn_box.rejected.connect(self.reject)
        save_btn = btn_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setStyleSheet(
                "QPushButton { background-color: #38a169; color: white;"
                " font-weight: bold; padding: 6px 20px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #2f855a; }"
            )
        cancel_btn = btn_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setStyleSheet(
                "QPushButton { background-color: #a0aec0; color: white;"
                " padding: 6px 20px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #718096; }"
            )
        layout.addWidget(btn_box)

    def _build_company_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        desc = QLabel(
            "Company details shown on wipe certificates.\n"
            "Firmendaten, die auf Loeschzertifikaten erscheinen."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4a5568; font-size: 11px; margin-bottom: 4px;")
        form.addRow(desc)

        self.company_name_edit = QLineEdit()
        self.company_name_edit.setPlaceholderText("ACME IT Solutions GmbH")
        form.addRow("Company Name / Firmenname:", self.company_name_edit)

        self.company_address_edit = QPlainTextEdit()
        self.company_address_edit.setPlaceholderText(
            "Musterstrasse 1\n12345 Berlin\nDeutschland"
        )
        self.company_address_edit.setMaximumHeight(100)
        form.addRow("Address / Adresse:", self.company_address_edit)

        logo_group = QHBoxLayout()
        self.logo_path_edit = QLineEdit()
        self.logo_path_edit.setPlaceholderText("Path to company logo (PNG/JPG)")
        self.logo_path_edit.setReadOnly(True)
        logo_group.addWidget(self.logo_path_edit)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_logo)
        logo_group.addWidget(browse_btn)

        clear_logo_btn = QPushButton("Clear")
        clear_logo_btn.clicked.connect(lambda: self.logo_path_edit.clear())
        logo_group.addWidget(clear_logo_btn)

        form.addRow("Logo:", logo_group)

        self.logo_preview_label = QLabel("No logo selected")
        self.logo_preview_label.setStyleSheet(
            "color: #718096; font-size: 11px; font-style: italic;"
        )
        form.addRow("", self.logo_preview_label)

        # Keep preview in sync with the path field
        self.logo_path_edit.textChanged.connect(self._update_logo_preview)

        return widget

    def _build_defaults_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        desc = QLabel(
            "Pre-filled defaults for new wipe operations.\n"
            "Voreinstellungen fuer neue Loeschvorgaenge."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4a5568; font-size: 11px; margin-bottom: 4px;")
        form.addRow(desc)

        self.operator_edit = QLineEdit()
        self.operator_edit.setPlaceholderText("Max Mustermann")
        self.operator_edit.setToolTip("Minimum 2 characters / Mindestens 2 Zeichen")
        form.addRow("Operator / Bediener:", self.operator_edit)

        self.method_combo = QComboBox()
        self.method_combo.addItem("Quick / Zero-Fill (1 pass)", "quick")
        self.method_combo.addItem("Standard / 3-Pass Random", "standard")
        self.method_combo.addItem("BSI VSITR / 7-Pass", "bsi")
        form.addRow("Default Wipe Method:", self.method_combo)

        self.schutzklasse_combo = QComboBox()
        self.schutzklasse_combo.addItem("1 — Normaler Schutzbedarf (normal protection)", 1)
        self.schutzklasse_combo.addItem("2 — Hoher Schutzbedarf (high protection)", 2)
        self.schutzklasse_combo.addItem("3 — Sehr hoher Schutzbedarf (very high protection)", 3)
        form.addRow("Default Schutzklasse:", self.schutzklasse_combo)

        cert_dir_layout = QHBoxLayout()
        self.cert_dir_edit = QLineEdit()
        self.cert_dir_edit.setReadOnly(True)
        cert_dir_layout.addWidget(self.cert_dir_edit)
        cert_dir_btn = QPushButton("Browse...")
        cert_dir_btn.clicked.connect(self._browse_cert_dir)
        cert_dir_layout.addWidget(cert_dir_btn)
        form.addRow("Certificate Output:", cert_dir_layout)

        return widget

    def _build_language_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        desc = QLabel(
            "Language settings for generated documents.\n"
            "Spracheinstellungen fuer erzeugte Dokumente."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #4a5568; font-size: 11px; margin-bottom: 4px;")
        form.addRow(desc)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Deutsch", "de")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("Both / Beides (bilingual)", "both")
        form.addRow("Certificate Language:", self.lang_combo)

        info = QLabel(
            "Controls the language used in generated PDF certificates.\n"
            "Die Sprache der erzeugten PDF-Zertifikate."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #4a5568; font-size: 11px;")
        form.addRow("", info)

        return widget

    # ── Helpers ───────────────────────────────────────────────────────────

    def _update_logo_preview(self, text: str) -> None:
        if text.strip():
            filename = Path(text.strip()).name
            self.logo_preview_label.setText(f"Selected: {filename}")
            self.logo_preview_label.setStyleSheet(
                "color: #2d3748; font-size: 11px; font-style: italic;"
            )
        else:
            self.logo_preview_label.setText("No logo selected")
            self.logo_preview_label.setStyleSheet(
                "color: #718096; font-size: 11px; font-style: italic;"
            )

    def _browse_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Company Logo",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.svg)",
        )
        if path:
            self.logo_path_edit.setText(path)

    def _browse_cert_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Certificate Output Directory",
        )
        if path:
            self.cert_dir_edit.setText(path)

    # ── Load / Save ──────────────────────────────────────────────────────

    def _load_from_config(self) -> None:
        self.company_name_edit.setText(self.config.company.name)
        self.company_address_edit.setPlainText(self.config.company.address)
        self.logo_path_edit.setText(self.config.company.logo_path)

        self.operator_edit.setText(self.config.operator_name)

        method_map = {"quick": 0, "standard": 1, "bsi": 2}
        self.method_combo.setCurrentIndex(
            method_map.get(self.config.default_wipe_method, 1)
        )

        sk_index = max(0, self.config.default_schutzklasse - 1)
        self.schutzklasse_combo.setCurrentIndex(sk_index)

        self.cert_dir_edit.setText(self.config.cert_output_dir)

        lang_map = {"de": 0, "en": 1, "both": 2}
        self.lang_combo.setCurrentIndex(
            lang_map.get(self.config.cert_language, 0)
        )

    def _save(self) -> None:
        # ── Input validation ────────────────────────────────────────────
        address = self.company_address_edit.toPlainText().strip()
        name = self.company_name_edit.text().strip()
        operator = self.operator_edit.text().strip()

        if address and not name:
            QMessageBox.warning(
                self,
                "Validation / Validierung",
                "Company name is required when an address is provided.\n"
                "Firmenname ist erforderlich, wenn eine Adresse angegeben ist.",
            )
            self.tabs.setCurrentIndex(0)
            self.company_name_edit.setFocus()
            return

        if operator and len(operator) < 2:
            QMessageBox.warning(
                self,
                "Validation / Validierung",
                "Operator name should be at least 2 characters.\n"
                "Der Bedienername sollte mindestens 2 Zeichen lang sein.",
            )
            self.tabs.setCurrentIndex(1)
            self.operator_edit.setFocus()
            return

        self.config.company.name = name
        self.config.company.address = address
        self.config.company.logo_path = self.logo_path_edit.text().strip()

        self.config.operator_name = operator
        self.config.default_wipe_method = (
            self.method_combo.currentData() or "standard"
        )
        self.config.default_schutzklasse = self.schutzklasse_combo.currentData() or 2
        self.config.cert_output_dir = (
            self.cert_dir_edit.text().strip()
            or str(Path.home() / ".stickshredder" / "certificates")
        )
        self.config.cert_language = self.lang_combo.currentData() or "de"

        self.config.save()
        self.accept()
