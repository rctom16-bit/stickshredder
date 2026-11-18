"""Building blocks of the „Signal“ design: cards, tiles, chips, rail buttons,
the striped progress bar, the phase stepper, the QR code and the icons.

Colours come from :func:`gui.theme.current`; static widgets are styled by the
application stylesheet through their ``card`` / ``role`` / ``tone`` properties.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui import theme


# -- Styling helpers ---------------------------------------------------------------


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget: QWidget, name: str, value) -> None:
    """Set a dynamic style property and re-apply the stylesheet if it changed."""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


def make_label(text: str = "", role: str = "", parent=None, *, wrap: bool = False,
               **props) -> QLabel:
    label = QLabel(text, parent)
    if role:
        label.setProperty("role", role)
    for key, value in props.items():
        label.setProperty(key, value)
    label.setWordWrap(wrap)
    return label


def make_card(kind: str, parent=None, **props) -> QFrame:
    frame = QFrame(parent)
    frame.setProperty("card", kind)
    for key, value in props.items():
        frame.setProperty(key, value)
    return frame


def make_chip(text: str, tone: str = "neutral", parent=None) -> QLabel:
    chip = make_label(text, "chip", parent, tone=tone)
    chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return chip


# -- Icons -------------------------------------------------------------------------

_ICONS = {
    "logo": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 34 34" fill="none">'
        '<rect x="4" y="4" width="26" height="13" rx="4" fill="{body}"/>'
        '<path d="M9 21v9M14 21v6M19 21v9M24 21v5" stroke="{c}" stroke-width="2.4" '
        'stroke-linecap="round"/></svg>'
    ),
    "delete": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none">'
        '<rect x="3" y="4" width="14" height="7" rx="2" stroke="{c}" stroke-width="1.6"/>'
        '<path d="M6 14v3M10 14v3M14 14v3" stroke="{c}" stroke-width="1.6" stroke-linecap="round"/></svg>'
    ),
    "certificates": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none">'
        '<path d="M4 3h9l3 3v11H4z" stroke="{c}" stroke-width="1.6" stroke-linejoin="round"/>'
        '<path d="M7 9h6M7 12h6" stroke="{c}" stroke-width="1.6" stroke-linecap="round"/></svg>'
    ),
    "keys": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none">'
        '<circle cx="7" cy="10" r="3.5" stroke="{c}" stroke-width="1.6"/>'
        '<path d="M10.5 10H17M14 10v3" stroke="{c}" stroke-width="1.6" stroke-linecap="round"/></svg>'
    ),
    "options": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none">'
        '<circle cx="10" cy="10" r="2.5" stroke="{c}" stroke-width="1.6"/>'
        '<path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4'
        'M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4" stroke="{c}" stroke-width="1.6" stroke-linecap="round"/></svg>'
    ),
    "moon": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18" fill="none">'
        '<path d="M15 11.5A6.5 6.5 0 016.5 3a6.5 6.5 0 108.5 8.5z" stroke="{c}" stroke-width="1.5" '
        'stroke-linejoin="round"/></svg>'
    ),
    "sun": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18" fill="none">'
        '<circle cx="9" cy="9" r="3.5" stroke="{c}" stroke-width="1.5"/>'
        '<path d="M9 1.5v2M9 14.5v2M1.5 9h2M14.5 9h2M3.7 3.7l1.4 1.4M12.9 12.9l1.4 1.4'
        'M3.7 14.3l1.4-1.4M12.9 5.1l1.4-1.4" stroke="{c}" stroke-width="1.5" stroke-linecap="round"/></svg>'
    ),
    "shred": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22" fill="none">'
        '<rect x="3" y="3" width="16" height="7" rx="2" stroke="{c}" stroke-width="2"/>'
        '<path d="M6 13v6M11 13v4M16 13v6" stroke="{c}" stroke-width="2" stroke-linecap="round"/></svg>'
    ),
    "refresh": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none">'
        '<path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3.2h-3.2" stroke="{c}" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    ),
}


def _device_pixel_ratio() -> float:
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    return max(2.0, ratio)


def icon_pixmap(name: str, size: int, colour: str, body: str = "") -> QPixmap:
    """Render one of the artboard icons in ``colour`` (crisp on high-DPI screens)."""
    svg = _ICONS[name].format(c=colour, body=body or colour)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    ratio = _device_pixel_ratio()
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def two_state_icon(name: str, size: int, off_colour: str, on_colour: str) -> QIcon:
    icon = QIcon()
    icon.addPixmap(icon_pixmap(name, size, off_colour), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(icon_pixmap(name, size, on_colour), QIcon.Mode.Normal, QIcon.State.On)
    return icon


class Logo(QWidget):
    """The StickShredder mark at the top of the rail."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(34, 34)

    def paintEvent(self, _event) -> None:  # noqa: N802 — Qt name
        p = theme.current()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, icon_pixmap("logo", 34, p.accent, body=p.logo_body))
        painter.end()


