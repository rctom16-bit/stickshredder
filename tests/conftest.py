"""Shared fixtures and sys.path setup for StickShredder tests."""

import ctypes
import importlib
import sys
import types
from pathlib import Path

# Make `src/` importable so `from core.config import ...` works.
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pytest


class _EmptyStorageNamespace:
    """Stand-in for the WMI storage namespace (MSFT_Disk etc.) with no disks."""

    def MSFT_Disk(self):  # noqa: N802 — WMI class name
        return []

    def MSFT_Partition(self):  # noqa: N802 — WMI class name
        return []


@pytest.fixture(autouse=True)
def _no_real_wmi_queries(monkeypatch):
    """Keep the host's disks and Windows storage stack out of unit tests.

    The storage namespace (MSFT_Disk boot/system flags) answers "no disks";
    without this, the real machine's Disk 0 would leak into every fake
    layout. Any other WMI connection to the real WMI service is refused
    (e.g. the GUI's deferred device scan). Tests that need a layout patch
    ``wmi.WMI`` or ``_get_wmi_connection`` themselves.
    """
    try:
        import wmi
    except ImportError:
        return
    # Test modules stub ``wmi`` with a MagicMock; an earlier monkeypatch may
    # have removed its WMI attribute, so don't assume it exists.
    real_wmi = getattr(wmi, "WMI", None)
    is_stub = not isinstance(wmi, types.ModuleType)
    empty = _EmptyStorageNamespace()

    def router(*args, **kwargs):
        if "storage" in str(kwargs.get("namespace", "")).lower():
            return empty
        if real_wmi is None or not is_stub:
            raise RuntimeError("real WMI is not used in unit tests")
        return real_wmi(*args, **kwargs)

    monkeypatch.setattr(wmi, "WMI", router, raising=False)


@pytest.fixture(autouse=True)
def _signing_key_in_tmp(tmp_path, monkeypatch):
    """Keep the signing key and trust list out of the real user profile."""
    import core.config

    keys = tmp_path / "_signing"
    monkeypatch.setattr(core.config, "KEY_DIR", keys, raising=False)
    monkeypatch.setattr(core.config, "TRUST_FILE", keys / "trusted_keys.json", raising=False)


_KERNEL32_MODULES =("wipe.device", "wipe.methods", "wipe.passthrough", "wipe.verify")
_ERROR_ACCESS_DENIED = 5


@pytest.fixture(autouse=True)
def _no_real_device_handles(monkeypatch):
    """Unit tests never open a real disk or volume.

    Every CreateFileW on a device path (``\\\\.\\PhysicalDriveN``, ``\\\\.\\E:``)
    fails like an unprivileged call (access denied). Ordinary files (e.g.
    the sparse-file verify test) still open. Tests that need a handle patch
    ``kernel32.CreateFileW`` themselves.
    """
    for name in _KERNEL32_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — module may not import off Windows
            continue
        k32 = getattr(mod, "kernel32", None)
        real = getattr(k32, "CreateFileW", None)
        if real is None or not callable(real):
            continue

        def guarded(path, *rest, _real=real):
            text = str(path or "").replace("/", "\\")
            if text.startswith("\\\\.\\") or text.upper().startswith("\\\\?\\GLOBALROOT"):
                ctypes.set_last_error(_ERROR_ACCESS_DENIED)
                return -1  # INVALID_HANDLE_VALUE
            return _real(path, *rest)

        monkeypatch.setattr(k32, "CreateFileW", guarded)
