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


# ── audit_log injection hardening ────────────────────────────────────

def test_audit_log_escapes_newlines(tmp_path, monkeypatch):
    """A message containing \\n must not produce a second physical line —
    attackers could otherwise forge additional audit entries by stuffing
    newlines into a device serial/model string."""
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    audit_log("line1\nline2")
    content = (cfg_dir / "audit.log").read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert len(lines) == 1, f"Expected single line, got {len(lines)}: {lines!r}"
    # Literal backslash-n must be present; real newline inside the entry must not.
    assert "line1\\nline2" in lines[0]
    assert "line1\nline2" not in lines[0]


def test_audit_log_escapes_carriage_returns(tmp_path, monkeypatch):
    """\\r can be used on Windows tooling to visually overwrite entries."""
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    audit_log("foo\rbar")
    content = (cfg_dir / "audit.log").read_text(encoding="utf-8")
    # The \r we wrote must now appear as the literal escape "\r" (backslash + r).
    assert "foo\\rbar" in content


def test_audit_log_escapes_pipes(tmp_path, monkeypatch):
    """``|`` is the field separator — an unescaped pipe could forge a
    fake timestamp/message split in log parsers."""
    import re
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    audit_log("foo|bar")
    content = (cfg_dir / "audit.log").read_text(encoding="utf-8")
    # The only legitimate pipe on the line is the timestamp separator.
    # The injected one must appear escaped as \| .
    assert "foo\\|bar" in content
    line = content.strip().splitlines()[0]
    # Count bare pipes not preceded by a backslash.
    bare_pipes = re.findall(r"(?<!\\)\|", line)
    assert len(bare_pipes) == 1, (
        f"Expected 1 separator pipe, got {len(bare_pipes)} in {line!r}"
    )


def test_audit_log_truncates_long_messages(tmp_path, monkeypatch):
    """Messages longer than 4000 chars get truncated with a marker so a
    rogue device can't bloat the audit log arbitrarily."""
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    audit_log("A" * 10000)
    line = (cfg_dir / "audit.log").read_text(encoding="utf-8").strip().splitlines()[0]
    assert len(line) <= 4100, f"Line too long: {len(line)} chars"
    assert line.endswith("[truncated]")


def test_audit_log_handles_none(tmp_path, monkeypatch):
    """Passing None must not crash the logger."""
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    audit_log(None)  # should not raise
    content = (cfg_dir / "audit.log").read_text(encoding="utf-8")
    # Either the "<none>" sentinel or the string "None" is acceptable.
    assert "<none>" in content or "None" in content


def test_audit_log_combined_injection_attempt(tmp_path, monkeypatch):
    """Realistic crafted serial that tries to forge a whole extra entry."""
    import re
    cfg_dir = _patch_log_paths(tmp_path, monkeypatch)
    crafted = "ABC\n2024-01-01 12:00:00 | WIPE SUCCEEDED | cert=999"
    audit_log(f"Wipe started for device serial={crafted}")
    lines = (cfg_dir / "audit.log").read_text(encoding="utf-8").strip().splitlines()
    # The entire crafted attack must collapse into a single audit line.
    assert len(lines) == 1
    assert "WIPE SUCCEEDED" in lines[0]  # present, but…
    # …the attacker's fake pipe separators must all be escaped.
    bare_pipes = re.findall(r"(?<!\\)\|", lines[0])
    assert len(bare_pipes) == 1


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