class RailButton(QToolButton):
    """Navigation item of the 88 px rail: icon above an 11 px label."""

    def __init__(self, icon_name: str, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.setProperty("rail", "true")
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(20, 20))
        self.setFixedSize(64, 56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        p = theme.current()
        self.setIcon(two_state_icon(self.icon_name, 20, p.muted, p.accent_text))


# -- Tiles ---------------------------------------------------------------------------


class Tile(QFrame):
    """Small inset box: label, value, optional note (Methode / Prüfung / Tempo …)."""

    def __init__(self, label: str = "", *, mono: bool = False, compact: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.setProperty("card", "tile")
        layout = QVBoxLayout(self)
        if compact:
            layout.setContentsMargins(14, 12, 14, 12)
        else:
            layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(4)
        self.label = make_label(label, "tile-label")
        self.value = make_label("", "tile-value-mono" if mono else "tile-value")
        self.note = make_label("", "tile-note")
        self.note.setWordWrap(True)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        layout.addStretch(1)
        self.note.hide()

    def set_values(self, value: str, note: str = "", tone: str = "") -> None:
        self.value.setText(value)
        self.note.setText(note)
        self.note.setVisible(bool(note))
        set_prop(self.note, "tone", tone)

    def set_label(self, text: str) -> None:
        self.label.setText(text)

    def value_text(self) -> str:
        return self.value.text()

    def note_text(self) -> str:
        return self.note.text()


# -- Progress -------------------------------------------------------------------------


class StripedProgressBar(QWidget):
    """14 px bar with moving diagonal stripes (115°, 14 px + 8 px)."""

    STRIPE_A = 14
    STRIPE_B = 8

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._tone = "accent"
        self._offset = 0.0
        self.setFixedHeight(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("progressbar")
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def setValue(self, fraction: float) -> None:  # noqa: N802 — Qt style
        self._value = max(0.0, min(1.0, float(fraction)))
        self.setAccessibleDescription(f"{int(self._value * 100)} %")
        self.update()

    def value(self) -> float:
        return self._value

    def setTone(self, tone: str) -> None:  # noqa: N802
        self._tone = tone
        self.update()

    def tone(self) -> str:
        return self._tone

    def setAnimated(self, on: bool) -> None:  # noqa: N802
        if on:
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        self._offset = (self._offset + 1.0) % (self.STRIPE_A + self.STRIPE_B)
        self.update()

    def _colours(self) -> tuple[str, str]:
        p = theme.current()
        if self._tone == "warning":
            return ("#F0B54A", "#DDA035") if p.name == "light" else ("#FFD38A", "#E8B866")
        if self._tone == "danger":
            return ("#D9483B", "#C23A2E") if p.name == "light" else ("#FFB4AA", "#E8968C")
        return p.accent, p.stripe

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(p.track))
        painter.drawRoundedRect(rect, radius, radius)
        width = rect.width() * self._value
        if width > 0.5:
            fill = QRectF(0, 0, max(width, rect.height()), rect.height())
            path = QPainterPath()
            path.addRoundedRect(fill, radius, radius)
            painter.setClipPath(path)
            first, second = self._colours()
            period = float(self.STRIPE_A + self.STRIPE_B)
            # CSS repeating-linear-gradient(115deg …): direction (sin, -cos).
            dx, dy = 0.9063, 0.4226
            start = QPointF(self._offset * dx, self._offset * dy)
            gradient = QLinearGradient(start, QPointF(start.x() + period * dx,
                                                      start.y() + period * dy))
            split = self.STRIPE_A / period
            gradient.setColorAt(0.0, QColor(first))
            gradient.setColorAt(split, QColor(first))
            gradient.setColorAt(min(split + 0.001, 1.0), QColor(second))
            gradient.setColorAt(1.0, QColor(second))
            gradient.setSpread(QLinearGradient.Spread.RepeatSpread)
            painter.fillRect(fill, QBrush(gradient))
        painter.end()


class PhaseStepper(QWidget):
    """Four steps (Überschreiben → Prüfen → Formatieren → Zertifikat), each a 6 px bar."""

    STATES = ("todo", "active", "done", "skipped")

    def __init__(self, labels: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._labels = list(labels or ["", "", "", ""])
        self._states = ["todo"] * len(self._labels)
        self._fractions = [0.0] * len(self._labels)
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(400, 36)

    def set_labels(self, labels: list[str]) -> None:
        self._labels = list(labels)
        self.update()

    def set_step(self, index: int, state: str, fraction: float | None = None) -> None:
        self._states[index] = state
        if fraction is not None:
            self._fractions[index] = max(0.0, min(1.0, float(fraction)))
        elif state == "done":
            self._fractions[index] = 1.0
        elif state in ("todo", "skipped"):
            self._fractions[index] = 0.0
        self.update()

    def reset(self) -> None:
        self._states = ["todo"] * len(self._labels)
        self._fractions = [0.0] * len(self._labels)
        self.update()

    def state(self, index: int) -> str:
        return self._states[index]

    def fraction(self, index: int) -> float:
        return self._fractions[index]

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        count = len(self._labels)
        gap = 8.0
        width = (self.width() - gap * (count - 1)) / count
        for i, label in enumerate(self._labels):
            x = i * (width + gap)
            bar = QRectF(x, 0, width, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p.track))
            painter.drawRoundedRect(bar, 3, 3)
            fraction = self._fractions[i]
            if fraction > 0:
                painter.setBrush(QColor(p.accent))
                painter.drawRoundedRect(QRectF(x, 0, max(6.0, width * fraction), 6), 3, 3)
            state = self._states[i]
            font = QFont(theme.fonts().body)
            font.setPixelSize(13)
            if state in ("active", "done"):
                font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            colour = {"done": p.accent_text, "active": p.text}.get(state, p.muted)
            painter.setPen(QColor(colour))
            painter.drawText(QRectF(x, 14, width, 22),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             f"{i + 1} {label}")
        painter.end()


# -- QR code -------------------------------------------------------------------------


def qr_matrix(text: str) -> list[list[bool]]:
    """Modules of the QR code for ``text`` — the same encoder the certificate PDF uses."""
    from reportlab.graphics.barcode.qr import QrCodeWidget

    code = QrCodeWidget(text, barLevel="L").qr
    code.make()
    return [[bool(cell) for cell in row] for row in code.modules]


class QrCode(QWidget):
    """The certificate's QR code, or a dashed placeholder while there is none yet."""

    QUIET = 4  # modules of white border, as on the PDF

    def __init__(self, placeholder_size: int = 96, module_px: int = 2,
                 placeholder_text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._matrix: list[list[bool]] = []
        self._placeholder_size = placeholder_size
        self._module_px = module_px
        self._placeholder_text = placeholder_text
        self.setFixedSize(placeholder_size, placeholder_size)

    def set_placeholder_text(self, text: str) -> None:
        self._placeholder_text = text
        self.update()

    def set_text(self, text: str | None) -> None:
        self._matrix = qr_matrix(text) if text else []
        if self._matrix:
            side = (len(self._matrix) + 2 * self.QUIET) * self._module_px
            self.setFixedSize(side, side)
            self.setAccessibleName("QR")
        else:
            self.setFixedSize(self._placeholder_size, self._placeholder_size)
        self.update()

    def has_code(self) -> bool:
        return bool(self._matrix)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if not self._matrix:
            pen = QPen(QColor(p.cert_placeholder), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)
            font = QFont(theme.fonts().body)
            font.setPixelSize(12)
            painter.setFont(font)
            painter.setPen(QColor(p.cert_muted))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._placeholder_text)
            painter.end()
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(QColor("#0B1020"))
        m = self._module_px
        offset = self.QUIET * m
        for r, row in enumerate(self._matrix):
            for c, dark in enumerate(row):
                if dark:
                    painter.drawRect(offset + c * m, offset + r * m, m, m)
        painter.end()


# -- Result mark -----------------------------------------------------------------------


class ResultIcon(QWidget):
    """64 px circle: check (ok), exclamation mark (limited) or cross (failed)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tone = "ok"
        self.setFixedSize(64, 64)

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = theme.current()
        fill, mark = {
            "ok": (p.accent, p.on_accent),
            "limited": (p.limited_eyebrow, p.limited_bg),
            "failed": (p.failed_eyebrow, p.failed_bg),
        }.get(self._tone, (p.accent, p.on_accent))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawEllipse(QRectF(2, 2, 60, 60))
        pen = QPen(QColor(mark), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                   Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if self._tone == "limited":
            painter.drawLine(QPointF(32, 18), QPointF(32, 36))
            painter.drawPoint(QPointF(32, 46))
        elif self._tone == "failed":
            painter.drawLine(QPointF(22, 22), QPointF(42, 42))
            painter.drawLine(QPointF(42, 22), QPointF(22, 42))
        else:
            path = QPainterPath(QPointF(19, 33))
            path.lineTo(28, 42)
            path.lineTo(45, 23)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.end()


def uppercase(label: QLabel, spacing: float = 0.6) -> QLabel:
    """Small caps-style eyebrow text (CSS ``text-transform: uppercase``)."""
    font = label.font()
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    label.setFont(font)
    return label
