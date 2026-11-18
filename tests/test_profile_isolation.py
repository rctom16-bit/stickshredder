"""Unit tests never write into the real %USERPROFILE%\\.stickshredder."""

from pathlib import Path

import core.config as config
import core.log as log

REAL_DIR = Path.home() / ".stickshredder"


def _outside_real_profile(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(REAL_DIR.resolve())
    except ValueError:
        return True
    return False


def test_config_paths_point_into_tmp():
    for name in ("CONFIG_DIR", "CONFIG_FILE", "AUDIT_LOG_FILE", "CERT_COUNTER_FILE",
                 "WIPE_HISTORY_FILE", "DEFAULT_CERT_OUTPUT", "KEY_DIR", "TRUST_FILE"):
        assert _outside_real_profile(getattr(config, name)), name


def test_log_module_copies_point_into_tmp():
    for name in ("CONFIG_DIR", "AUDIT_LOG_FILE", "WIPE_HISTORY_FILE"):
        assert _outside_real_profile(getattr(log, name)), name


def test_cli_default_cert_output_points_into_tmp():
    import cli

    assert _outside_real_profile(cli.DEFAULT_CERT_OUTPUT)


def test_audit_log_lands_in_tmp(tmp_path):
    log.audit_log("isolation probe")
    assert log.AUDIT_LOG_FILE.read_text(encoding="utf-8").endswith("isolation probe\n")
    assert tmp_path in log.AUDIT_LOG_FILE.parents
