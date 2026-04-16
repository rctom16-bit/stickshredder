"""QThread-based worker for running wipe operations off the main thread."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

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
from wipe.verify import verify_wipe
from wipe.demo import create_demo_file, wipe_demo_file, verify_demo_file

if TYPE_CHECKING:
    pass


class WipeWorker(QThread):
    """Executes wipe operations for one or more devices in a background thread.

    Signals
    -------
    progress_updated(device_index, pass_num, total_passes, bytes_written, total_bytes, speed_mbps)
    device_completed(device_index, success, cert_path)
    all_completed()
    error(device_index, message)
    status_message(message)
    """

    progress_updated = Signal(int, int, int, int, int, float)
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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.devices = devices
        self.wipe_method = wipe_method
        self.config = config
        self.schutzklasse = schutzklasse
        self.operator = operator or config.operator_name or "Unknown"
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
            is_demo = device.device_id.startswith(r"\\.\DemoDevice")

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

                    # Verify
                    self.status_message.emit("Demo: verifying virtual disk...")
                    from wipe.methods import ZeroFill, RandomThreePass, BsiVsitr
                    if isinstance(self.wipe_method, ZeroFill):
                        expected_pattern = b"\x00"
                    elif isinstance(self.wipe_method, (RandomThreePass, BsiVsitr)):
                        expected_pattern = b""
                    else:
                        expected_pattern = b"\x00"

                    verification = verify_demo_file(
                        demo_file_path, expected_pattern, sample_count=100,
                    )

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

                    # 5. Execute wipe with progress callback
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
                        f"Wiping {device.friendly_name} with {self.wipe_method.name}..."
                    )
                    wipe_result = self.wipe_method.execute(
                        handle, drive_size, progress_callback=_progress_cb,
                    )

                    if not wipe_result.success:
                        raise RuntimeError(
                            f"Wipe failed: {wipe_result.error_message}"
                        )

                    if self.is_cancelled:
                        raise InterruptedError("Cancelled after wipe")

                    # 6. Verify
                    self.status_message.emit(f"Verifying {device.friendly_name}...")
                    from wipe.methods import ZeroFill, RandomThreePass, BsiVsitr
                    if isinstance(self.wipe_method, ZeroFill):
                        expected_pattern = b"\x00"
                    elif isinstance(self.wipe_method, (RandomThreePass, BsiVsitr)):
                        expected_pattern = b""
                    else:
                        expected_pattern = b"\x00"

                    verification = verify_wipe(
                        handle, drive_size, expected_pattern, sample_count=100,
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
                    verification_passed=verification.passed,
                    sectors_checked=verification.sectors_checked,
                    verification_hash=verification.sample_hash,
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
                    "verification": (
                        "PASSED" if verification.passed else "FAILED"
                    ),
                    "cert_number": cert_number,
                })

                success = True
                self.status_message.emit(
                    f"Device {device.friendly_name} completed successfully."
                )

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

            self.device_completed.emit(idx, success, cert_path)

        self.all_completed.emit()
