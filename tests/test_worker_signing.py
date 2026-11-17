"""GUI wipe worker passes the signing key and history hash like the CLI."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6")


@pytest.fixture()
def app_config(tmp_path):
    from core.config import AppConfig

    cfg = AppConfig()
    cfg.cert_output_dir = str(tmp_path / "certs")
    return cfg


@pytest.fixture()
def demo_device():
    from wipe.demo import create_demo_device

    return create_demo_device()


def _run_worker(qtbot, app_config, demo_device, *, verify_ok=True, extra=()):
    from gui.wipe_worker import WipeWorker
    from wipe.methods import WipeResult, ZeroFill
    from wipe.verify import VerifyResult

    calls: list[str] = []
    captured: dict = {}

    def fake_log(row):
        calls.append("log")
        captured["row"] = row
        return "ef" * 32

    def fake_generate(data, path, signing_key=None, history_hash=""):
        calls.append("cert")
        captured.update(data=data, key=signing_key, history_hash=history_hash)
        return path

    errors: list[tuple[int, str]] = []
    with patch("gui.wipe_worker.create_demo_file", return_value="fake.bin"), \
         patch("gui.wipe_worker.wipe_demo_file") as mock_wipe, \
         patch("gui.wipe_worker.verify_demo_file") as mock_verify, \
         patch("gui.wipe_worker.generate_certificate", side_effect=fake_generate), \
         patch("gui.wipe_worker.log_wipe_to_csv", side_effect=fake_log), \
         patch("gui.wipe_worker.get_next_cert_number", return_value=5), \
         patch("gui.wipe_worker.audit_log"), \
         patch("os.remove"):
        for p in extra:
            p.start()
        try:
            verify = VerifyResult(
                success=verify_ok, method="full", bytes_verified=1024,
                expected_pattern="zeros", error_count=0 if verify_ok else 3,
                mismatch_offsets=[] if verify_ok else [0, 512, 1024],
                duration_seconds=0.01,
            )
            # The demo path takes the verification from the wipe result.
            mock_wipe.return_value = WipeResult(
                method_name="ZeroFill", passes=1, start_time=datetime.now(),
                end_time=datetime.now(), bytes_written=1024, success=True,
                error_message=None, verify_result=verify,
            )
            mock_verify.return_value = verify
            worker = WipeWorker(
                devices=[demo_device], wipe_method=ZeroFill(), config=app_config,
                verify_mode="full",
            )
            worker.error.connect(lambda idx, msg: errors.append((idx, msg)))
            with qtbot.waitSignal(worker.all_completed, timeout=10000):
                worker.start()
        finally:
            for p in reversed(extra):
                p.stop()
    return calls, captured, errors


def test_worker_signs_certificate_with_history_hash(qtbot, app_config, demo_device):
    import core.config as config
    from cert.signing import SigningKey

    calls, captured, errors = _run_worker(qtbot, app_config, demo_device)

    assert errors == []
    assert calls == ["log", "cert"]
    assert isinstance(captured["key"], SigningKey)
    assert captured["key"].fingerprint() == config.load_signing_key().fingerprint()
    assert captured["history_hash"] == "ef" * 32


def test_worker_records_real_verification_details(qtbot, app_config, demo_device):
    _, captured, _ = _run_worker(qtbot, app_config, demo_device, verify_ok=False)
    data = captured["data"]
    assert data.verify_method == "full"
    assert data.verify_error_count == 3
    assert data.wipe_success is True
    from cert.payload import result_status

    assert result_status(data) == "failed"


def test_worker_refuses_to_wipe_when_key_unreadable(qtbot, app_config, demo_device):
    from cert.signing import SigningKeyError

    broken = patch("gui.wipe_worker.load_signing_key", side_effect=SigningKeyError("kaputt"))
    calls, _, errors = _run_worker(qtbot, app_config, demo_device, extra=[broken])
    assert calls == []
    assert len(errors) == 1 and "kaputt" in errors[0][1]
