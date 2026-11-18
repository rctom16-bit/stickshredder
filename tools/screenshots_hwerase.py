"""Render the hardware-erase (Beta) screenshots into docs/screenshots/v2/.

    python tools/screenshots_hwerase.py [output-dir]

hwerase-confirm-dark.png  the extra confirmation over the Löschen page
hwerase-running-dark.png  the drive erasing itself (sample values)
hwerase-result-dark.png   the result of a demo run on the demo SSD

Runs on Qt's offscreen platform in a throw-away profile. It never touches a
real disk: the disk list is a fixed sample, no WMI query runs, and the only
run is the demo SSD's pretend erase plus the demo overwrite of a 10 MB temp
file. The "running" screen is fed with sample values; no worker runs for it.
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

from screenshots_v2 import _isolate_profile, _sample_disks  # noqa: E402


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots" / "v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    _isolate_profile(Path(tempfile.mkdtemp(prefix="stickshredder_shots_")))

    from dataclasses import replace

    from PySide6.QtCore import QEventLoop, QPoint, QTimer
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication

    import gui.pages.delete_page as delete_page
    import gui.wipe_worker as wipe_worker
    from core.config import AppConfig
    from gui.app_window import AppWindow
    from gui.pages.delete_page import DEMO_SSD_ID, _Run
    from gui.pages.wipe_views import HardwareEraseDialog
    from gui.theme import ThemeManager, load_fonts

    disks, media = _sample_disks()
    delete_page.list_devices = lambda: list(disks)
    delete_page.media_type_of = lambda device: media.get(device.device_id, "unknown")
    real_worker = delete_page.WipeWorker

    def demo_only_worker(**kwargs):
        if not all(d.device_id.startswith("\\\\.\\DemoDevice") for d in kwargs["devices"]):
            raise RuntimeError("screenshots only ever wipe the demo devices")
        return real_worker(**kwargs)

    delete_page.WipeWorker = demo_only_worker
    wipe_worker.WipeWorker.DEMO_ERASE_SECONDS = 1.0

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    config = AppConfig()
    config.cert_output_dir = str(Path(tempfile.mkdtemp(prefix="stickshredder_certs_")))
    config.operator_name = "Robin"
    theme = ThemeManager(app, "dark", load_fonts())
    theme.apply()
    window = AppWindow(config, theme)
    window.resize(1280, 800)
    window.show()
    page = window.delete_page

    def settle(ms: int = 300) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def save(pixmap, name: str) -> None:
        path = out_dir / f"{name}.png"
        pixmap.save(str(path))
        print(path)

    settle(500)
    page.add_demo_device()
    ssd = next(d for d in page._devices if d.device_id == DEMO_SSD_ID)
    page.select_device(ssd)

    # Confirm: the extra dialog centred over the dimmed page.
    dialog = HardwareEraseDialog(ssd, window)
    dialog.untested_check.setChecked(True)
    dialog.show()
    dialog.adjustSize()
    settle()
    shot = window.grab()
    card = dialog.grab()
    painter = QPainter(shot)
    painter.fillRect(shot.rect(), QColor(0, 0, 0, 140))
    painter.drawPixmap(QPoint((shot.width() - card.width()) // 2,
                              (shot.height() - card.height()) // 2), card)
    painter.end()
    save(shot, "hwerase-confirm-dark")
    dialog.close()

    # Running: the drive erases itself (sample values, no worker).
    options = replace(page.options, hardware_erase=True)
    page._run = _Run(device=ssd, options=options, passes=1)
    page.running_view.start(ssd, options)
    page.running_view.set_step("format", "skipped")
    page.stack.setCurrentWidget(page.running_view)
    window._on_busy_changed(True)
    page._on_status_message("Checking hardware erase support...")
    page._on_hardware_erase_started(0, "ata-enhanced", 2)
    page.running_view._elapsed_timer.stop()
    page.running_view.update_elapsed(42)
    page.running_view.progress_bar.setAnimated(False)
    settle()
    save(window.grab(), "hwerase-running-dark")
    page.running_view.end_hardware_erase(False)
    page._run = None
    window._on_busy_changed(False)

    # Result: a real demo run on the demo SSD (pretend erase + 10 MB overwrite).
    page.stack.setCurrentWidget(page.select_view)
    page._hardware_device_id = ssd.device_id
    page.set_options(options)
    page.start_wipe(ssd)
    loop = QEventLoop()
    QTimer.singleShot(60_000, loop.quit)
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if page.worker is None else None)
    timer.start(100)
    loop.exec()
    timer.stop()
    if page.current_view() != "result":
        print("demo hardware erase did not reach the result page", file=sys.stderr)
        return 1
    settle()
    save(window.grab(), "hwerase-result-dark")
    return 0


if __name__ == "__main__":
    sys.exit(main())
