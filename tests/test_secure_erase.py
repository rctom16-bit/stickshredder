"""Tests for wipe.secure_erase — ATA Secure Erase (EXPERIMENTAL).

These tests NEVER touch real hardware and NEVER let a real ATA command escape:
``wipe.passthrough.ata_identify`` and ``wipe.passthrough.send_ata_command`` are
always mocked.

``wipe.passthrough`` is built in parallel and may not exist on disk yet, so we
register a lightweight stub module in ``sys.modules`` *before* importing
``wipe.secure_erase``. Once the real module lands the stub is skipped and the
exact same patch targets keep working.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── Make sure `wipe.passthrough` is importable (stub if not yet built) ────────
try:  # pragma: no cover - depends on parallel build state
    import wipe.passthrough  # noqa: F401
except Exception:  # pragma: no cover - stub path
    _stub = types.ModuleType("wipe.passthrough")
    _stub.ata_identify = lambda handle: None
    _stub.send_ata_command = lambda handle, **kwargs: None
    sys.modules["wipe.passthrough"] = _stub

import wipe.transport as tr
from wipe.secure_erase import (
    SecureEraseResult,
    secure_erase,
    secure_erase_supported,
    _build_security_block,
    ATA_SECURITY_SET_PASSWORD,
    ATA_SECURITY_ERASE_PREPARE,
    ATA_SECURITY_ERASE_UNIT,
)


@pytest.fixture(autouse=True)
def _sata_disk(monkeypatch):
    """By default the handle is a non-system SATA disk on storahci."""
    monkeypatch.setattr(
        tr, "inspect_handle",
        lambda handle, **kw: tr.StorageInfo("ata", "SATA disk on storahci", disk_number=2),
    )


def _transport_is(monkeypatch, transport: str, reason: str):
    monkeypatch.setattr(tr, "inspect_handle",
                        lambda handle, **kw: tr.StorageInfo(transport, reason))


# ─────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────

def make_identify(
    *,
    security_supported=True,
    security_enabled=False,
    security_locked=False,
    security_frozen=False,
    enhanced_erase_supported=False,
    erase_unit_minutes=2,
):
    """A stand-in for passthrough.IdentifyData with the fields we read."""
    return SimpleNamespace(
        security_supported=security_supported,
        security_enabled=security_enabled,
        security_locked=security_locked,
        security_frozen=security_frozen,
        enhanced_erase_supported=enhanced_erase_supported,
        erase_unit_minutes=erase_unit_minutes,
    )


def ok_command(*args, **kwargs):
    """A stand-in for a successful AtaCommandResult."""
    return SimpleNamespace(success=True, ata_status=0x50, ata_error=0, data=b"")


def fail_command(*args, **kwargs):
    return SimpleNamespace(success=False, ata_status=0x51, ata_error=0x04, data=b"")


HANDLE = 1234


# ─────────────────────────────────────────────────────────────────────
# secure_erase_supported — status probe only, never erases
# ─────────────────────────────────────────────────────────────────────

@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_none_identify_is_unsupported(mock_identify, mock_send):
    mock_identify.return_value = None
    result = secure_erase_supported(HANDLE)
    assert isinstance(result, SecureEraseResult)
    assert result.method == "unsupported"
    assert result.supported is False
    assert result.success is False
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_no_security_feature_is_unsupported(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_supported=False)
    result = secure_erase_supported(HANDLE)
    assert result.method == "unsupported"
    assert result.supported is False
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_frozen_reports_frozen(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_frozen=True)
    result = secure_erase_supported(HANDLE)
    assert result.method == "frozen"
    assert result.frozen is True
    assert result.supported is True
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_normal_drive_reports_secure_erase(mock_identify, mock_send):
    mock_identify.return_value = make_identify(enhanced_erase_supported=False)
    result = secure_erase_supported(HANDLE)
    assert result.method == "ata-secure-erase"
    assert result.supported is True
    assert result.frozen is False
    assert result.success is False  # probe only
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_enhanced_drive_reports_enhanced(mock_identify, mock_send):
    mock_identify.return_value = make_identify(enhanced_erase_supported=True)
    result = secure_erase_supported(HANDLE)
    assert result.method == "ata-enhanced"
    assert result.supported is True
    mock_send.assert_not_called()


@patch("wipe.passthrough.ata_identify")
def test_supported_never_raises_on_exception(mock_identify):
    mock_identify.side_effect = RuntimeError("boom")
    result = secure_erase_supported(HANDLE)
    assert result.method == "error"
    assert result.success is False
    assert result.error is not None


# ─────────────────────────────────────────────────────────────────────
# secure_erase — refuses without issuing commands when unsafe
# ─────────────────────────────────────────────────────────────────────

@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_erase_unsupported_issues_no_command(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_supported=False)
    result = secure_erase(HANDLE)
    assert result.method == "unsupported"
    assert result.success is False
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_erase_none_identify_issues_no_command(mock_identify, mock_send):
    mock_identify.return_value = None
    result = secure_erase(HANDLE)
    assert result.method == "unsupported"
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_erase_frozen_issues_no_command(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_frozen=True)
    result = secure_erase(HANDLE)
    assert result.method == "frozen"
    assert result.frozen is True
    assert result.success is False
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_erase_locked_issues_no_command(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_locked=True)
    result = secure_erase(HANDLE)
    assert result.method == "locked"
    assert result.success is False
    mock_send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# secure_erase — happy path
# ─────────────────────────────────────────────────────────────────────

@patch("wipe.passthrough.send_ata_command", side_effect=ok_command)
@patch("wipe.passthrough.ata_identify")
def test_erase_success_runs_full_sequence(mock_identify, mock_send):
    mock_identify.return_value = make_identify()
    result = secure_erase(HANDLE)

    assert result.success is True
    assert result.method == "ata-secure-erase"
    assert result.supported is True
    assert result.frozen is False
    assert result.error is None

    # Exactly the three SECURITY commands, in order.
    commands = [c.kwargs["command"] for c in mock_send.call_args_list]
    assert commands == [
        ATA_SECURITY_SET_PASSWORD,
        ATA_SECURITY_ERASE_PREPARE,
        ATA_SECURITY_ERASE_UNIT,
    ]


@patch("wipe.passthrough.send_ata_command", side_effect=ok_command)
@patch("wipe.passthrough.ata_identify")
def test_erase_enhanced_sets_enhanced_method_and_bit(mock_identify, mock_send):
    mock_identify.return_value = make_identify(enhanced_erase_supported=True)
    result = secure_erase(HANDLE, enhanced=True)

    assert result.success is True
    assert result.method == "ata-enhanced"

    # The ERASE UNIT (0xF4) data block must carry the enhanced bit (word0 bit1).
    erase_call = next(
        c for c in mock_send.call_args_list
        if c.kwargs["command"] == ATA_SECURITY_ERASE_UNIT
    )
    block = erase_call.kwargs["data_out"]
    assert block[0] & 0x02  # word0 bit 1 set → enhanced erase


@patch("wipe.passthrough.send_ata_command", side_effect=ok_command)
@patch("wipe.passthrough.ata_identify")
def test_erase_password_lands_in_blocks(mock_identify, mock_send):
    mock_identify.return_value = make_identify()
    secure_erase(HANDLE, password="hunter2")

    set_pw_call = next(
        c for c in mock_send.call_args_list
        if c.kwargs["command"] == ATA_SECURITY_SET_PASSWORD
    )
    block = set_pw_call.kwargs["data_out"]
    assert block[2:2 + len("hunter2")] == b"hunter2"
    assert len(block) == 512


# ─────────────────────────────────────────────────────────────────────
# secure_erase — command failures map to method="error"
# ─────────────────────────────────────────────────────────────────────

@patch("wipe.passthrough.send_ata_command", side_effect=fail_command)
@patch("wipe.passthrough.ata_identify")
def test_erase_set_password_failure_is_error(mock_identify, mock_send):
    mock_identify.return_value = make_identify()
    result = secure_erase(HANDLE)
    assert result.success is False
    assert result.method == "error"
    assert result.error is not None
    # First failing command aborts the sequence — only SET PASSWORD attempted.
    assert mock_send.call_count == 1


@patch("wipe.passthrough.ata_identify")
def test_erase_command_returning_none_is_error(mock_identify):
    mock_identify.return_value = make_identify()
    with patch("wipe.passthrough.send_ata_command", return_value=None) as mock_send:
        result = secure_erase(HANDLE)
    assert result.method == "error"
    assert result.success is False
    assert mock_send.call_count == 1


@patch("wipe.passthrough.ata_identify")
def test_erase_erase_unit_failure_is_error(mock_identify):
    mock_identify.return_value = make_identify()
    # SET PASSWORD + ERASE PREPARE succeed, ERASE UNIT fails.
    results = [ok_command(), ok_command(), fail_command()]
    with patch("wipe.passthrough.send_ata_command", side_effect=results) as mock_send:
        result = secure_erase(HANDLE)
    assert result.method == "error"
    assert result.success is False
    assert mock_send.call_count == 3


@patch("wipe.passthrough.ata_identify")
def test_erase_never_raises_on_exception(mock_identify):
    mock_identify.side_effect = RuntimeError("kaboom")
    result = secure_erase(HANDLE)
    assert result.method == "error"
    assert result.success is False
    assert result.error is not None


# ─────────────────────────────────────────────────────────────────────
# _build_security_block — layout
# ─────────────────────────────────────────────────────────────────────

def test_build_block_is_512_bytes_password_padded():
    block = _build_security_block("pw")
    assert len(block) == 512
    assert block[0] == 0x00 and block[1] == 0x00  # control word 0 (user, normal)
    assert block[2:4] == b"pw"
    assert block[4:] == b"\x00" * (512 - 4)


def test_build_block_enhanced_sets_word0_bit1():
    block = _build_security_block("pw", enhanced=True)
    assert block[0] & 0x02
    assert block[2:4] == b"pw"


def test_build_block_truncates_long_password_to_32_bytes():
    long_pw = "x" * 100
    block = _build_security_block(long_pw)
    # Password occupies bytes 2..33; byte 34 onward must be zero.
    assert block[2:34] == b"x" * 32
    assert block[34] == 0x00


# ─────────────────────────────────────────────────────────────────────
# Only a non-system SATA disk on storahci ever gets an ATA command
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("transport", ["sat", "nvme", "none"])
@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_sends_nothing_unless_the_transport_is_ata(
        mock_identify, mock_send, monkeypatch, transport):
    _transport_is(monkeypatch, transport, "disk 0 is a system disk")
    mock_identify.return_value = make_identify()

    result = secure_erase_supported(HANDLE)

    assert result.method == "unsupported"
    assert result.supported is False
    assert "system disk" in result.error
    mock_identify.assert_not_called()
    mock_send.assert_not_called()


@pytest.mark.parametrize("transport", ["sat", "nvme", "none"])
@patch("wipe.passthrough.send_ata_command", side_effect=ok_command)
@patch("wipe.passthrough.ata_identify")
def test_erase_issues_no_command_unless_the_transport_is_ata(
        mock_identify, mock_send, monkeypatch, transport):
    _transport_is(monkeypatch, transport, "USB device is not an ATA drive")
    mock_identify.return_value = make_identify()

    result = secure_erase(HANDLE)

    assert result.success is False
    assert result.method == "unsupported"
    assert "not an ATA drive" in result.error
    mock_identify.assert_not_called()
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command", side_effect=ok_command)
@patch("wipe.passthrough.ata_identify")
def test_erase_issues_no_command_when_the_transport_check_raises(
        mock_identify, mock_send, monkeypatch):
    def boom(handle, **kw):
        raise RuntimeError("storage stack exploded")

    monkeypatch.setattr(tr, "inspect_handle", boom)
    mock_identify.return_value = make_identify()

    result = secure_erase(HANDLE)

    assert result.success is False
    mock_identify.assert_not_called()
    mock_send.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# The drive's own time estimate (IDENTIFY words 89/90) — shown while it erases
# ─────────────────────────────────────────────────────────────────────

@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_reports_the_normal_erase_time_estimate(mock_identify, mock_send):
    mock_identify.return_value = make_identify(erase_unit_minutes=4)
    result = secure_erase_supported(HANDLE)
    assert result.method == "ata-secure-erase"
    assert result.estimated_minutes == 4
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_reports_the_enhanced_erase_time_estimate(mock_identify, mock_send):
    words = [0] * 256
    words[90] = 3  # word 90: enhanced erase time, 2-minute units -> 6 minutes
    identify = make_identify(enhanced_erase_supported=True, erase_unit_minutes=4)
    identify.raw_words = tuple(words)
    mock_identify.return_value = identify
    result = secure_erase_supported(HANDLE)
    assert result.method == "ata-enhanced"
    assert result.estimated_minutes == 6
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_without_estimate_reports_zero(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_frozen=True, erase_unit_minutes=4)
    assert secure_erase_supported(HANDLE).estimated_minutes == 0
    mock_identify.return_value = make_identify(enhanced_erase_supported=True)
    assert secure_erase_supported(HANDLE).estimated_minutes == 0  # no word 90


# ─────────────────────────────────────────────────────────────────────
# Review fixes: a locked drive, and the temporary password after a failure
# ─────────────────────────────────────────────────────────────────────

def _commands(mock_send):
    return [c.kwargs["command"] for c in mock_send.call_args_list]


F1, F2, F3, F4, F6 = 0xF1, 0xF2, 0xF3, 0xF4, 0xF6


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_supported_reports_a_locked_drive(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_enabled=True, security_locked=True)
    result = secure_erase_supported(HANDLE)
    assert result.method == "locked"
    assert result.supported is True and result.frozen is False
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_failed_erase_unit_removes_the_password_again(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(), make_identify(security_enabled=True),
                                 make_identify(security_enabled=False)]
    mock_send.side_effect = [ok_command(), ok_command(), fail_command(), ok_command()]
    result = secure_erase(HANDLE)
    assert result.success is False and result.method == "error"
    assert _commands(mock_send) == [F1, F3, F4, F6]
    assert mock_send.call_args_list[3].kwargs["data_out"] == _build_security_block("StickShredder")
    assert result.password_set is False


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_failed_prepare_removes_the_password_again(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(), make_identify(security_enabled=True),
                                 make_identify(security_enabled=False)]
    mock_send.side_effect = [ok_command(), fail_command(), ok_command()]
    result = secure_erase(HANDLE)
    assert _commands(mock_send) == [F1, F3, F6]
    assert result.password_set is False


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_password_stays_reported_when_the_drive_is_already_locked(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(),
                                 make_identify(security_enabled=True, security_locked=True)]
    mock_send.side_effect = [ok_command(), ok_command(), fail_command()]
    result = secure_erase(HANDLE)
    assert _commands(mock_send) == [F1, F3, F4]   # no DISABLE PASSWORD on a locked drive
    assert result.password_set is True


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_password_stays_reported_when_disable_fails(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(), make_identify(security_enabled=True)]
    mock_send.side_effect = [ok_command(), ok_command(), fail_command(), fail_command()]
    result = secure_erase(HANDLE)
    assert _commands(mock_send) == [F1, F3, F4, F6]
    assert result.password_set is True


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_password_stays_reported_when_the_drive_cannot_be_asked(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(), None]
    mock_send.side_effect = [ok_command(), ok_command(), fail_command()]
    result = secure_erase(HANDLE)
    assert _commands(mock_send) == [F1, F3, F4]
    assert result.password_set is True


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_failed_set_password_without_a_password_on_the_drive(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(), make_identify(security_enabled=False)]
    mock_send.side_effect = [fail_command()]
    result = secure_erase(HANDLE)
    assert _commands(mock_send) == [F1]
    assert result.password_set is False


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_exception_after_the_password_was_sent_still_cleans_up(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(), make_identify(security_enabled=True),
                                 make_identify(security_enabled=False)]
    mock_send.side_effect = [ok_command(), RuntimeError("bridge gone"), ok_command()]
    result = secure_erase(HANDLE)
    assert result.method == "error"
    assert _commands(mock_send) == [F1, F3, F6]
    assert result.password_set is False


@patch("wipe.passthrough.send_ata_command", side_effect=ok_command)
@patch("wipe.passthrough.ata_identify")
def test_successful_erase_leaves_no_password(mock_identify, mock_send):
    mock_identify.return_value = make_identify()
    assert secure_erase(HANDLE).password_set is False


@patch("wipe.passthrough.ata_identify")
def test_a_broken_audit_log_does_not_turn_success_into_failure(mock_identify, monkeypatch):
    import wipe.secure_erase as module

    def broken(_message):
        raise OSError("disk full")

    monkeypatch.setattr(module, "audit_log", broken)
    mock_identify.return_value = make_identify()
    with patch("wipe.passthrough.send_ata_command", side_effect=ok_command):
        result = secure_erase(HANDLE)
    assert result.success is True


# ─────────────────────────────────────────────────────────────────────
# unlock_drive — "Gesperrte Platte entsperren": one try, temporary password only
# ─────────────────────────────────────────────────────────────────────

from wipe.secure_erase import unlock_drive  # noqa: E402


def _expired_identify():
    identify = make_identify(security_enabled=True, security_locked=True)
    words = [0] * 256
    words[128] = 0x0017  # supported, enabled, locked, attempt count expired
    identify.raw_words = tuple(words)
    return identify


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_unlock_unlocks_and_removes_the_password(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(security_enabled=True, security_locked=True),
                                 make_identify(security_enabled=False)]
    mock_send.side_effect = [ok_command(), ok_command()]
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (True, "unlocked")
    assert _commands(mock_send) == [F2, F6]
    for call in mock_send.call_args_list:
        assert call.kwargs["data_out"] == _build_security_block("StickShredder")


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_unlock_of_an_unlocked_drive_only_removes_the_password(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(security_enabled=True),
                                 make_identify(security_enabled=False)]
    mock_send.side_effect = [ok_command()]
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (True, "unlocked")
    assert _commands(mock_send) == [F6]


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_unlock_of_a_drive_without_password_does_nothing(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_enabled=False)
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (True, "no_password")
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_unlock_tries_the_password_only_once(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_enabled=True, security_locked=True)
    mock_send.side_effect = [fail_command()]
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (False, "wrong_password")
    assert _commands(mock_send) == [F2]


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_unlock_refuses_a_frozen_drive(mock_identify, mock_send):
    mock_identify.return_value = make_identify(security_enabled=True, security_frozen=True)
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (False, "frozen")
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_unlock_refuses_when_the_attempts_are_used_up(mock_identify, mock_send):
    mock_identify.return_value = _expired_identify()
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (False, "expired")
    mock_send.assert_not_called()


@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_unlock_reports_an_error_when_the_password_stays(mock_identify, mock_send):
    mock_identify.side_effect = [make_identify(security_enabled=True, security_locked=True),
                                 make_identify(security_enabled=True)]
    mock_send.side_effect = [ok_command(), ok_command()]
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (False, "error")


@pytest.mark.parametrize("transport", ["sat", "nvme", "none"])
@patch("wipe.passthrough.send_ata_command")
@patch("wipe.passthrough.ata_identify")
def test_unlock_sends_nothing_unless_the_transport_is_ata(
        mock_identify, mock_send, monkeypatch, transport):
    _transport_is(monkeypatch, transport, "USB device is not an ATA drive")
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (False, "unsupported")
    assert "not an ATA drive" in result.error
    mock_identify.assert_not_called()
    mock_send.assert_not_called()


@patch("wipe.passthrough.ata_identify", side_effect=RuntimeError("boom"))
def test_unlock_never_raises(mock_identify):
    result = unlock_drive(HANDLE)
    assert (result.success, result.state) == (False, "error")
    assert "boom" in result.error
