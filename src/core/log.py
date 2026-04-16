"""Append-only audit log for all wipe operations."""

import csv
import io
import logging
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
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat()
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} | {message}\n")
    logger.info(message)


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
    "cert_number",
]


def log_wipe_to_csv(wipe_data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    file_exists = WIPE_HISTORY_FILE.exists()
    with open(WIPE_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(wipe_data)


def read_wipe_history() -> list[dict]:
    if not WIPE_HISTORY_FILE.exists():
        return []
    with open(WIPE_HISTORY_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)
