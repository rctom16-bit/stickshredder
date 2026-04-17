"""StickShredder entry point — GUI launcher with admin check and CLI fallback."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys


# ── ctypes prototypes for shell32 (Windows) ──────────────────────────────
# On non-Windows platforms, ctypes.WinDLL is unavailable; guard the prototype
# setup so importing this module (e.g. for tests) still works.
if sys.platform == "win32":
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    shell32.ShellExecuteW.argtypes = [
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE  # HINSTANCE on 64-bit

    shell32.IsUserAnAdmin.argtypes = []
    shell32.IsUserAnAdmin.restype = wintypes.BOOL
else:  # pragma: no cover - exercised via tests that patch sys.platform
    shell32 = None  # type: ignore[assignment]


def is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    try:
        return bool(shell32.IsUserAnAdmin())  # type: ignore[union-attr]
    except (AttributeError, OSError):
        return False


def _build_relaunch_params(argv: list[str], frozen: bool) -> str:
    """Build the ShellExecuteW parameters string for a UAC re-launch.

    When frozen (PyInstaller), sys.executable IS the app exe, so argv[0] duplicates
    it and must be excluded. When from source, sys.executable is python.exe and
    argv[0] is the script path — include it.
    """
    if frozen:
        return subprocess.list2cmdline(argv[1:])
    return subprocess.list2cmdline([argv[0], *argv[1:]])


def main() -> int:
    """Application entry point.

    - If CLI arguments are present, delegates to the CLI module.
    - Otherwise, launches the PySide6 GUI.
    """
    if sys.platform != "win32":
        print(
            "StickShredder requires Windows. Detected platform: " + sys.platform + "\n"
            "For Linux, try nwipe (https://github.com/martijnvanbrummelen/nwipe).\n"
            "For a bootable wipe environment, try ShredOS (https://github.com/PartialVolume/shredos.2020.02).",
            file=sys.stderr,
        )
        return 1

    # Ensure src/ is on the path so internal imports work
    src_dir = os.path.dirname(os.path.abspath(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from core.log import setup_logging
    setup_logging()

    # ── CLI mode ─────────────────────────────────────────────────────
    # If any arguments besides the script name are passed, try CLI mode.
    if len(sys.argv) > 1:
        try:
            from cli import main as cli_main
            cli_main()
            return 0
        except ImportError:
            print(
                "CLI module not available. Run without arguments "
                "to launch the GUI.",
                file=sys.stderr,
            )
            return 1

    # ── Admin check ──────────────────────────────────────────────────
    if not is_admin():
        # Try to re-launch as admin via UAC
        try:
            params = _build_relaunch_params(sys.argv, getattr(sys, "frozen", False))
            result = shell32.ShellExecuteW(
                None, "runas", sys.executable,
                params, None, 1,
            )
            # ShellExecuteW returns > 32 on success
            if result > 32:
                return 0
        except (AttributeError, OSError):
            pass

        # If UAC was declined or unavailable, warn and continue
        print(
            "WARNING: StickShredder requires administrator privileges "
            "for raw disk access. Some features may not work.",
            file=sys.stderr,
        )

    # ── GUI mode ─────────────────────────────────────────────────────
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    # High-DPI scaling (PySide6 enables this by default, but be explicit)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("StickShredder")
    app.setOrganizationName("StickShredder")
    app.setApplicationVersion("1.1.0")

    # Apply stylesheet
    from gui.styles import APP_STYLESHEET
    app.setStyleSheet(APP_STYLESHEET)

    # Load config
    from core.config import AppConfig
    config = AppConfig.load()

    # Show admin warning in GUI if not elevated
    if not is_admin():
        QMessageBox.warning(
            None,
            "Administrator Privileges Required",
            "StickShredder is not running as Administrator.\n\n"
            "Raw disk access and device wiping require elevated privileges.\n"
            "Please restart the application as Administrator.",
        )

    # Create and show main window
    from gui.main_window import MainWindow
    window = MainWindow(config)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
