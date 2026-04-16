"""Tests for core.log — audit_log, log_wipe_to_csv, read_wipe_history."""

import csv
import pytest

from core.log import audit_log, log_wipe_to_csv, read_wipe_history, CSV_HEADERS


def _patch_log_paths(tmp_path, monkeypatch):
    """Redirect all log paths to tmp_path."""
    cfg_dir = tmp_path / "config"
    monkeypatch.setattr("core.log.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.log.AUDIT_LOG_FILE", cfg_dir / "audit.log")
    monkeypatch.setattr("core.log.WIPE_HISTORY_FILE", cfg_dir / "wipe_history.csv")
    return cfg_dir


# ── audit_log ─────────────────────────────────────────────────────────

def test_audit_log_creates_file(tmp_path, monkeypatch):
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    audit_log("Test message")
    log_file = cfg_dir / "audit.log"
    assert log_file.exists()


def test_audit_log_writes_timestamped_line(tmp_path, monkeypatch):
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    audit_log("Hello world")
    content = (cfg_dir / "audit.log").read_text(encoding="utf-8")
    assert "Hello world" in content
    # Timestamp format: ISO with 'T'
    assert "|" in content


def test_audit_log_appends(tmp_path, monkeypatch):
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    audit_log("First")
    audit_log("Second")
    lines = (cfg_dir / "audit.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "First" in lines[0]
    assert "Second" in lines[1]


# ── log_wipe_to_csv ──────────────────────────────────────────────────

def _sample_wipe_data(**overrides):
    data = {h: "" for h in CSV_HEADERS}
    data.update(
        date="2026-01-15",
        device_model="TestDisk",
        serial_number="SN123",
        capacity_bytes="32000000000",
        method="ZeroFill",
        passes="1",
        operator="Max",
        result="SUCCESS",
    )
    data.update(overrides)
    return data


def test_log_wipe_to_csv_creates_file_with_headers(tmp_path, monkeypatch):
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    log_wipe_to_csv(_sample_wipe_data())
    csv_file = cfg_dir / "wipe_history.csv"
    assert csv_file.exists()
    first_line = csv_file.read_text(encoding="utf-8").splitlines()[0]
    for header in CSV_HEADERS:
        assert header in first_line


def test_log_wipe_to_csv_appends_rows(tmp_path, monkeypatch):
    _patch_log_paths(tmp_path, monkeypatch)
    log_wipe_to_csv(_sample_wipe_data(serial_number="AAA"))
    log_wipe_to_csv(_sample_wipe_data(serial_number="BBB"))
    history = read_wipe_history()
    assert len(history) == 2
    assert history[0]["serial_number"] == "AAA"
    assert history[1]["serial_number"] == "BBB"


# ── read_wipe_history ────────────────────────────────────────────────

def test_read_wipe_history_roundtrip(tmp_path, monkeypatch):
    _patch_log_paths(tmp_path, monkeypatch)
    log_wipe_to_csv(_sample_wipe_data(device_model="SanDisk"))
    rows = read_wipe_history()
    assert len(rows) == 1
    assert rows[0]["device_model"] == "SanDisk"
    assert rows[0]["method"] == "ZeroFill"


def test_read_wipe_history_missing_file(tmp_path, monkeypatch):
    _patch_log_paths(tmp_path, monkeypatch)
    assert read_wipe_history() == []


# ── CSV_HEADERS ──────────────────────────────────────────────────────

def test_csv_headers_content():
    assert "date" in CSV_HEADERS
    assert "serial_number" in CSV_HEADERS
    assert "result" in CSV_HEADERS
    assert len(CSV_HEADERS) >= 10
