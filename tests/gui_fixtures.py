"""Shared helpers for the tests of the new GUI shell (gui.app_window & co.).

Imported by the test modules (not a conftest, so the shared conftest stays
untouched). Every GUI test runs on the offscreen Qt platform and with the
whole StickShredder profile (config, audit log, history, certificate counter)
redirected into ``tmp_path``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from wipe.device import DeviceInfo


def make_device(
    letter: str = "E:",
    index: int = 1,
    model: str = "SanDisk 3.2Gen1",
    serial: str = "0901C3F2CFA4",
    size: int = 61_933_428_736,
    connection: str = "USB",
    removable: bool = True,
    system: bool = False,
    internal: bool = False,
    bitlocker: bool = False,
) -> DeviceInfo:
    """A fake physical disk (never touched: the tests patch the worker)."""
    letters = [letter] if letter else []
    return DeviceInfo(
        drive_letter=letter,
        device_id=f"\\\\.\\PhysicalDrive{index}",
        model=model,
        serial_number=serial,
        capacity_bytes=size,
        filesystem="exFAT" if letter else "RAW",
        connection_type=connection,
        is_removable=removable,
        is_system_drive=system,
        is_internal=internal,
        has_bitlocker=bitlocker,
        friendly_name=f"{model} ({letter or 'Disk ' + str(index)})",
        disk_letters=letters,
    )


@pytest.fixture()
def isolated_profile(tmp_path, monkeypatch):
    """Point every StickShredder profile file at ``tmp_path``."""
    import core.config as config
    import core.log as log

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", home)
    monkeypatch.setattr(config, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(config, "AUDIT_LOG_FILE", home / "audit.log")
    monkeypatch.setattr(config, "CERT_COUNTER_FILE", home / "cert_counter.txt")
    monkeypatch.setattr(config, "WIPE_HISTORY_FILE", home / "wipe_history.csv")
    monkeypatch.setattr(config, "DEFAULT_CERT_OUTPUT", home / "certificates")
    monkeypatch.setattr(log, "CONFIG_DIR", home)
    monkeypatch.setattr(log, "AUDIT_LOG_FILE", home / "audit.log")
    monkeypatch.setattr(log, "WIPE_HISTORY_FILE", home / "wipe_history.csv")
    return home


@pytest.fixture()
def app_config(tmp_path, isolated_profile):
    from core.config import AppConfig

    cfg = AppConfig()
    cfg.cert_output_dir = str(tmp_path / "certs")
    return cfg


@pytest.fixture()
def gui_reset(qapp):
    """Undo app-wide theme and language changes after a test.

    Also frees the test's windows right away. A window kept alive by a
    reference cycle (signal → bound method → window) would otherwise be
    freed by Python's garbage collector at some random later moment — for
    example while the next test's ``setStyleSheet`` walks every widget, which
    crashes Qt (access violation).
    """
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    from gui import i18n

    palette = qapp.palette()
    sheet = qapp.styleSheet()
    yield qapp
    i18n.set_language("de")
    for widget in qapp.topLevelWidgets():
        widget.close()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.setStyleSheet(sheet)
    qapp.setPalette(palette)
