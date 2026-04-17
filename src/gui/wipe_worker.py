"""QThread-based worker for running wipe operations off the main thread."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal

from core.config import AppConfig, get_next_cert_number
from core.log import audit_log, log_wipe_to_csv
from cert.generator import CertificateData, generate_certificate, format_capacity
from wipe.device import (
    DeviceInfo,
    dismount_volume,
    open_physical_drive,
    close_drive,
    get_drive_size,
    lock_volume,
    unlock_volume,
)
from wipe.methods import WipeMethod, WipeResult
from wipe.demo import create_demo_file, wipe_demo_file, verify_demo_file

if TYPE_CHECKING:
    pass


# Accepted values for the verify_mode parameter passed to WipeMethod.execute().
VERIFY_MODES = ("none", "sample", "full")


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


def _verify_ok(verify_result: Any) -> bool | None:
    """Normalize the success flag across VerifyResult and its legacy alias.

    Returns True/False when verification ran, or None if no verification
    result is available (verify_mode="none" or verify raised internally).
    """
    if verify_result is None:
        return None
    # New field name used by wipe.verify.VerifyResult.
    if hasattr(verify_result, "success"):
        return bool(verify_result.success)
    # Legacy field name still used by some demo paths.
    if hasattr(verify_result, "passed"):
        return bool(verify_result.passed)
    return None


class WipeWorker(QThread):
    """Executes wipe operations for one or more devices in a background thread.

    Signals
    -------
    progress_updated(device_index, pass_num, total_passes, bytes_written, total_bytes, speed_mbps)
        Emitted during the overwrite phase.
    verify_progress(device_index, fraction, bytes_done, total_bytes, speed_mbps)
        Emitted during full-verification reads. Fraction is 0.0..1.0.
    phase_changed(device_index, phase)
        phase is one of "wiping", "verifying", "done".
    device_completed(device_index, success, cert_path)
    all_completed()
    error(device_index, message)
    status_message(message)
    """

    progress_updated = Signal(int, int, int, int, int, float)
    verify_progress = Signal(int, float, int, int, float)
    phase_changed = Signal(int, str)
    device_completed = Signal(int, bool, str)
    all_completed = Signal()
    error = Signal(int, str)
    status_message = Signal(str)

    def __init__(
        self,
        devices: list[DeviceInfo],
        wipe_method: WipeMethod,
        config: AppConfig,
        schutzklasse: int = 2,
        operator: str = "",
        verify_mode: str = "sample",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.devices = devices
        self.wipe_method = wipe_method
        self.config = config
        self.schutzklasse = schutzklasse
        self.operator = operator or config.operator_name or "Unknown"
        if verify_mode not in VERIFY_MODES:
            audit_log(
                f"WipeWorker: unknown verify_mode={verify_mode!r}, "
                f"falling back to 'sample'"
            )
            verify_mode = "sample"
        self.verify_mode = verify_mode
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation. The worker checks this between operations."""
        self._cancel_event.set()
        audit_log("Wipe cancellation requested by user")

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> None:  # noqa: C901
        """Main worker loop — processes each device sequentially."""
        for idx, device in enumerate(self.devices):
            if self.is_cancelled:
                self.status_message.emit("Wipe cancelled by user.")
                break

            self.status_message.emit(
                f"Processing device {idx + 1}/{len(self.devices)}: "
                f"{device.friendly_name}"
            )

            handle: int | None = None
            demo_file_path: str | None = None
            cert_path = ""
            success = False
            verify_result: Any = None
            is_demo = device.device_id.startswith(r"\\.\DemoDevice")

            # Signal that this device is entering the wipe phase.
            self.phase_changed.emit(idx, "wiping")

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
                        self.progress_updated.emit(
                            _idx, pass_num, total_passes,
                            bytes_written, total_bytes, speed,
                        )

                    self.status_message.emit(
                        f"Demo: wiping virtual disk with {self.wipe_method.name}..."
                    )
                    wipe_result: WipeResult = wipe_demo_file(
                        demo_file_path, self.wipe_method, progress_callback=_progress_cb,
                    )

                    if not wipe_result.success:
                        raise RuntimeError(
                            f"Demo wipe failed: {wipe_result.error_message}"
                        )

                    if self.is_cancelled:
                        raise InterruptedError("Cancelled after wipe")

                    # Demo verification (out-of-band; the demo wipe path does
                    # not yet accept verify_mode through the method). Tolerant
                    # of either VerifyResult (success=) or legacy
                    # VerificationResult (passed=) via _verify_ok.
                    if self.verify_mode != "none":
                        self.phase_changed.emit(idx, "verifying")
                        self.status_message.emit("Demo: verifying virtual disk...")
                        expected_pattern = _expected_pattern_for(self.wipe_method)
                        try:
                            verify_result = verify_demo_file(
                                demo_file_path, expected_pattern, sample_count=100,
                            )
                        except Exception as exc:  # noqa: BLE001
                            audit_log(f"Demo verification failed to run: {exc}")
                            verify_result = None

                else:
                    # ── Real device mode ──
                    # 1. Dismount volume
                    self.status_message.emit(f"Dismounting {device.drive_letter}...")
                    try:
                        dismount_volume(device.drive_letter)
                    except OSError as exc:
                        audit_log(f"Dismount warning for {device.drive_letter}: {exc}")

                    # 2. Open physical drive
                    self.status_message.emit(f"Opening {device.device_id}...")
                    handle = open_physical_drive(device.device_id)

                    # 3. Lock volume
                    self.status_message.emit(f"Locking {device.drive_letter}...")
                    try:
                        lock_volume(handle)
                    except OSError as exc:
                        audit_log(f"Lock warning for {device.drive_letter}: {exc}")

                    # 4. Get drive size
                    drive_size = get_drive_size(handle)
                    self.status_message.emit(
                        f"Drive size: {format_capacity(drive_size)}"
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
                        if not verifying_announced["value"]:
                            verifying_announced["value"] = True
                            self.phase_changed.emit(_idx, "verifying")
                            self.status_message.emit(
                                f"Verifying {device.friendly_name}..."
                            )
                        self.verify_progress.emit(
                            _idx, fraction, bytes_done, total_bytes, speed,
                        )

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

                cert_data = CertificateData(
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
                    wipe_method=self.wipe_method.name,
                    sicherheitsstufe=self.wipe_method.sicherheitsstufe,
                    schutzklasse=self.schutzklasse,
                    passes=self.wipe_method.passes,
                    start_time=wipe_result.start_time,
                    end_time=wipe_result.end_time,
                    verification_passed=verification_passed_bool,
                    sectors_checked=verification_sectors,
                    verification_hash=verification_hash,
                    company_name=self.config.company.name,
                    company_address=self.config.company.address,
                    company_logo_path=self.config.company.logo_path,
                    language=self.config.cert_language,
                )

                cert_filename = (
                    f"SS-{cert_number:06d}_"
                    f"{device.serial_number or 'unknown'}_"
                    f"{datetime.now().strftime('%Y%m%d')}.pdf"
                )
                cert_dir = self.config.cert_output_dir
                cert_path = str(
                    __import__("pathlib").Path(cert_dir) / cert_filename
                )
                cert_path = generate_certificate(cert_data, cert_path)

                # 10. Log to CSV
                duration = (
                    wipe_result.end_time - wipe_result.start_time
                ).total_seconds()
                if verify_ok is True:
                    verification_text = "PASSED"
                elif verify_ok is False:
                    verification_text = "FAILED"
                else:
                    verification_text = "SKIPPED"
                log_wipe_to_csv({
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "device_model": device.model,
                    "serial_number": device.serial_number,
                    "capacity_bytes": device.capacity_bytes,
                    "method": self.wipe_method.name,
                    "passes": self.wipe_method.passes,
                    "operator": self.operator,
                    "start_time": wipe_result.start_time.isoformat(),
                    "end_time": wipe_result.end_time.isoformat(),
                    "duration_seconds": int(duration),
                    "result": "SUCCESS" if wipe_result.success else "FAILED",
                    "verification": verification_text,
                    "cert_number": cert_number,
                })

                # Treat "verify ran and failed" as an unsuccessful device.
                # "Verify not run" or "verify passed" both count as success.
                success = verify_ok is not False
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
                # Clean up demo temp file
                if demo_file_path is not None:
                    try:
                        import os as _os
                        _os.remove(demo_file_path)
                        audit_log(f"Demo: cleaned up temp file {demo_file_path}")
                    except OSError:
                        pass

            self.phase_changed.emit(idx, "done")
            self.device_completed.emit(idx, success, cert_path)

        self.all_completed.emit()
