"""Append-only audit log for all wipe operations."""

import csv
import io
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.config import CONFIG_DIR, AUDIT_LOG_FILE, WIPE_HISTORY_FILE

logger = logging.getLogger("stickshredder")


def setup_logging() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(CONFIG_DIR / "app.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def audit_log(message: str) -> None:
    """Append a timestamped entry to the audit log file.

    Control characters (``\\n``, ``\\r``) and the ``|`` field separator are
    escaped to prevent log-injection attacks via crafted device strings
    (serial numbers, models, etc.) that could otherwise forge additional
    log entries. Pathologically long messages are truncated to 4000 chars.
    """
    if message is None:
        message = "<none>"
    # Replace control characters that could be used to forge log entries
    safe_msg = (
        str(message)
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("|", "\\|")
    )
    # Truncate pathologically long messages
    if len(safe_msg) > 4000:
        safe_msg = safe_msg[:4000] + "...[truncated]"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat()
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} | {safe_msg}\n")
    logger.info(safe_msg)


CSV_HEADERS = [
    "date",
    "device_model",
    "serial_number",
    "capacity_bytes",
    "method",
    "passes",
    "operator",
    "start_time",
    "end_time",
    "duration_seconds",
    "result",
    "verification",
    "reformat",
    "reformat_label",
    "cert_number",
    "bad_sectors",
    "hidden_area",
    # Tamper-evident chain (v2.0): entry_hash = chain_hash(prev_hash, record)
    # where record is every other column of the row, as written to the file.
    "prev_hash",
    "entry_hash",
]

HASH_COLUMNS = ("prev_hash", "entry_hash")
RECORD_COLUMNS = [c for c in CSV_HEADERS if c not in HASH_COLUMNS]

# Serialises read-last-hash + append so two wipes finishing at the same time
# in one process cannot fork the chain.
_HISTORY_LOCK = threading.Lock()


def _cell(value) -> str:
    """A value exactly as ``csv.DictWriter`` writes it and ``DictReader`` reads it."""
    return "" if value is None else str(value)


def history_record(row: dict) -> dict:
    """The hashed part of a history row: every non-hash column, as text."""
    return {col: _cell(row.get(col)) for col in RECORD_COLUMNS}


def _read_rows() -> tuple[list[str] | None, list[dict]]:
    if not WIPE_HISTORY_FILE.exists():
        return None, []
    with open(WIPE_HISTORY_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return (list(reader.fieldnames) if reader.fieldnames else None), rows


def _migrate_header(rows: list[dict]) -> None:
    """Rewrite an older history file with the current columns.

    The original file is kept next to it as ``wipe_history.pre-2.0[-N].csv``.
    Old rows get empty hash columns (they predate the chain).
    """
    backup = WIPE_HISTORY_FILE.with_name("wipe_history.pre-2.0.csv")
    counter = 1
    while backup.exists():
        backup = WIPE_HISTORY_FILE.with_name(f"wipe_history.pre-2.0-{counter}.csv")
        counter += 1
    shutil.copy2(WIPE_HISTORY_FILE, backup)
    tmp = WIPE_HISTORY_FILE.with_name(WIPE_HISTORY_FILE.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _cell(row.get(col)) for col in CSV_HEADERS})
    os.replace(tmp, WIPE_HISTORY_FILE)


def _last_entry_hash(rows: list[dict]) -> str:
    from core.history_chain import GENESIS_HASH

    for row in reversed(rows):
        entry_hash = (row.get("entry_hash") or "").strip()
        if entry_hash:
            return entry_hash
    return GENESIS_HASH


def log_wipe_to_csv(wipe_data: dict) -> str:
    """Append a wipe to the history and return the new entry's chain hash.

    ``wipe_data`` holds the non-hash columns; missing ones are written empty
    and any ``prev_hash``/``entry_hash`` given is ignored (always recomputed).
    The returned hash goes into the signed certificate (payload field ``h``).
    """
    from core.history_chain import chain_hash

    unknown = sorted(set(wipe_data) - set(CSV_HEADERS))
    if unknown:
        raise ValueError(f"unknown history columns: {', '.join(unknown)}")

    with _HISTORY_LOCK:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        header, rows = _read_rows()
        if header is not None and header != CSV_HEADERS:
            _migrate_header(rows)
        prev_hash = _last_entry_hash(rows)
        record = history_record(wipe_data)
        entry_hash = chain_hash(prev_hash, record)
        file_exists = WIPE_HISTORY_FILE.exists() and header is not None
        with open(WIPE_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            if not file_exists:
                writer.writeheader()
            writer.writerow({**record, "prev_hash": prev_hash, "entry_hash": entry_hash})
    return entry_hash


def read_wipe_history() -> list[dict]:
    if not WIPE_HISTORY_FILE.exists():
        return []
    with open(WIPE_HISTORY_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


@dataclass
class HistoryCheck:
    """Result of :func:`verify_wipe_history`.

    ``broken_row`` is the 1-based position of the first bad entry (data rows,
    header not counted); ``legacy`` counts entries written before the chain
    existed (no hashes), which are skipped.
    """

    total: int = 0
    checked: int = 0
    legacy: int = 0
    broken_row: int | None = None
    broken_entry: dict | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.broken_row is None


def verify_wipe_history() -> HistoryCheck:
    """Check the history's hash chain; report the first changed, removed or inserted entry.

    Rows without hashes before the first hashed row are legacy entries from
    older versions and are counted, not reported. A row without hashes after
    the chain has started is reported: entries are never written that way, so
    it was inserted or its hashes were stripped.
    """
    from core.history_chain import GENESIS_HASH, chain_hash

    _header, rows = _read_rows()
    check = HistoryCheck(total=len(rows))
    prev = GENESIS_HASH
    started = False
    for index, row in enumerate(rows, start=1):
        stored_prev = (row.get("prev_hash") or "").strip().lower()
        stored_entry = (row.get("entry_hash") or "").strip().lower()

        def broken(reason: str) -> HistoryCheck:
            check.broken_row = index
            check.broken_entry = row
            check.reason = reason
            return check

        if not stored_entry and not stored_prev:
            if not started:
                check.legacy += 1
                continue
            return broken(
                "Eintrag ohne Prüfsumme nach Beginn der Kette (eingefügt oder Prüfsumme "
                "entfernt) / Entry without checksum after the chain started (inserted or "
                "checksum removed)"
            )
        started = True
        if stored_prev != prev:
            return broken(
                "Verkettung unterbrochen — ein vorheriger Eintrag fehlt oder wurde geändert / "
                "Chain broken — a previous entry is missing or was changed"
            )
        expected = chain_hash(prev, history_record(row))
        if stored_entry != expected:
            return broken("Eintrag wurde verändert / Entry was changed")
        prev = expected
        check.checked += 1
    return check
