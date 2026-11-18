"""Render the 2.0 window screenshots into docs/screenshots/v2/.

    python tools/screenshots_v2.py [output-dir]

Runs on Qt's offscreen platform in a throw-away profile (config, history,
certificates and signing key in a temp folder). It never touches a real disk:
the disk list is a fixed sample, no WMI query runs, and the only wipe is the
demo wipe of a 10 MB temp file. The "running" screen is fed with sample
progress values; no worker runs for it.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


DEMO_OPERATOR = "Max Mustermann"


def _isolate_profile(home: Path) -> None:
    import core.config as config
    import core.log as log

    home.mkdir(parents=True, exist_ok=True)
    config.CONFIG_DIR = home
    config.CONFIG_FILE = home / "config.json"
    config.AUDIT_LOG_FILE = home / "audit.log"
    config.CERT_COUNTER_FILE = home / "cert_counter.txt"
    config.WIPE_HISTORY_FILE = home / "wipe_history.csv"
    config.DEFAULT_CERT_OUTPUT = home / "certificates"
    config.KEY_DIR = home / "keys"
    config.TRUST_FILE = home / "keys" / "trusted_keys.json"
    log.CONFIG_DIR = home
    log.AUDIT_LOG_FILE = home / "audit.log"
    log.WIPE_HISTORY_FILE = home / "wipe_history.csv"


def _sample_disks():
    """Made-up disks: no real model names or serial numbers in published screenshots."""
    from wipe.device import DeviceInfo

    gib = 1024 ** 3
    stick = DeviceInfo(
        drive_letter="F:", device_id="\\\\.\\PhysicalDrive1", model="Generic USB Flash Disk",
        serial_number="DEMO00000001", capacity_bytes=int(57.7 * gib), filesystem="exFAT",
        connection_type="USB", is_removable=True, is_system_drive=False,
        friendly_name="Generic USB Flash Disk (F:)", disk_letters=["F:"],
    )
    system = DeviceInfo(
        drive_letter="C:", device_id="\\\\.\\PhysicalDrive0", model="Example NVMe SSD 1TB",
        serial_number="DEMO00000000", capacity_bytes=int(953.9 * gib), filesystem="NTFS",
        connection_type="NVMe", is_removable=False, is_system_drive=True, is_internal=True,
        friendly_name="Example NVMe SSD 1TB (C:)", disk_letters=["C:"],
    )
    ssd = DeviceInfo(
        drive_letter="", device_id="\\\\.\\PhysicalDrive2", model="Example SATA SSD 500GB",
        serial_number="DEMO00000002", capacity_bytes=int(465.8 * gib), filesystem="RAW",
        connection_type="USB", is_removable=False, is_system_drive=False,
        friendly_name="Example SATA SSD 500GB", disk_letters=[],
    )
    media = {stick.device_id: "flash", system.device_id: "ssd", ssd.device_id: "ssd"}
    return [system, stick, ssd], media


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots" / "v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    _isolate_profile(Path(tempfile.mkdtemp(prefix="stickshredder_shots_")))

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    import gui.pages.delete_page as delete_page
    from core.config import AppConfig
    from gui.app_window import AppWindow
    from gui.pages.delete_page import _Run
    from gui.theme import ThemeManager, load_fonts

    disks, media = _sample_disks()
    delete_page.list_devices = lambda: list(disks)
    delete_page.media_type_of = lambda device: media.get(device.device_id, "unknown")
    real_worker = delete_page.WipeWorker

    def demo_only_worker(**kwargs):
        if not all(d.device_id.startswith("\\\\.\\DemoDevice") for d in kwargs["devices"]):
            raise RuntimeError("screenshots only ever wipe the demo device")
        return real_worker(**kwargs)

    delete_page.WipeWorker = demo_only_worker

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    config = AppConfig()
    config.cert_output_dir = str(Path(tempfile.mkdtemp(prefix="stickshredder_certs_")))
    config.operator_name = DEMO_OPERATOR
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

    def shot(name: str) -> None:
        settle()
        path = out_dir / f"{name}.png"
        window.grab().save(str(path))
        print(path)

    settle(500)

    # Result: a real demo wipe (10 MB temp file).
    demo = page.add_demo_device()
    page.start_wipe(demo)
    loop = QEventLoop()
    QTimer.singleShot(60_000, loop.quit)
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if page.worker is None else None)
    timer.start(100)
    loop.exec()
    timer.stop()
    if page.current_view() != "result":
        print("demo wipe did not reach the result page", file=sys.stderr)
        return 1
    shot("result-dark")
    window.set_theme_setting("light")
    shot("result-light")

    # Selection page with the sample disk list.
    page.stack.setCurrentWidget(page.select_view)
    page.set_devices(list(disks))
    page.select_device(disks[1])
    shot("main-light")
    window.set_theme_setting("dark")
    shot("main-dark")

    # Running: sample values as on the artboard (overwrite done, verify at 24 %).
    stick = disks[1]
    page._run = _Run(device=stick, options=page.options, passes=1)
    page.running_view.start(stick, page.options)
    page.running_view.stepper.set_step(2, "skipped")
    page.stack.setCurrentWidget(page.running_view)
    window._on_busy_changed(True)
    gib = 1024 ** 3
    page._on_status_message("Checking and dismounting F:...")
    page._on_progress(0, 1, 1, int(57.7 * gib), int(57.7 * gib), 118.0)
    page._on_phase_changed(0, "verifying")
    page._on_verify_progress(0, 0.24, int(13.8 * gib), int(57.7 * gib), 131.0)
    page.running_view.progress_bar.setAnimated(False)
    shot("running-dark")
    window.set_theme_setting("light")
    shot("running-light")
    page._run = None
    window._on_busy_changed(False)
    window.set_theme_setting("dark")
    return 0


if __name__ == "__main__":
    sys.exit(main())
