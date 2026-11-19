"""Tamper-evident wipe history in core.log (hash chain over the CSV rows)."""

from __future__ import annotations

import csv
import threading

import pytest

import core.log as log
from core.history_chain import chain_hash
from core.log import (
    CSV_HEADERS,
    log_wipe_to_csv,
    read_wipe_history,
    verify_wipe_history,
)

# Header of the 1.1.1 / PR #1 history file (no hash columns).
LEGACY_HEADERS = [
    "date", "device_model", "serial_number", "capacity_bytes", "method", "passes",
    "operator", "start_time", "end_time", "duration_seconds", "result",
    "verification", "reformat", "reformat_label", "cert_number", "bad_sectors",
    "hidden_area",
]


@pytest.fixture()
def history_file(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".stickshredder"
    monkeypatch.setattr(log, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(log, "AUDIT_LOG_FILE", cfg_dir / "audit.log")
    monkeypatch.setattr(log, "WIPE_HISTORY_FILE", cfg_dir / "wipe_history.csv")
    return cfg_dir / "wipe_history.csv"


def _row(**overrides) -> dict:
    row = {
        "date": "2026-11-17 10:00:00",
        "device_model": "SanDisk",
        "serial_number": "SN1",
        "capacity_bytes": 1024,
        "method": "ZeroFill",
        "passes": 1,
        "operator": "Max",
        "start_time": "2026-11-17 09:00:00",
        "end_time": "2026-11-17 09:50:00",
        "duration_seconds": 3000,
        "result": "SUCCESS",
        "verification": "FULL-PASSED",
        "cert_number": 1,
    }
    row.update(overrides)
    return row


def _rewrite(path, rows, headers):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _write_legacy(path, count):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {h: "" for h in LEGACY_HEADERS} | {"serial_number": f"OLD{i}", "cert_number": str(i)}
        for i in range(1, count + 1)
    ]
    _rewrite(path, rows, LEGACY_HEADERS)


# ── writing ──────────────────────────────────────────────────────────

def test_headers_end_with_hash_columns():
    assert CSV_HEADERS[-2:] == ["prev_hash", "entry_hash"]


def test_first_entry_chains_from_empty_hash(history_file):
    entry_hash = log_wipe_to_csv(_row())
    rows = read_wipe_history()
    assert rows[0]["prev_hash"] == ""
    assert rows[0]["entry_hash"] == entry_hash
    record = {k: v for k, v in rows[0].items() if k not in ("prev_hash", "entry_hash")}
    assert entry_hash == chain_hash("", record)


def test_entries_link_to_their_predecessor(history_file):
    first = log_wipe_to_csv(_row(cert_number=1))
    second = log_wipe_to_csv(_row(cert_number=2))
    rows = read_wipe_history()
    assert rows[1]["prev_hash"] == first
    assert rows[1]["entry_hash"] == second
    assert first != second


def test_hash_covers_values_as_written_to_the_file(history_file):
    """Ints, None and missing columns hash exactly as they read back."""
    entry_hash = log_wipe_to_csv(_row(passes=3, reformat=None))
    row = read_wipe_history()[0]
    assert row["passes"] == "3" and row["reformat"] == ""
    record = {k: v for k, v in row.items() if k not in ("prev_hash", "entry_hash")}
    assert chain_hash("", record) == entry_hash


def test_unknown_column_is_rejected(history_file):
    with pytest.raises(ValueError):
        log_wipe_to_csv(_row(bogus="x"))


def test_hash_columns_in_input_are_ignored_and_recomputed(history_file):
    entry_hash = log_wipe_to_csv(_row(prev_hash="1" * 64, entry_hash="0" * 64))
    row = read_wipe_history()[0]
    assert row["prev_hash"] == ""
    assert row["entry_hash"] == entry_hash != "0" * 64


def test_legacy_file_is_migrated_with_backup(history_file):
    _write_legacy(history_file, 2)
    original = history_file.read_bytes()

    entry_hash = log_wipe_to_csv(_row(cert_number=3))

    backups = list(history_file.parent.glob("wipe_history.pre-2.0*.csv"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    with open(history_file, newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == CSV_HEADERS
    rows = read_wipe_history()
    assert [r["serial_number"] for r in rows] == ["OLD1", "OLD2", "SN1"]
    assert rows[0]["entry_hash"] == "" and rows[1]["entry_hash"] == ""
    assert rows[2]["prev_hash"] == "" and rows[2]["entry_hash"] == entry_hash


def test_concurrent_writers_keep_one_chain(history_file):
    threads = [
        threading.Thread(target=log_wipe_to_csv, args=(_row(cert_number=i),))
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check = verify_wipe_history()
    assert check.ok and check.checked == 8


# ── verifying ────────────────────────────────────────────────────────

def test_verify_empty_history(history_file):
    check = verify_wipe_history()
    assert check.ok and check.total == 0 and check.broken_row is None


def test_verify_intact_chain(history_file):
    for i in range(3):
        log_wipe_to_csv(_row(cert_number=i))
    check = verify_wipe_history()
    assert check.ok
    assert (check.total, check.checked, check.legacy) == (3, 3, 0)
    assert check.broken_row is None


def test_verify_detects_edited_entry(history_file):
    for i in range(1, 4):
        log_wipe_to_csv(_row(cert_number=i))
    rows = read_wipe_history()
    rows[1]["operator"] = "Mallory"
    _rewrite(history_file, rows, CSV_HEADERS)

    check = verify_wipe_history()
    assert not check.ok
    assert check.broken_row == 2
    assert check.broken_entry["cert_number"] == "2"
    assert check.reason


def test_verify_detects_deleted_entry(history_file):
    for i in range(1, 4):
        log_wipe_to_csv(_row(cert_number=i))
    rows = read_wipe_history()
    del rows[1]
    _rewrite(history_file, rows, CSV_HEADERS)

    check = verify_wipe_history()
    assert not check.ok
    assert check.broken_row == 2
    assert check.broken_entry["cert_number"] == "3"


def test_verify_detects_edited_prev_hash_column(history_file):
    for i in range(1, 3):
        log_wipe_to_csv(_row(cert_number=i))
    rows = read_wipe_history()
    rows[1]["prev_hash"] = "f" * 64
    _rewrite(history_file, rows, CSV_HEADERS)
    assert verify_wipe_history().broken_row == 2


def test_verify_skips_and_counts_legacy_rows(history_file):
    _write_legacy(history_file, 2)
    log_wipe_to_csv(_row(cert_number=3))
    log_wipe_to_csv(_row(cert_number=4))

    check = verify_wipe_history()
    assert check.ok
    assert (check.total, check.legacy, check.checked) == (4, 2, 2)


def test_verify_only_legacy_rows_is_ok(history_file):
    _write_legacy(history_file, 3)
    check = verify_wipe_history()
    assert check.ok and check.legacy == 3 and check.checked == 0


def test_verify_reports_entry_whose_hashes_were_removed(history_file):
    """A row without hashes after the chain started was inserted or stripped."""
    for i in range(1, 4):
        log_wipe_to_csv(_row(cert_number=i))
    rows = read_wipe_history()
    rows[2]["prev_hash"] = ""
    rows[2]["entry_hash"] = ""
    rows[2]["operator"] = "Mallory"
    _rewrite(history_file, rows, CSV_HEADERS)

    check = verify_wipe_history()
    assert not check.ok
    assert check.broken_row == 3
