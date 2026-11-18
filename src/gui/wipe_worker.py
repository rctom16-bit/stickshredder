"""QThread-based worker for running wipe operations off the main thread."""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal

from core.config import AppConfig, get_next_cert_number, load_signing_key
from core.log import audit_log, history_result_value, log_wipe_to_csv
from cert.payload import result_status
from cert.generator import CertificateData, generate_certificate, format_capacity
from cert.signing import SigningKeyError
from wipe.device import (
    DeviceInfo,
    UnsafeTargetError,
    prepare_target,
    release_volumes,
    open_physical_drive,
    close_drive,
    get_drive_size,
    lock_volume,
    unlock_volume,
)
from wipe import hardware_erase
from wipe.methods import WipeMethod, WipeResult
from wipe.demo import create_demo_file, wipe_demo_file, verify_demo_file
from wipe.hidden_areas import detect_hidden_areas, hidden_bytes
from wipe.secure_erase import SecureEraseResult

if TYPE_CHECKING:
    pass


# Accepted values for the verify_mode parameter passed to WipeMethod.execute().
VERIFY_MODES = ("none", "sample", "full")

# Accepted values for the reformat parameter on WipeWorker.
# "none" means do not reformat; the others select the target filesystem.
REFORMAT_MODES = ("none", "fat32", "exfat", "ntfs")


def _expected_pattern_for(method: WipeMethod) -> bytes:
    """Return the expected post-wipe byte pattern for the given method.

    Used by the demo code path which still runs verification out-of-band.
    Real-device verification is handled inside WipeMethod.execute() and
    does not need this helper.
    """
    from wipe.methods import ZeroFill, RandomThreePass, BsiVsitr
    if isinstance(method, ZeroFill):
        return b"\x00"
    if isinstance(method, (RandomThreePass, BsiVsitr)):
        return b""  # random — verify just checks "non-zero"
    # CustomWipe or anything else: delegate to the method if it exposes it.
    try:
        return method._expected_final_pattern()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return b"\x00"


class HardwareEraseStopped(RuntimeError):
    """The hardware erase stopped before it finished: nothing to certify."""


def _stopped_message(report: hardware_erase.EraseReport) -> str:
    """German / English error text (the page shows the half for its language)."""
    if report.status == "refused":
        return ("Hardware-Löschung nicht möglich, nichts wurde geschrieben / "
                f"Hardware erase not possible, nothing was written: {report.detail}")
    return f"Hardware-Löschung fehlgeschlagen / Hardware erase failed: {report.detail}"


def _verify_ok(result: Any) -> bool | None:
    """Return the verify success status, or None if no verify was performed."""
    if result is None:
        return None
    return bool(result.success)


