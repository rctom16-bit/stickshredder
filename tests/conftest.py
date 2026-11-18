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


# Paths under core.config.CONFIG_DIR (%USERPROFILE%\.stickshredder) that
# modules write to. Modules import them by name (``from core.config import
# AUDIT_LOG_FILE``), so every module's copy is redirected, not just config's.
_PROFILE_PATH_NAMES = ("CONFIG_DIR", "CONFIG_FILE", "AUDIT_LOG_FILE", "CERT_COUNTER_FILE",
                       "WIPE_HISTORY_FILE", "DEFAULT_CERT_OUTPUT")


@pytest.fixture(autouse=True)
def _profile_in_tmp(tmp_path, monkeypatch):
    """Unit tests never write into the real %USERPROFILE%\\.stickshredder.

    The audit log, config file, certificate counter, wipe history and
    default certificate folder all point into ``tmp_path``. Tests that set
    their own paths still override these.
    """
    import core.config

    # Import the modules holding copies now, so a later import inside a test
    # cannot bind (and keep) the redirected path of that one test.
    for name in ("core.log", "cli"):
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 — module may not import off Windows
            pass

    real_dir = Path(core.config.CONFIG_DIR)
    fake_dir = tmp_path / "_profile"
    redirect = {}
    for name in _PROFILE_PATH_NAMES:
        real = Path(getattr(core.config, name))
        redirect[real] = fake_dir / real.relative_to(real_dir)
    for mod in list(sys.modules.values()):
        if not str(getattr(mod, "__file__", "") or "").startswith(str(SRC_DIR)):
            continue
        for name in _PROFILE_PATH_NAMES:
            value = getattr(mod, name, None)
            if isinstance(value, Path) and value in redirect:
                monkeypatch.setattr(mod, name, redirect[value])


_KERNEL32_MODULES = ("wipe.device", "wipe.methods", "wipe.passthrough", "wipe.verify",
                     "wipe.drive_probe")
# Modules that send drive commands (firmware commands and storage queries).
# wipe.device: the partition table is removed before every write.
_DRIVE_COMMAND_MODULES = ("wipe.passthrough", "wipe.sat", "wipe.nvme", "wipe.transport",
                          "wipe.device")
_ERROR_ACCESS_DENIED = 5


@pytest.fixture(autouse=True)
def _no_real_drive_commands(monkeypatch):
    """No drive command reaches a real device from a unit test.

    Every DeviceIoControl of the drive-command modules fails like an
    unprivileged call (access denied) unless the test installs its own fake.
    Without this, a made-up handle number (1234) could name a real open
    handle of the test process and the command would reach a real driver.
    """
    def blocked(*_args):
        ctypes.set_last_error(_ERROR_ACCESS_DENIED)
        return 0

    for name in _DRIVE_COMMAND_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — module may not import off Windows
            continue
        k32 = getattr(mod, "kernel32", None)
        if k32 is not None:
            monkeypatch.setattr(k32, "DeviceIoControl", blocked)


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


_ERROR_NO_MORE_FILES = 18


@pytest.fixture(autouse=True)
def _no_real_volume_enumeration(monkeypatch):
    """Unit tests see no volumes of the host (prepare_target's volume scan).

    FindFirstVolumeW answers "no volumes" and no mount point resolves to a
    volume. Tests that need volumes patch these (or the helpers) themselves.
    """
    try:
        mod = importlib.import_module("wipe.device")
    except Exception:  # noqa: BLE001 — module may not import off Windows
        return

    def no_volumes(*_args):
        ctypes.set_last_error(_ERROR_NO_MORE_FILES)
        return -1  # INVALID_HANDLE_VALUE

    def no_mount_point(*_args):
        ctypes.set_last_error(_ERROR_NO_MORE_FILES)
        return 0

    monkeypatch.setattr(mod.kernel32, "FindFirstVolumeW", no_volumes)
    monkeypatch.setattr(mod.kernel32, "GetVolumeNameForVolumeMountPointW", no_mount_point)

    def no_shell():
        raise RuntimeError("Explorer's shell is not used in unit tests")

    # The BitLocker probe reads the host's volumes through Explorer's shell.
    monkeypatch.setattr(mod, "_shell_application", no_shell)
