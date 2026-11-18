"""The Schlüssel page: this PC's signing key, the key card and the trusted keys.

Shows the public fingerprint only (grouped, monospace, copyable) — the private
key is never shown or exported here. „Schlüsselkarte erstellen“ writes the
key-card PDF (``cert.keycard.generate_keycard``) and opens it. Trusted keys
(``TrustStore``) can be added from a key-card PDF, renamed and removed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cert.keycard import generate_keycard
from cert.signing import KEY_FILE_NAME, fingerprint_of
from cert.verify_cert import TrustStore, extract_key_from_keycard_pdf
from core.log import audit_log
from gui.dialogs import ask_yes_no
from gui.i18n import language, language_half, tr
from gui.pages.wipe_views import open_path
from gui.widgets import make_card, make_chip, make_label, set_prop, uppercase

KEYCARD_LANGUAGES = ("de", "en", "both")


# -- Helpers --------------------------------------------------------------------------


def display_key_name(name: str) -> str:
    """A trusted key's name as shown; the own key's built-in name in the UI language."""
    import core.config as cfg

    return language_half(name) if name == cfg.OWN_KEY_NAME else name


def grouped_fingerprint(fingerprint: str) -> str:
    """``AAAA-BBBB-CCCC-DDDD`` on the first line, the other four groups on the second."""
    groups = fingerprint.split("-")
    if len(groups) <= 4:
        return fingerprint
    return "-".join(groups[:4]) + "\n" + "-".join(groups[4:])


def keycard_path(config, fingerprint: str) -> Path:
    """Where the key card goes (same name as ``stickshredder keycard``)."""
    return Path(config.cert_output_dir) / f"StickShredder-Schluesselkarte_{fingerprint[:9]}.pdf"


def format_day(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d" if language() == "en" else "%d.%m.%Y")


def _added_day(text: str) -> str:
    try:
        return format_day(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return ""


def pick_pdf(parent, title: str, file_filter: str) -> str:
    path, _ = QFileDialog.getOpenFileName(parent, title, "", file_filter)
    return path


def ask_text(parent, title: str, label: str, text: str = "") -> str | None:
    """A one-line text prompt; ``None`` when cancelled."""
    value, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, text)
    return value if ok else None


def _button(variant: str = "") -> QPushButton:
    button = QPushButton()
    if variant:
        button.setProperty("variant", variant)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


# -- Trusted key row ----------------------------------------------------------------------


class TrustedKeyRow(QFrame):
    """One trusted key: name, fingerprint, since when; rename and remove."""

    def __init__(self, entry: dict, own: bool, page: "KeysPage", parent=None) -> None:
        super().__init__(parent)
        self.setProperty("card", "row")
        self.fingerprint = entry["fingerprint"]
        self.entry = entry
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 12, 12)
        layout.setSpacing(14)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.name_label = make_label("", "row-title")
        self.own_chip = make_chip("", "accent")
        self.own_chip.setVisible(own)
        top.addWidget(self.name_label)
        top.addWidget(self.own_chip)
        top.addStretch(1)
        texts.addLayout(top)
        self.fingerprint_label = make_label(self.fingerprint, "key-mono")
        self.fingerprint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        texts.addWidget(self.fingerprint_label)
        self.added_label = make_label("", "hint")
        texts.addWidget(self.added_label)
        layout.addLayout(texts, 1)
        self.rename_button = _button("small")
        self.rename_button.clicked.connect(lambda: page.rename_key(self.fingerprint))
        self.remove_button = _button("small-danger")
        self.remove_button.clicked.connect(lambda: page.remove_key(self.fingerprint))
        layout.addWidget(self.rename_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.remove_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.retranslate()

    def retranslate(self) -> None:
        self.name_label.setText(display_key_name(self.entry["name"]))
        self.own_chip.setText(tr("keys.own_chip"))
        day = _added_day(self.entry.get("added", ""))
        self.added_label.setText(tr("keys.added_on", date=day) if day else "")
        self.added_label.setVisible(bool(day))
        self.rename_button.setText(tr("keys.rename"))
        self.remove_button.setText(tr("keys.remove"))


# -- Page -------------------------------------------------------------------------------


class KeysPage(QWidget):
    """Own fingerprint, key card, trusted keys."""

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setObjectName("PageRoot")
        self._key = None
        self._key_error = ""
        self._rows: list[TrustedKeyRow] = []
        self._status: tuple[str, dict, str] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("ScrollBody")
        scroll.setWidget(body)
        outer.addWidget(scroll)
        root = QVBoxLayout(body)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(20)

        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.title_label = make_label("", "h1")
        self.subtitle_label = make_label("", "muted", wrap=True)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        root.addLayout(titles)

        top = QHBoxLayout()
        top.setSpacing(20)
        top.addWidget(self._build_own_card(), 3)
        top.addWidget(self._build_keycard_card(), 2)
        root.addLayout(top)

        self.status_label = make_label("", "status", wrap=True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.hide()
        root.addWidget(self.status_label)

        root.addWidget(self._build_trusted_card())
        root.addStretch(1)

        self.retranslate()
        self.refresh()

    def _build_own_card(self) -> QFrame:
        card = make_card("surface")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(12)
        self.own_eyebrow = uppercase(make_label("", "section"))
        layout.addWidget(self.own_eyebrow)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.fingerprint_label = make_label("", "fingerprint", wrap=True)
        self.fingerprint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_button = _button("small")
        self.copy_button.clicked.connect(self.copy_fingerprint)
        row.addWidget(self.fingerprint_label, 1)
        row.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        self.created_label = make_label("", "hint")
        self.private_note = make_label("", "hint", wrap=True)
        layout.addWidget(self.created_label)
        layout.addWidget(self.private_note)
        layout.addSpacing(6)
        self.keycard_button = _button("accent")
        self.keycard_button.clicked.connect(self.create_keycard)
        layout.addWidget(self.keycard_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.company_hint = make_label("", "warning", wrap=True)
        layout.addWidget(self.company_hint)
        layout.addStretch(1)
        return card

    def _build_keycard_card(self) -> QFrame:
        card = make_card("note")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)
        self.keycard_title = make_label("", "card-title", wrap=True)
        self.keycard_text = make_label("", "note", wrap=True)
        layout.addWidget(self.keycard_title)
        layout.addWidget(self.keycard_text)
        layout.addStretch(1)
        return card

    def _build_trusted_card(self) -> QFrame:
        card = make_card("surface")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.trusted_title = make_label("", "card-title")
        self.trusted_text = make_label("", "muted", wrap=True)
        titles.addWidget(self.trusted_title)
        titles.addWidget(self.trusted_text)
        head.addLayout(titles, 1)
        self.add_button = _button()
        self.add_button.clicked.connect(lambda: self.add_from_keycard())
        head.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        self.trust_error_label = make_label("", "danger", wrap=True)
        self.trust_error_label.hide()
        layout.addWidget(self.trust_error_label)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(8)
        layout.addLayout(self._rows_layout)
        self.no_keys_label = make_label("", "hint")
        layout.addWidget(self.no_keys_label)
        return card

    # ── Data ──────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        self._load_own_key()
        self.company_hint.setVisible(not (self.config.company.name or "").strip())
        self._load_trusted()

    def _load_own_key(self) -> None:
        import core.config as cfg

        try:
            self._key = cfg.load_signing_key()
            self._key_error = ""
        except Exception as exc:  # noqa: BLE001 — shown on the page, never fatal
            audit_log(f"Signing key not readable on the keys page: {exc}")
            self._key = None
            self._key_error = str(exc)
        ok = self._key is not None
        self.copy_button.setEnabled(ok)
        self.keycard_button.setEnabled(ok)
        set_prop(self.fingerprint_label, "tone", "" if ok else "danger")
        self._render_own_key()

    def _render_own_key(self) -> None:
        if self._key is None:
            self.fingerprint_label.setText(tr("keys.unreadable", error=self._key_error))
            self.created_label.hide()
            return
        self.fingerprint_label.setText(grouped_fingerprint(self._key.fingerprint()))
        created = self._key_created()
        self.created_label.setText(tr("keys.created", date=format_day(created))
                                   if created else "")
        self.created_label.setVisible(created is not None)

    @staticmethod
    def _key_created() -> datetime | None:
        import core.config as cfg

        try:
            stat = (Path(cfg.KEY_DIR) / KEY_FILE_NAME).stat()
        except OSError:
            return None
        stamp = getattr(stat, "st_birthtime", None) or stat.st_mtime
        return datetime.fromtimestamp(stamp)

    def _store(self) -> TrustStore | None:
        import core.config as cfg

        try:
            return TrustStore.load(cfg.TRUST_FILE)
        except ValueError as exc:
            audit_log(f"Trusted key list not readable: {exc}")
            return None

    def _load_trusted(self) -> None:
        import core.config as cfg

        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []
        store = self._store()
        self.add_button.setEnabled(store is not None)
        self.trust_error_label.setText(
            "" if store is not None else tr("keys.trust_damaged", path=str(cfg.TRUST_FILE)))
        self.trust_error_label.setVisible(store is None)
        own = self._key.fingerprint() if self._key is not None else ""
        for entry in (store.entries() if store is not None else []):
            row = TrustedKeyRow(entry, entry["fingerprint"] == own, self)
            self._rows_layout.addWidget(row)
            self._rows.append(row)
        self.no_keys_label.setVisible(store is not None and not self._rows)

    def trusted_rows(self) -> list[TrustedKeyRow]:
        return list(self._rows)

    def fingerprint(self) -> str:
        return self._key.fingerprint() if self._key is not None else ""

    # ── Actions ───────────────────────────────────────────────────────────────

    def _show_status(self, key: str, tone: str = "ok", **values) -> None:
        self._status = (key, values, tone)
        self.status_label.setText(tr(key, **values))
        set_prop(self.status_label, "tone", tone)
        self.status_label.show()

    def copy_fingerprint(self) -> None:
        if self._key is None:
            return
        QApplication.clipboard().setText(self._key.fingerprint())
        self._show_status("keys.copied")

    def create_keycard(self) -> Path | None:
        """Write the key card PDF into the certificate folder and open it."""
        if self._key is None:
            return None
        output = keycard_path(self.config, self._key.fingerprint())
        lang = self.config.cert_language if self.config.cert_language in KEYCARD_LANGUAGES \
            else "de"
        try:
            path = Path(generate_keycard(self._key, self.config.company.name, output,
                                         language=lang))
        except Exception as exc:  # noqa: BLE001 — disk full, no rights, PDF error …
            audit_log(f"Key card could not be created: {exc}")
            self._show_status("keys.keycard_failed", "danger", error=str(exc))
            return None
        self._show_status("keys.keycard_saved", "ok", path=str(path))
        open_path(str(path))
        return path

    def add_from_keycard(self, path: str | None = None, name: str | None = None) -> str | None:
        """Trust the key of a key-card PDF. Returns its fingerprint, ``None`` if nothing added."""
        if path is None:
            path = pick_pdf(self, tr("keys.pick_title"), tr("certs.pdf_filter"))
            if not path:
                return None
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            self._show_status("keys.file_unreadable", "danger", error=str(exc))
            return None
        public_key = extract_key_from_keycard_pdf(data)
        if public_key is None:
            self._show_status("keys.not_a_keycard", "danger")
            return None
        fingerprint = fingerprint_of(public_key)
        store = self._store()
        if store is None:
            return None
        known = store.as_dict().get(fingerprint)
        if known is not None:
            self._show_status("keys.already", "ok", name=display_key_name(known))
            return fingerprint
        if name is None:
            name = ask_text(self, tr("keys.name_title"), tr("keys.name_label"))
            if name is None:
                return None
        if not name.strip():
            self._show_status("keys.name_empty", "danger")
            return None
        store.add(public_key, name.strip())
        audit_log(f"Trusted key added in the GUI: fingerprint={fingerprint}")
        self._load_trusted()
        self._show_status("keys.added", "ok", name=name.strip(), fingerprint=fingerprint)
        return fingerprint

    def _name_of(self, fingerprint: str) -> str:
        for row in self._rows:
            if row.fingerprint == fingerprint:
                return display_key_name(row.entry["name"])
        return fingerprint

    def rename_key(self, fingerprint: str, name: str | None = None) -> bool:
        store = self._store()
        if store is None:
            return False
        if name is None:
            name = ask_text(self, tr("keys.name_title"), tr("keys.name_label"),
                            self._name_of(fingerprint))
            if name is None:
                return False
        if not name.strip():
            self._show_status("keys.name_empty", "danger")
            return False
        if not store.rename(fingerprint, name.strip()):
            return False
        self._load_trusted()
        self._show_status("keys.renamed", "ok", name=name.strip())
        return True

    def remove_key(self, fingerprint: str) -> bool:
        store = self._store()
        if store is None:
            return False
        name = self._name_of(fingerprint)
        text = tr("keys.remove_text", name=name)
        if self._key is not None and fingerprint == self._key.fingerprint():
            text = f"{tr('keys.remove_own')} {text}"
        if not ask_yes_no(self, tr("keys.remove_title"), text):
            return False
        if not store.remove(fingerprint):
            return False
        audit_log(f"Trusted key removed in the GUI: fingerprint={fingerprint}")
        self._load_trusted()
        self._show_status("keys.removed", "ok", name=name)
        return True

    # ── Language and theme ────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self.title_label.setText(tr("keys.title"))
        self.subtitle_label.setText(tr("keys.subtitle"))
        self.own_eyebrow.setText(tr("keys.own_eyebrow"))
        self.copy_button.setText(tr("keys.copy"))
        self.private_note.setText(tr("keys.private_note"))
        self.keycard_button.setText(tr("keys.keycard_button"))
        self.company_hint.setText(tr("keys.company_missing"))
        self.keycard_title.setText(tr("keys.keycard_title"))
        self.keycard_text.setText(tr("keys.keycard_text"))
        self.trusted_title.setText(tr("keys.trusted_title"))
        self.trusted_text.setText(tr("keys.trusted_text"))
        self.add_button.setText(tr("keys.add"))
        self.no_keys_label.setText(tr("keys.none"))
        for row in self._rows:
            row.retranslate()
        if self._key is not None or self._key_error:
            self._render_own_key()
        if self.trust_error_label.isVisibleTo(self):
            import core.config as cfg

            self.trust_error_label.setText(tr("keys.trust_damaged", path=str(cfg.TRUST_FILE)))
        if self._status is not None:
            key, values, tone = self._status
            self.status_label.setText(tr(key, **values))

    def on_theme_changed(self) -> None:
        for widget in self.findChildren(QWidget):
            widget.update()