class WipeWorker(QThread):
    """Executes wipe operations for one or more devices in a background thread.

    Signals
    -------
    progress_updated(device_index, pass_num, total_passes, bytes_written, total_bytes, speed_mbps)
        Emitted during the overwrite phase.
    verify_progress(device_index, fraction, bytes_done, total_bytes, speed_mbps)
        Emitted during full-verification reads. Fraction is 0.0..1.0.
    phase_changed(device_index, phase)
        phase is one of "wiping", "verifying", "reformatting", "done".
    device_completed(device_index, success, cert_path)
    all_completed()
    error(device_index, message)
    status_message(message)
    """

    # byte counts use 'qlonglong' (int64) so drives >2 GiB don't overflow
    # Qt's default signed 32-bit `int` marshalling.
    progress_updated = Signal(int, int, int, 'qlonglong', 'qlonglong', float)
    verify_progress = Signal(int, float, 'qlonglong', 'qlonglong', float)
    phase_changed = Signal(int, str)
    device_completed = Signal(int, bool, str)
    all_completed = Signal()
    error = Signal(int, str)
    status_message = Signal(str)
    stall_detected = Signal(int, int, str)  # device_index, seconds_stalled, hint
    # Hardware erase (Beta): the drive starts erasing itself (erase method,
    # its own estimate in minutes, 0 = none) and is done (an EraseReport).
    hardware_erase_started = Signal(int, str, int)
    hardware_erase_finished = Signal(int, object)

    # Demo SSD: how long the pretend drive erase takes.
    DEMO_ERASE_SECONDS = 3.0

    def __init__(
        self,
        devices: list[DeviceInfo],
        wipe_method: WipeMethod,
        config: AppConfig,
        schutzklasse: int = 2,
        operator: str = "",
        verify_mode: str = "full",
        reformat: str = "none",
        reformat_label: str = "USB",
        reformat_partition: str = "MBR",
        hardware_erase: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.devices = devices
        # With hardware_erase the drive erases itself first; wipe_method is
        # then the overwrite after it (the page passes one pass of zeros).
        self.wipe_method = wipe_method
        self.hardware_erase = hardware_erase
        self.config = config
        self.schutzklasse = schutzklasse
        self.operator = operator or config.operator_name or "Unknown"
        if verify_mode not in VERIFY_MODES:
            audit_log(
                f"WipeWorker: unknown verify_mode={verify_mode!r}, "
                f"falling back to 'full'"
            )
            verify_mode = "full"
        self.verify_mode = verify_mode
        if reformat not in REFORMAT_MODES:
            audit_log(
                f"WipeWorker: unknown reformat={reformat!r}, "
                f"falling back to 'none'"
            )
            reformat = "none"
        self.reformat = reformat
        self.reformat_label = reformat_label
        self.reformat_partition = reformat_partition
        self._cancel_event = threading.Event()
        # Stall-watchdog state. last_progress_time is updated on every
        # progress or verify_progress emission. A background thread (spawned
        # in run()) warns via stall_detected if no progress has been reported
        # for STALL_THRESHOLD_SECONDS — typically a sign of a USB controller
        # hang or an AV product silently blocking raw disk writes.
        self._last_progress_time: float | None = None
        self._stall_warned = False
        self._watchdog_generation = 0

    STALL_THRESHOLD_SECONDS = 60

    def cancel(self) -> None:
        """Request cancellation. The worker checks this between operations."""
        self._cancel_event.set()
        audit_log("Wipe cancellation requested by user")

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _note_progress(self) -> None:
        """Record that progress just happened — resets the stall timer."""
        import time as _time
        self._last_progress_time = _time.monotonic()
        self._stall_warned = False

    def _start_stall_watchdog(self, device_index: int) -> None:
        """Spawn a daemon thread that fires stall_detected if progress stops.

        The watchdog exits on cancellation or when _last_progress_time is
        cleared at the end of each device's work.
        """
        import time as _time

        # A newer watchdog (restarted after a hardware erase) retires this one.
        self._watchdog_generation += 1
        generation = self._watchdog_generation

        def _current() -> bool:
            return (not self.is_cancelled and self._last_progress_time is not None
                    and generation == self._watchdog_generation)

        def _watch() -> None:
            while _current():
                _time.sleep(5)
                if not _current():
                    return
                elapsed = _time.monotonic() - self._last_progress_time
                if elapsed >= self.STALL_THRESHOLD_SECONDS and not self._stall_warned:
                    self._stall_warned = True
                    hint = (
                        f"No write progress for {int(elapsed)}s. The USB "
                        "controller may have hung. Try: (1) cancel this "
                        "wipe, (2) unplug and replug the drive, "
                        "(3) restart the wipe. If it persists, try a "
                        "different USB port or see the README troubleshooting."
                    )
                    audit_log(f"Stall detected on device {device_index}: {hint}")
                    self.stall_detected.emit(device_index, int(elapsed), hint)
                    # Keep watching in case it recovers; but don't re-warn.

        t = threading.Thread(target=_watch, daemon=True, name="WipeStallWatchdog")
        t.start()

    # -- Hardware erase (Beta) ------------------------------------------------

    def _on_erase_started(self, device_index: int, method: str, minutes: int) -> None:
        # The drive reports no progress while it erases itself: no stall warning.
        self._last_progress_time = None
        self.hardware_erase_started.emit(device_index, method, int(minutes or 0))

    def _on_erase_finished(self, device_index: int, report, device: DeviceInfo,
                           announce_wipe: bool = True) -> None:
        self.hardware_erase_finished.emit(device_index, report)
        if report.status != "erased":
            return
        self._note_progress()
        self._start_stall_watchdog(device_index)
        if announce_wipe:
            self.status_message.emit(
                f"Wiping {device.friendly_name} with {self.wipe_method.name}..."
            )

    def _demo_hardware_erase(self, device_index: int, device: DeviceInfo) -> SecureEraseResult:
        """Demo SSD: pretend the drive erases itself for a few seconds."""
        import time as _time

        method = "ata-enhanced"
        self.status_message.emit("Checking hardware erase support...")
        self._on_erase_started(device_index, method, 1)
        start = _time.monotonic()
        while _time.monotonic() - start < self.DEMO_ERASE_SECONDS:
            _time.sleep(0.05)
        duration = _time.monotonic() - start
        audit_log(f"Demo: hardware erase simulated ({duration:.1f}s), no drive touched")
        report = hardware_erase.EraseReport("erased", method=method,
                                            duration_seconds=duration, estimated_minutes=1)
        self._on_erase_finished(device_index, report, device, announce_wipe=False)
        return SecureEraseResult(success=True, method=method, supported=True, frozen=False,
                                 duration_seconds=duration, error=None)

    def run(self) -> None:  # noqa: C901
        """Main worker loop — processes each device sequentially."""
        # Every certificate is signed. Load the key before touching any
        # device so a key problem stops the run while the data is intact.
        try:
            signing_key = load_signing_key()
        except (SigningKeyError, OSError) as exc:
            message = (
                "Signaturschlüssel kann nicht geladen werden, es wurde nichts gelöscht / "
                f"Signing key cannot be loaded, nothing was wiped: {exc}"
            )
            audit_log(f"WipeWorker: {message}")
            self.status_message.emit(message)
            for idx in range(len(self.devices)):
                self.error.emit(idx, message)
                self.device_completed.emit(idx, False, "")
            self.all_completed.emit()
            return

        for idx, device in enumerate(self.devices):
            if self.is_cancelled:
                self.status_message.emit("Wipe cancelled by user.")
                break

            self.status_message.emit(
                f"Processing device {idx + 1}/{len(self.devices)}: "
                f"{device.friendly_name}"
            )

            handle: int | None = None
            volume_locks: list[int] = []
            demo_file_path: str | None = None
            cert_path = ""
            success = False
            verify_result: Any = None
            hidden = None  # HiddenAreaInfo from the real-device HPA/DCO probe
            secure_erase_result: SecureEraseResult | None = None
            is_demo = device.device_id.startswith(r"\\.\DemoDevice")

            # Signal that this device is entering the wipe phase.
            self.phase_changed.emit(idx, "wiping")

            # Start the stall watchdog for this device. _note_progress is
            # called from the progress/verify callbacks below; if the clock
            # rolls past STALL_THRESHOLD_SECONDS without one, the watchdog
            # emits stall_detected.
            self._note_progress()
            self._start_stall_watchdog(idx)

            try:
                if is_demo:
                    # ── Demo mode: use virtual disk file ──
                    self.status_message.emit("Demo Mode: creating virtual disk file...")
                    demo_file_path = create_demo_file(device.capacity_bytes)
                    drive_size = device.capacity_bytes
                    self.status_message.emit(
                        f"Demo drive size: {format_capacity(drive_size)}"
                    )

                    if self.is_cancelled:
                        raise InterruptedError("Cancelled before wipe")

                    erase_started_at = datetime.now()
                    if self.hardware_erase:
                        secure_erase_result = self._demo_hardware_erase(idx, device)

                    def _progress_cb(
                        pass_num: int,
                        total_passes: int,
                        bytes_written: int,
                        total_bytes: int,
                        speed: float,
                        _idx: int = idx,
                    ) -> None:
                        if self.is_cancelled:
                            raise InterruptedError("Cancelled during wipe")
                        self._note_progress()
                        self.progress_updated.emit(
                            _idx, pass_num, total_passes,
                            bytes_written, total_bytes, speed,
                        )

                    self.status_message.emit(
                        f"Demo: wiping virtual disk with {self.wipe_method.name}..."
                    )

                    def _verify_progress_cb(
                        fraction: float,
                        bytes_done: int,
                        total_bytes: int,
                        speed: float,
                        _idx: int = idx,
                    ) -> None:
                        if self.is_cancelled:
                            raise InterruptedError("Cancelled during verify")
                        self._note_progress()
                        self.phase_changed.emit(_idx, "verifying")
                        self.verify_progress.emit(
                            _idx, fraction, bytes_done, total_bytes, speed,
                        )

                    wipe_result: WipeResult = wipe_demo_file(
                        demo_file_path, self.wipe_method,
                        progress_callback=_progress_cb,
                        verify_mode=self.verify_mode,
                        verify_progress_callback=_verify_progress_cb,
                        reformat=self.reformat,
                        reformat_label=self.reformat_label,
                        reformat_partition=self.reformat_partition,
                    )

                    if not wipe_result.success:
                        raise RuntimeError(
                            f"Demo wipe failed: {wipe_result.error_message}"
                        )
                    if secure_erase_result is not None:
                        # Named and timed as the whole procedure, like the real one.
                        wipe_result.method_name = (
                            f"{hardware_erase.erase_label(secure_erase_result.method)} "
                            f"+ {self.wipe_method.name}"
                        )
                        wipe_result.start_time = erase_started_at

                    if self.is_cancelled:
                        raise InterruptedError("Cancelled after wipe")

                    verify_result = wipe_result.verify_result
                    if self.verify_mode != "none":
                        self.phase_changed.emit(idx, "verifying")
                    if self.verify_mode != "none" and verify_result is None:
                        audit_log("Demo verification was requested but returned None")

                    # Demo path: pull format result Agent C attached to the
                    # WipeResult (None if reformat == "none" or it failed).
                    format_result = getattr(wipe_result, "format_result", None)
                    if self.reformat != "none":
                        self.phase_changed.emit(idx, "reformatting")
                        self.status_message.emit(
                            f"Demo: reformatted virtual disk to "
                            f"{self.reformat.upper()}"
                        )

                else:
                    # ── Real device mode ──
                    # 1. Re-check identity and dismount every volume of
                    #    the disk. Raises UnsafeTargetError -> device fails.
                    letters = ", ".join(
                        device.disk_letters or [device.drive_letter or device.device_id]
                    )
                    self.status_message.emit(f"Checking and dismounting {letters}...")
                    # The volumes stay locked until the wipe and verification
                    # are done; otherwise Windows can remount them mid-wipe.
                    volume_locks = prepare_target(device)

                    # 2. Open physical drive
                    self.status_message.emit(f"Opening {device.device_id}...")
                    handle = open_physical_drive(device.device_id)

                    # 3. Physical-drive lock intentionally skipped.
                    # FSCTL_LOCK_VOLUME is documented for volume handles
                    # (\\.\E:), not physical-drive handles (\\.\PhysicalDrive1).
                    # On some USB controllers this deadlocks WriteFile, and
                    # prepare_target() already dismounted every volume, giving us exclusive
                    # access to the media.
                    audit_log(
                        f"Physical-drive lock skipped for {device.drive_letter} "
                        f"(dismount already exclusive)"
                    )

                    # 4. Get drive size
                    drive_size = get_drive_size(handle)
                    self.status_message.emit(
                        f"Drive size: {format_capacity(drive_size)}"
                    )

                    # 4b. Probe for hidden areas (HPA/DCO) — read-only. USB
                    # bridges report unsupported; we never modify the drive.
                    hidden = detect_hidden_areas(handle)
                    if hidden.supported and (hidden.hpa_present or hidden.dco_present):
                        parts = []
                        if hidden.hpa_present:
                            parts.append(
                                f"HPA {format_capacity(hidden_bytes(hidden.hpa_hidden_sectors))}"
                            )
                        if hidden.dco_present:
                            parts.append(
                                f"DCO {format_capacity(hidden_bytes(hidden.dco_hidden_sectors))}"
                            )
                        self.status_message.emit(
                            "Hidden area(s) detected (" + ", ".join(parts)
                            + ") — recorded on certificate; not reached by overwrite."
                        )

                    if self.is_cancelled:
                        raise InterruptedError("Cancelled before wipe")

                    # 5. Execute wipe + (optional) verification in one call.
                    # Verify runs inline inside WipeMethod.execute() when
                    # verify_mode != "none". The verify callback announces the
                    # phase transition on its first tick.
                    verifying_announced = {"value": False}

                    def _progress_cb(
                        pass_num: int,
                        total_passes: int,
                        bytes_written: int,
                        total_bytes: int,
                        speed: float,
                        _idx: int = idx,
                    ) -> None:
                        if self.is_cancelled:
                            raise InterruptedError("Cancelled during wipe")
                        self._note_progress()
                        self.progress_updated.emit(
                            _idx, pass_num, total_passes,
                            bytes_written, total_bytes, speed,
                        )

                    def _verify_cb(
                        fraction: float,
                        bytes_done: int,
                        total_bytes: int,
                        speed: float,
                        _idx: int = idx,
                    ) -> None:
                        if self.is_cancelled:
                            raise InterruptedError("Cancelled during verify")
                        self._note_progress()
                        if not verifying_announced["value"]:
                            verifying_announced["value"] = True
                            self.phase_changed.emit(_idx, "verifying")
                            self.status_message.emit(
                                f"Verifying {device.friendly_name}..."
                            )
                        self.verify_progress.emit(
                            _idx, fraction, bytes_done, total_bytes, speed,
                        )

                    if self.hardware_erase:
                        # Same procedure as the CLI: the drive's own erase, then
                        # one pass of zeros + verification. A drive that says
                        # no is refused before anything is written.
                        self.status_message.emit("Checking hardware erase support...")
                        outcome = hardware_erase.run(
                            handle, drive_size, self.wipe_method,
                            verify_mode=self.verify_mode,
                            progress_callback=_progress_cb,
                            verify_progress_callback=_verify_cb,
                            on_erase_started=lambda method, minutes, _i=idx:
                                self._on_erase_started(_i, method, minutes),
                            on_erase_finished=lambda report, _i=idx, _d=device:
                                self._on_erase_finished(_i, report, _d),
                        )
                        if outcome.report.status != "erased":
                            raise HardwareEraseStopped(_stopped_message(outcome.report))
                        secure_erase_result = outcome.erase
                        wipe_result = outcome.wipe_result
                    else:
                        self.status_message.emit(
                            f"Wiping {device.friendly_name} with {self.wipe_method.name}..."
                        )
                        wipe_result = self.wipe_method.execute(
                            handle,
                            drive_size,
                            progress_callback=_progress_cb,
                            verify_mode=self.verify_mode,
                            verify_progress_callback=_verify_cb,
                        )
                    # Wipe + verify done: let go of the volumes before any
                    # reformat (which needs the disk free).
                    locks, volume_locks = volume_locks, []
                    release_volumes(locks)

                    if not wipe_result.success:
                        raise RuntimeError(
                            f"Wipe failed: {wipe_result.error_message}"
                        )

                    if self.is_cancelled:
                        raise InterruptedError("Cancelled after wipe")

                    # Pull the verify result produced by execute() itself.
                    verify_result = wipe_result.verify_result

                    # Sample verification doesn't emit verify_progress ticks,
                    # so announce the phase here if it wasn't already.
                    if (
                        self.verify_mode != "none"
                        and not verifying_announced["value"]
                    ):
                        self.phase_changed.emit(idx, "verifying")

                    # 6b. Optional reformat — only when wipe (and any verify)
                    # succeeded. Reformat failures are logged but do NOT fail
                    # the device-level wipe; the cert is still issued and
                    # simply omits the reformat fields.
                    format_result = None
                    if self.reformat != "none" and wipe_result.success:
                        self.phase_changed.emit(idx, "reformatting")
                        self.status_message.emit(
                            f"Reformatting {device.drive_letter} to "
                            f"{self.reformat.upper()}..."
                        )
                        from wipe.format import reformat_drive
                        import re
                        m = re.search(r"PhysicalDrive(\d+)", device.device_id)
                        # Reformatting writes a new partition table: same
                        # gate as the wipe. A refusal only skips the reformat.
                        # The check locks the volumes again; Clear-Disk needs
                        # the disk free, so the locks go before the reformat.
                        try:
                            release_volumes(prepare_target(device))
                            unsafe = None
                        except UnsafeTargetError as exc:
                            unsafe = exc
                        if unsafe is not None:
                            audit_log(
                                f"Reformat skipped for {device.device_id}: {unsafe}"
                            )
                            self.status_message.emit(
                                f"Formatieren übersprungen / reformat skipped: {unsafe}"
                            )
                        elif m:
                            disk_number = int(m.group(1))
                            format_result = reformat_drive(
                                disk_number=disk_number,
                                filesystem=self.reformat,
                                label=self.reformat_label,
                                partition_style=self.reformat_partition,
                                progress_callback=lambda msg: self.status_message.emit(msg),
                            )
                            # Attach to the wipe result so cert generation can see it
                            try:
                                wipe_result.format_result = format_result
                            except Exception:
                                pass
                            if not format_result.success:
                                audit_log(
                                    f"Reformat failed for {device.drive_letter}: "
                                    f"{format_result.error_message}"
                                )
                        else:
                            audit_log(
                                f"Reformat skipped for {device.drive_letter}: "
                                f"could not parse PhysicalDrive number from "
                                f"{device.device_id!r}"
                            )

                    # 7. Unlock
                    try:
                        unlock_volume(handle)
                    except OSError:
                        pass

                    # 8. Close drive
                    close_drive(handle)
                    handle = None

                # 9. Generate certificate
                self.status_message.emit("Generating certificate...")
                cert_number = get_next_cert_number()
                verify_ok = _verify_ok(verify_result)
                # Certificate field still takes a bool; skipped verify counts
                # as False for the document. The device-level success flag
                # further down is what gates the UI red/green indicator.
                verification_passed_bool = bool(verify_ok) if verify_ok else False
                verification_sectors = int(
                    getattr(verify_result, "sectors_checked", 0) or 0
                )
                verification_hash = str(
                    getattr(verify_result, "sample_hash", "") or ""
                )

                # Reformat metadata for the cert. Agent F is adding these
                # optional fields to CertificateData; build the kwargs dict
                # and try the call once with them, falling back without them
                # if the dataclass doesn't accept them yet.
                reformat_kwargs = {
                    "reformat_performed": bool(
                        format_result and format_result.success
                    ),
                    "reformat_filesystem": (
                        format_result.filesystem
                        if format_result and format_result.success
                        else ""
                    ),
                    "reformat_label": (
                        format_result.label
                        if format_result and format_result.success
                        else ""
                    ),
                    # Bad-sector accounting (v1.2). Reported on the certificate
                    # when > 0; harmless defaults otherwise.
                    "bad_sector_count": wipe_result.bad_sector_count,
                    "bad_sector_bytes": wipe_result.bad_sector_bytes,
                    "bad_sector_offsets": list(wipe_result.bad_sector_offsets),
                    # Hidden areas (HPA/DCO) (v1.2). `hidden` is None on the demo
                    # path (no real handle to probe).
                    "hidden_area_probed": bool(hidden is not None and hidden.supported),
                    "hpa_present": bool(hidden and hidden.hpa_present),
                    "hpa_hidden_bytes": (
                        hidden_bytes(hidden.hpa_hidden_sectors)
                        if hidden and hidden.hpa_present else 0
                    ),
                    "dco_present": bool(hidden and hidden.dco_present),
                    "dco_hidden_bytes": (
                        hidden_bytes(hidden.dco_hidden_sectors)
                        if hidden and hidden.dco_present else 0
                    ),
                }

                base_kwargs = dict(
                    cert_number=cert_number,
                    date=datetime.now(),
                    operator=self.operator,
                    client_reference="",
                    asset_tag="",
                    device_model=device.model,
                    device_manufacturer="",
                    serial_number=device.serial_number,
                    capacity_bytes=device.capacity_bytes,
                    filesystem=device.filesystem,
                    connection_type=device.connection_type,
                    # "ATA Secure Erase (enhanced) + ZeroFill" after a hardware erase.
                    wipe_method=wipe_result.method_name,
                    sicherheitsstufe=self.wipe_method.sicherheitsstufe,
                    schutzklasse=self.schutzklasse,
                    passes=wipe_result.passes,
                    start_time=wipe_result.start_time,
                    end_time=wipe_result.end_time,
                    verification_passed=verification_passed_bool,
                    sectors_checked=verification_sectors,
                    verification_hash=verification_hash,
                    company_name=self.config.company.name,
                    company_address=self.config.company.address,
                    company_logo_path=self.config.company.logo_path,
                    language=self.config.cert_language,
                    # The signed payload states how verification ran and
                    # whether the wipe itself succeeded (three-level result).
                    verify_method=(
                        getattr(verify_result, "method", "none") or "none"
                        if verify_result is not None else "none"
                    ),
                    verify_bytes=int(getattr(verify_result, "bytes_verified", 0) or 0),
                    verify_pattern=str(getattr(verify_result, "expected_pattern", "") or ""),
                    verify_error_count=int(getattr(verify_result, "error_count", 0) or 0),
                    verify_mismatch_offsets=list(
                        getattr(verify_result, "mismatch_offsets", None) or []
                    ),
                    verify_duration_seconds=float(
                        getattr(verify_result, "duration_seconds", 0.0) or 0.0
                    ),
                    wipe_success=bool(wipe_result.success),
                    secure_erase_used=secure_erase_result is not None,
                    secure_erase_method=(
                        secure_erase_result.method if secure_erase_result else ""
                    ),
                )

                try:
                    cert_data = CertificateData(**base_kwargs, **reformat_kwargs)
                except TypeError:
                    # Agent F's fields aren't merged yet — fall back so we
                    # don't break the wipe pipeline on a transient ordering.
                    cert_data = CertificateData(**base_kwargs)

                # 10. Log to the history first — its chain hash goes into
                # the signed certificate (same order as the CLI).
                duration = (
                    wipe_result.end_time - wipe_result.start_time
                ).total_seconds()
                if verify_ok is True:
                    verification_text = "PASSED"
                elif verify_ok is False:
                    verification_text = "FAILED"
                else:
                    verification_text = "SKIPPED"
                history_row = {
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "device_model": device.model,
                    "serial_number": device.serial_number,
                    "capacity_bytes": device.capacity_bytes,
                    "method": wipe_result.method_name,
                    "passes": wipe_result.passes,
                    "operator": self.operator,
                    "start_time": wipe_result.start_time.isoformat(),
                    "end_time": wipe_result.end_time.isoformat(),
                    "duration_seconds": int(duration),
                    "result": history_result_value(result_status(cert_data)),
                    "verification": verification_text,
                    "cert_number": cert_number,
                    "bad_sectors": wipe_result.bad_sector_count,
                }
                try:
                    history_hash = log_wipe_to_csv(history_row)
                except (OSError, ValueError) as exc:
                    audit_log(f"History could not be written: {exc}")
                    history_hash = ""

                cert_filename = (
                    f"SS-{cert_number:06d}_"
                    f"{device.serial_number or 'unknown'}_"
                    f"{datetime.now().strftime('%Y%m%d')}.pdf"
                )
                cert_dir = self.config.cert_output_dir
                cert_path = str(Path(cert_dir) / cert_filename)
                cert_path = generate_certificate(
                    cert_data, cert_path,
                    signing_key=signing_key, history_hash=history_hash,
                )

                # Overall device success: wipe must succeed, and if verify
                # ran, it must succeed too. "Verify not run" is neutral.
                wipe_ok = bool(wipe_result.success)
                overall_ok = wipe_ok and verify_ok is not False
                success = overall_ok
                self.status_message.emit(
                    f"Device {device.friendly_name} completed "
                    f"{'successfully' if success else 'with verification errors'}."
                )
                # Stash the verify result on the worker for tests/inspectors.
                self._last_verify_result = verify_result

            except InterruptedError:
                self.status_message.emit("Wipe cancelled.")
                self.error.emit(idx, "Cancelled by user")
            except Exception as exc:
                error_msg = str(exc)
                audit_log(
                    f"Wipe error for {device.friendly_name}: {error_msg}"
                )
                self.error.emit(idx, error_msg)
                self.status_message.emit(
                    f"Error on {device.friendly_name}: {error_msg}"
                )
            finally:
                if handle is not None:
                    try:
                        unlock_volume(handle)
                    except OSError:
                        pass
                    close_drive(handle)
                locks, volume_locks = volume_locks, []
                release_volumes(locks)
                # Clean up demo temp file
                if demo_file_path is not None:
                    try:
                        os.remove(demo_file_path)
                        audit_log(f"Demo: cleaned up temp file {demo_file_path}")
                    except OSError:
                        pass

            # Stop the stall watchdog for this device.
            self._last_progress_time = None

            self.phase_changed.emit(idx, "done")
            self.device_completed.emit(idx, success, cert_path)

        self.all_completed.emit()
