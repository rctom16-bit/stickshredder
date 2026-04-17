"""Cross-cutting integration tests for the v1.1 reformat-after-wipe flow.

These tests exercise the new reformat path end-to-end: the demo wipe writing
a fake filesystem signature, the PowerShell command composition that drives
the real `reformat_drive`, the failure path that surfaces stderr, the
certificate carrying the reformat metadata, and the CLI wiring that passes
``--reformat`` through to the format module.

Six other agents are working in parallel on the production source files
(``wipe/format.py``, ``wipe/demo.py``, ``cli.py``, ``cert/generator.py``,
``gui/wipe_worker.py``). This file only adds tests — it does not modify any
source.

Skip rules:
    * ``test_reformat_drive_powershell_command_composition`` mocks
      ``subprocess.run`` and patches ``sys.platform`` so it runs on any OS.
    * Same for the failure-path test.
    * The demo wipe + cert + CLI tests are pure-Python and run anywhere.
"""

from __future__ import annotations

import ctypes
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────
# ctypes shim — only install on non-Windows. On Windows, the real
# ``ctypes.windll.kernel32`` already exists, and a sibling integration
# test (test_v1_1_hardening.py::test_full_verify_detects_single_bit_flip_16mb)
# fetches it at module-import time via ``ctypes.windll.kernel32`` and depends
# on it being the real kernel32 to drive CreateFileW. Clobbering the
# attribute here would cache a MagicMock that the next test inherits.
# ─────────────────────────────────────────────────────────────────────
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()  # type: ignore[attr-defined]
if sys.platform != "win32":
    ctypes.windll.kernel32 = MagicMock()
sys.modules.setdefault("wmi", MagicMock())

# Imports below intentionally come after the ctypes shim.
from cert.generator import CertificateData, generate_certificate  # noqa: E402
from wipe.demo import (  # noqa: E402
    DEFAULT_DEMO_SIZE,
    create_demo_file,
    wipe_demo_file,
)
from wipe.format import FormatResult, reformat_drive  # noqa: E402
from wipe.methods import ZeroFill  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# 1. End-to-end demo wipe + verify + reformat (cross-platform)
# ─────────────────────────────────────────────────────────────────────


