"""Tests for main entry point — platform guard and UAC relaunch quoting."""

import ctypes
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure ctypes.windll exists and shell32 calls are safe to import under
# any test runner, mirroring the pattern in tests/test_device.py.
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()
ctypes.windll.shell32 = MagicMock()

# Stub out shell32 WinDLL lookups so `import main` succeeds on non-Windows CI.
if not hasattr(ctypes, "WinDLL"):
    ctypes.WinDLL = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

import main  # noqa: E402  (import after mocking)


# ── Platform guard ───────────────────────────────────────────────────────

def test_main_exits_on_non_windows(monkeypatch, capsys):
    """main() must return 1 and print Linux alternatives on non-Windows."""
    monkeypatch.setattr(sys, "platform", "linux")

    rc = main.main()

    captured = capsys.readouterr()
    assert rc == 1
    assert "nwipe" in captured.err
    assert "ShredOS" in captured.err


def test_main_exits_on_darwin(monkeypatch, capsys):
    """macOS is also unsupported and must bail out with rc=1."""
    monkeypatch.setattr(sys, "platform", "darwin")

    rc = main.main()

    captured = capsys.readouterr()
    assert rc == 1
    assert "darwin" in captured.err


# ── _build_relaunch_params ───────────────────────────────────────────────

def test_build_relaunch_params_quotes_spaces():
    """Paths containing spaces must be quoted so ShellExecuteW parses them as one arg."""
    argv = ["C:/Program Files/app.py", "--flag"]
    params = main._build_relaunch_params(argv, frozen=False)

    # subprocess.list2cmdline wraps the spaced path in double quotes.
    assert '"C:/Program Files/app.py"' in params
    assert "--flag" in params


def test_build_relaunch_params_frozen_excludes_argv0():
    """Under PyInstaller sys.executable IS argv[0]; don't duplicate it."""
    argv = ["C:/StickShredder/StickShredder.exe", "--cli", "wipe"]
    params = main._build_relaunch_params(argv, frozen=True)

    assert "StickShredder.exe" not in params
    assert "--cli" in params
    assert "wipe" in params


def test_build_relaunch_params_source_includes_argv0():
    """Running from source: argv[0] is the .py script and must be preserved."""
    argv = ["src/main.py", "--verbose"]
    params = main._build_relaunch_params(argv, frozen=False)

    assert params.startswith("src/main.py") or params.startswith('"src/main.py"')
    assert "--verbose" in params


def test_build_relaunch_params_frozen_no_extra_args():
    """Frozen exe with no extra args should produce an empty params string."""
    argv = ["C:/StickShredder/StickShredder.exe"]
    params = main._build_relaunch_params(argv, frozen=True)

    assert params == ""


def test_build_relaunch_params_source_argv0_with_spaces():
    """Source-mode argv[0] with spaces must be quoted to survive ShellExecuteW."""
    argv = ["C:/Users/Robin Oertel/main.py", "--flag"]
    params = main._build_relaunch_params(argv, frozen=False)

    assert '"C:/Users/Robin Oertel/main.py"' in params
    assert "--flag" in params
