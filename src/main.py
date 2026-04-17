"""StickShredder entry point — GUI launcher with admin check and CLI fallback."""

from __future__ import annotations

import ctypes
import os
import sys


def is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def main() -> int:
    """Application entry point.

    - If CLI arguments are present, delegates to the CLI module.
    - Otherwise, launches the PySide6 GUI.
    """
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
            result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", sys.executable,
                " ".join(sys.argv), None, 1,
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