def test_end_to_end_demo_wipe_with_exfat_reformat(tmp_path, monkeypatch):
    """Full demo flow: wipe a virtual file, sample-verify, then "reformat" to
    exFAT with a custom label. The demo path simulates the reformat by writing
    a fake boot-sector signature; we assert all four moving parts succeeded."""
    # Redirect audit log so this test can't clobber user state.
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr("core.log.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.log.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    demo_path = create_demo_file(
        size_bytes=DEFAULT_DEMO_SIZE,
        path=str(tmp_path / "demo.bin"),
    )

    wipe_result = wipe_demo_file(
        demo_path,
        ZeroFill(),
        verify_mode="sample",
        reformat="exfat",
        reformat_label="TESTSTICK",
    )

    # Wipe + verify succeeded
    assert wipe_result.success is True, (
        f"demo wipe failed: {wipe_result.error_message!r}"
    )
    assert wipe_result.verify_result is not None
    assert wipe_result.verify_result.success is True

    # Reformat result must be attached and reflect the requested params.
    assert wipe_result.format_result is not None, (
        "wipe_demo_file must attach a FormatResult when reformat != 'none'"
    )
    fr = wipe_result.format_result
    assert fr.success is True, f"demo reformat failed: {fr.error_message!r}"
    assert fr.filesystem == "exFAT", (
        f"expected canonical 'exFAT' filesystem casing, got {fr.filesystem!r}"
    )
    assert fr.label == "TESTSTICK", (
        f"label should be preserved verbatim for exFAT, got {fr.label!r}"
    )

    # The demo path actually writes the fake signature at offset 0; sanity-
    # check that at least the filesystem tag landed in the file.
    head = Path(demo_path).read_bytes()[:16]
    assert b"exFAT" in head, (
        f"demo reformat should leave an exFAT signature at the start of the "
        f"virtual disk; got first 16 bytes = {head!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# 2. PowerShell command composition (mocked, cross-platform)
# ─────────────────────────────────────────────────────────────────────


def _success_completed_process() -> SimpleNamespace:
    """A subprocess.CompletedProcess-shaped success result for mocks."""
    return SimpleNamespace(
        returncode=0,
        stdout=(
            "DriveLetter FileSystemLabel FileSystem  DriveType\n"
            "----------- --------------- ----------  ---------\n"
            "F           TEST            exFAT       Removable\n"
        ),
        stderr="",
    )


def test_reformat_drive_powershell_command_composition(monkeypatch):
    """`reformat_drive` must invoke PowerShell with a single ``-Command`` script
    that contains the four expected storage-cmdlet calls in the right order
    with the disk number, filesystem, and label substituted in.

    We bypass the Windows platform guard and the PATH check by patching
    ``sys.platform`` and ``shutil.which`` so the test is fully cross-platform.
    """
    # Patch the platform check inside the format module so the Windows-only
    # early-return doesn't fire on Linux/macOS test runners.
    monkeypatch.setattr("wipe.format.sys.platform", "win32")
    # Pretend powershell.exe is on PATH.
    monkeypatch.setattr(
        "wipe.format.shutil.which",
        lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )

    with patch("wipe.format.subprocess.run") as mock_run:
        mock_run.return_value = _success_completed_process()

        result = reformat_drive(
            disk_number=7,
            filesystem="exfat",
            label="TEST",
        )

    # The function returned a successful FormatResult.
    assert isinstance(result, FormatResult)
    assert result.success is True, (
        f"reformat_drive should succeed when PowerShell exits 0; "
        f"got error_message={result.error_message!r}"
    )
    assert result.filesystem == "exFAT"
    assert result.label == "TEST"

    # subprocess.run must have been called exactly once.
    mock_run.assert_called_once()

    # The command vector is the first positional arg. Find the script that was
    # passed after the ``-Command`` switch.
    call_args, call_kwargs = mock_run.call_args
    cmd = call_args[0] if call_args else call_kwargs.get("args")
    assert isinstance(cmd, list), (
        f"subprocess.run should be called with a list argv, got {type(cmd)}"
    )
    assert "-Command" in cmd, (
        f"PowerShell invocation must use -Command; got argv={cmd!r}"
    )
    script_index = cmd.index("-Command") + 1
    assert script_index < len(cmd), "no script string after -Command"
    script = cmd[script_index]
    assert isinstance(script, str)

    # All four storage cmdlets must appear with the disk number substituted in.
    assert "Clear-Disk -Number 7" in script, (
        f"missing/incorrect Clear-Disk in script: {script!r}"
    )
    assert "Initialize-Disk -Number 7" in script, (
        f"missing/incorrect Initialize-Disk in script: {script!r}"
    )
    assert "New-Partition -DiskNumber 7" in script, (
        f"missing/incorrect New-Partition in script: {script!r}"
    )
    # Format-Volume should reference exFAT and the label "TEST" (the script
    # uses "...-FileSystem exFAT ... -NewFileSystemLabel \"TEST\"...", so we
    # check both substrings rather than a fragile exact match).
    assert "Format-Volume" in script
    assert "exFAT" in script, (
        f"Format-Volume must request the exFAT filesystem; got {script!r}"
    )
    assert "TEST" in script, (
        f"Format-Volume must apply the requested 'TEST' label; got {script!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# 3. Failure path: PowerShell stderr is surfaced
# ─────────────────────────────────────────────────────────────────────


def test_reformat_drive_failure_reports_stderr(monkeypatch):
    """When PowerShell exits non-zero with stderr text, that text must appear
    in the FormatResult.error_message so callers can show a useful error."""
    monkeypatch.setattr("wipe.format.sys.platform", "win32")
    monkeypatch.setattr(
        "wipe.format.shutil.which",
        lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )

    failing = SimpleNamespace(
        returncode=1,
        stdout="",
        stderr="Access denied",
    )
    with patch("wipe.format.subprocess.run", return_value=failing):
        result = reformat_drive(
            disk_number=3,
            filesystem="ntfs",
            label="LOCKED",
        )

    assert isinstance(result, FormatResult)
    assert result.success is False, (
        "reformat_drive should fail when PowerShell exits non-zero"
    )
    assert result.error_message is not None
    assert "Access denied" in result.error_message, (
        f"stderr text 'Access denied' should be surfaced verbatim in "
        f"error_message; got {result.error_message!r}"
    )
    # Filesystem and label still reflect what the caller asked for, even on
    # failure — the certificate generator relies on this.
    assert result.filesystem == "NTFS"
    assert result.label == "LOCKED"


# ─────────────────────────────────────────────────────────────────────
# 4. FAT32 on a "large" disk — reformat_drive itself doesn't size-check
# ─────────────────────────────────────────────────────────────────────


def test_reformat_fat32_on_large_disk_warns(monkeypatch):
    """`reformat_drive` doesn't know the disk's physical size — it only
    accepts a disk number and trusts the caller. This test pins that contract:
    requesting fat32 on disk 12 (which would be a >32 GB drive in real life)
    still proceeds and lets PowerShell decide.

    If the implementation ever grows a built-in size check, this test should
    be updated to assert the warning surface rather than the pass-through.
    """
    monkeypatch.setattr("wipe.format.sys.platform", "win32")
    monkeypatch.setattr(
        "wipe.format.shutil.which",
        lambda name: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )

    with patch("wipe.format.subprocess.run") as mock_run:
        mock_run.return_value = _success_completed_process()
        result = reformat_drive(
            disk_number=12,
            filesystem="fat32",
            label="BIGDRIVE",
        )

    # Pass-through behaviour: the FAT32 request reaches PowerShell as-is.
    mock_run.assert_called_once()
    call_args, call_kwargs = mock_run.call_args
    cmd = call_args[0] if call_args else call_kwargs.get("args")
    script = cmd[cmd.index("-Command") + 1]
    assert "FAT32" in script, (
        f"FAT32 must reach the PowerShell script verbatim; got {script!r}"
    )
    assert "Format-Volume" in script

    # And the result reflects the canonical casing PowerShell expects.
    assert result.filesystem == "FAT32"
    # FAT32 labels are capped at 11 chars by the sanitiser. "BIGDRIVE" is 8
    # chars so it should pass through unchanged.
    assert result.label == "BIGDRIVE"


# ─────────────────────────────────────────────────────────────────────
# 5. Certificate carries reformat metadata end-to-end
# ─────────────────────────────────────────────────────────────────────


def test_cert_generated_with_reformat_info(tmp_path, monkeypatch):
    """A CertificateData with reformat_performed=True must produce a valid
    PDF, and the renderer must include the reformat filesystem text. We use
    the Paragraph spy pattern from tests/test_certificate.py to inspect the
    raw text reportlab sees."""
    # Avoid clobbering real config / audit log.
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr("core.log.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.log.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    # Capture every Paragraph constructed during certificate generation so we
    # can assert "exFAT" actually reaches the renderer (and isn't silently
    # dropped by a missed wiring).
    import cert.generator as gen

    captured: list[str] = []
    real_paragraph = gen.Paragraph

    def _spy(text, *args, **kwargs):
        captured.append(text if isinstance(text, str) else str(text))
        return real_paragraph(text, *args, **kwargs)

    monkeypatch.setattr(gen, "Paragraph", _spy)

    out = tmp_path / "cert_reformat.pdf"
    data = CertificateData(
        cert_number=1234,
        date=datetime(2026, 4, 17, 12, 0, 0),
        operator="Integration Test",
        client_reference="",
        asset_tag="",
        device_model="SanDisk Ultra",
        device_manufacturer="SanDisk",
        serial_number="ABC123DEF",
        capacity_bytes=32 * 1024 ** 3,
        filesystem="FAT32",
        connection_type="USB",
        wipe_method="ZeroFill",
        sicherheitsstufe="H-2",
        schutzklasse=2,
        passes=1,
        start_time=datetime(2026, 4, 17, 11, 0, 0),
        end_time=datetime(2026, 4, 17, 11, 25, 0),
        verification_passed=True,
        sectors_checked=100,
        verification_hash="a" * 64,
        company_name="Test GmbH",
        company_address="Teststr. 1\n12345 Berlin",
        company_logo_path="",
        language="en",
        reformat_performed=True,
        reformat_filesystem="exFAT",
        reformat_label="TESTSTICK",
    )

    with patch("cert.generator.audit_log"):
        cert_path = generate_certificate(data, str(out))

    p = Path(cert_path)
    assert p.exists(), f"certificate PDF not created at {cert_path}"
    # >1 KB sanity check — a real cert is several KB; failure mode would be a
    # near-empty file from a renderer crash inside the reformat section.
    size = p.stat().st_size
    assert size > 1024, (
        f"certificate PDF suspiciously small ({size} bytes); "
        f"reformat section may have crashed the build"
    )
    # PDF magic bytes
    assert p.read_bytes()[:5] == b"%PDF-"

    # The renderer must have written the filesystem and label into a Paragraph
    # somewhere — that's the user-visible proof the reformat metadata flowed
    # through.
    blob = "\n".join(captured)
    assert "exFAT" in blob, (
        f"'exFAT' must appear in a rendered Paragraph; captured={captured!r}"
    )
    assert "TESTSTICK" in blob, (
        f"'TESTSTICK' label must appear in a rendered Paragraph; "
        f"captured={captured!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# 6. CLI plumbs --reformat / --reformat-label through to reformat_drive
# ─────────────────────────────────────────────────────────────────────


def _fake_device():
    """A safe-to-wipe device matching the real DeviceInfo surface used by cli."""
    return SimpleNamespace(
        drive_letter="D:",
        device_id=r"\\.\PhysicalDrive5",
        model="Fake USB Stick",
        serial_number="FAKE-CLI-0001",
        capacity_bytes=1024 * 1024,
        filesystem="FAT32",
        connection_type="USB",
        is_removable=True,
        is_system_drive=False,
        is_internal=False,
        has_bitlocker=False,
        has_active_processes=False,
        partition_count=1,
        safe_to_wipe=True,
        friendly_name="Fake USB Stick (D:)",
    )


def _fake_wipe_result_success():
    """A successful WipeResult with a sample-verify success attached."""
    now = datetime.now()
    verify = SimpleNamespace(
        success=True,
        method="sample",
        bytes_verified=1024 * 1024,
        expected_pattern="zeros",
        error_count=0,
        mismatch_offsets=[],
        duration_seconds=0.1,
        sectors_checked=100,
        sectors_matched=100,
        sample_hash="c" * 64,
        timestamp=now,
    )
    return SimpleNamespace(
        method_name="ZeroFill",
        passes=1,
        start_time=now,
        end_time=now,
        bytes_written=1024 * 1024,
        success=True,
        error_message=None,
        verify_result=verify,
        zero_blank_appended=False,
    )


def test_cli_passes_reformat_args_to_worker(monkeypatch):
    """`stickshredder wipe ... --reformat exfat --reformat-label MYUSB` must
    end up calling ``wipe.format.reformat_drive`` with ``filesystem="exfat"``
    and ``label="MYUSB"``. This is the cross-cutting wiring test — the unit
    test for the parser already lives in tests/test_cli.py.
    """
    import cli  # imported lazily so the ctypes shim above is in effect

    # Build the args via the real parser so we exercise the CLI surface.
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "wipe",
            "--device", "D:",
            "--method", "zero",
            "--operator", "Test",
            "--verify", "none",
            "--reformat", "exfat",
            "--reformat-label", "MYUSB",
            "--yes",
        ]
    )

    fake_method = MagicMock()
    fake_method.name = "ZeroFill"
    fake_method.passes = 1
    fake_method.sicherheitsstufe = "1-2"
    fake_method.execute.return_value = _fake_wipe_result_success()

    fake_reformat = MagicMock(return_value=FormatResult(
        success=True,
        method="powershell",
        filesystem="exFAT",
        label="MYUSB",
        partition_style="MBR",
        duration_seconds=1.2,
        error_message=None,
    ))

    # cli.cmd_wipe does `from wipe.format import reformat_drive` *inside* the
    # function. Pre-register a stub module so that import resolves to our
    # mock without depending on the production wipe.format implementation
    # for this CLI-wiring test.
    import sys as _sys
    import types as _types
    fake_format_mod = _types.ModuleType("wipe.format")
    fake_format_mod.reformat_drive = fake_reformat
    saved = _sys.modules.get("wipe.format")
    _sys.modules["wipe.format"] = fake_format_mod

    try:
        with (
            patch.object(cli, "is_admin", return_value=True),
            patch.object(cli, "list_devices", return_value=[_fake_device()]),
            patch.object(cli, "_resolve_wipe_method", return_value=fake_method),
            patch.object(cli, "dismount_volume"),
            patch.object(cli, "open_physical_drive", return_value=99999),
            patch.object(cli, "close_drive"),
            patch.object(cli, "lock_volume"),
            patch.object(cli, "unlock_volume"),
            patch.object(cli, "get_drive_size", return_value=1024 * 1024),
            patch.object(cli, "get_next_cert_number", return_value=1),
            patch.object(cli, "generate_certificate", return_value="C:/tmp/fake.pdf"),
            patch.object(cli, "log_wipe_to_csv"),
            patch.object(cli, "audit_log"),
        ):
            cli.cmd_wipe(args)
    finally:
        # Restore whatever was in sys.modules before so we don't leak the
        # stub into other tests in the same process.
        if saved is not None:
            _sys.modules["wipe.format"] = saved
        else:
            _sys.modules.pop("wipe.format", None)

    # The reformat_drive shim must have been called exactly once with the
    # filesystem and label the CLI parsed.
    fake_reformat.assert_called_once()
    kwargs = fake_reformat.call_args.kwargs
    assert kwargs.get("filesystem") == "exfat", (
        f"CLI must forward --reformat verbatim; got {kwargs.get('filesystem')!r}"
    )
    assert kwargs.get("label") == "MYUSB", (
        f"CLI must forward --reformat-label verbatim; got {kwargs.get('label')!r}"
    )
    # The CLI parses "PhysicalDrive5" out of device_id → disk_number == 5.
    assert kwargs.get("disk_number") == 5, (
        f"CLI must extract the disk number from device_id; "
        f"got {kwargs.get('disk_number')!r}"
    )
