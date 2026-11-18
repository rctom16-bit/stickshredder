"""Render the Zertifikate, Schlüssel and Optionen screenshots into docs/screenshots/v2/.

    python tools/screenshots_pages.py [output-dir]

Runs on Qt's offscreen platform in a throw-away profile (config, history,
certificates, signing key and trust list in a temp folder) filled with demo
data. It never touches a disk and never wipes anything: the history entries
are written directly, the checked certificate is a signed demo token in a
temp file.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from screenshots_v2 import _isolate_profile  # noqa: E402

_HISTORY = [
    ("2026-11-12 08:41:10", "SanDisk 3.2Gen1", "0901C3F2CFA4", "Zero Fill (1 pass)", "OK"),
    ("2026-11-12 09:30:52", "Intenso Basic Line", "21100845000102", "Zero Fill (1 pass)", "OK"),
    ("2026-11-13 14:02:37", "WD Elements 2TB", "WX12A3456789", "Random (3 passes)", "OK"),
    ("2026-11-14 10:15:05", "Kingston DataTraveler", "E0D55EA5741B", "Zero Fill (1 pass)",
     "LIMITED"),
    ("2026-11-17 16:48:21", "Samsung SSD 870 EVO", "S6PNNS0T123456", "Zero Fill (1 pass)", "OK"),
    ("2026-11-18 11:03:44", "Verbatim Store'n'Go", "075E1C2B", "BSI-VSITR (7 passes)", "FAILED"),
    ("2026-11-18 13:27:09", "SanDisk Ultra Fit", "4C530001230925", "Zero Fill (1 pass)", "OK"),
]


def _demo_data(home: Path, config) -> Path:
    """History, trust list and one signed demo certificate; returns its path."""
    import core.config as cfg
    from cert.signing import SigningKey
    from cert.verify_cert import TrustStore
    from core.log import log_wipe_to_csv

    for number, (date, model, serial, method, result) in enumerate(_HISTORY, start=8):
        log_wipe_to_csv({
            "date": date, "device_model": model, "serial_number": serial,
            "capacity_bytes": 61_933_428_736, "method": method, "passes": 1,
            "operator": config.operator_name, "duration_seconds": 1062, "result": result,
            "verification": "FAILED" if result == "FAILED" else "PASSED",
            "cert_number": number, "bad_sectors": 2 if result == "LIMITED" else 0,
        })
    key = cfg.load_signing_key()  # also trusted as this PC's own key
    partner = SigningKey.from_seed(bytes(range(1, 33)))
    TrustStore.load(cfg.TRUST_FILE).add(partner.public_key_bytes(), "Muster IT · Außenstelle Fürth")
    token = key.sign_payload({
        "v": 1, "n": 14, "t": "2026-11-18T12:27:09Z", "op": config.operator_name,
        "co": config.company.name, "dm": "SanDisk Ultra Fit", "ds": "4C530001230925",
        "r": "ok",
    })
    pdf = home / "SS-000014_4C530001230925_20261118.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% demo certificate\n% stickshredder-token:" + token.encode())
    return pdf


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots" / "v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="stickshredder_pages_"))
    _isolate_profile(home)

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    import gui.pages.delete_page as delete_page
    from core.config import AppConfig
    from gui.app_window import AppWindow
    from gui.theme import ThemeManager, load_fonts

    delete_page.list_devices = lambda: []  # no disk scan at all

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    config = AppConfig()
    config.cert_output_dir = str(home / "certificates")
    config.operator_name = "Robin"
    config.company.name = "Muster IT GmbH"
    config.company.address = "Königstraße 12\n90402 Nürnberg"
    pdf = _demo_data(home, config)

    theme = ThemeManager(app, "dark", load_fonts())
    theme.apply()
    window = AppWindow(config, theme)
    window.resize(1280, 800)
    window.show()

    def settle(ms: int = 300) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def shot(name: str) -> None:
        settle()
        path = out_dir / f"{name}.png"
        window.grab().save(str(path))
        print(path)

    settle(500)
    for mode in ("dark", "light"):
        window.set_theme_setting(mode)
        window._show_page("certificates")
        page = window.certificates_page
        page.verify_certificate(str(pdf))
        page.table.selectRow(0)
        shot(f"certificates-{mode}")
        window._show_page("keys")
        shot(f"keys-{mode}")
        window._show_page("options")
        shot(f"options-{mode}")
    window.set_theme_setting("dark")
    return 0


if __name__ == "__main__":
    sys.exit(main())
