"""User settings and company info management."""

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path

try:
    import msvcrt  # Windows-only file locking primitive
except ImportError:  # pragma: no cover - non-Windows fallback
    msvcrt = None  # type: ignore[assignment]

# In-process mutex for the cert counter. The msvcrt file lock guards against
# cross-process races, but Windows cannot serialise two threads from the SAME
# process attempting to open/lock the same file — the second open() raises
# PermissionError before the lock can be acquired. This threading.Lock gates
# the whole open-lock-read-write-unlock-close cycle per-process.
_CERT_COUNTER_LOCK = threading.Lock()

APP_NAME = "StickShredder"
APP_VERSION = "1.0.0"
CONFIG_DIR = Path.home() / ".stickshredder"
CONFIG_FILE = CONFIG_DIR / "config.json"
AUDIT_LOG_FILE = CONFIG_DIR / "audit.log"
CERT_COUNTER_FILE = CONFIG_DIR / "cert_counter.txt"
WIPE_HISTORY_FILE = CONFIG_DIR / "wipe_history.csv"
DEFAULT_CERT_OUTPUT = CONFIG_DIR / "certificates"

# Cert counter is stored as a decimal integer. Anything beyond 10 digits is
# treated as corruption (10 digits covers values up to ~9.9 billion, which is
# far beyond any legitimate wipe volume) and will be reset to 0.
_CERT_COUNTER_MAX_DIGITS = 10


@dataclass
class CompanyInfo:
    name: str = ""
    address: str = ""
    logo_path: str = ""


@dataclass
class AppConfig:
    company: CompanyInfo = field(default_factory=CompanyInfo)
    operator_name: str = ""
    default_wipe_method: str = "standard"
    default_schutzklasse: int = 2
    cert_output_dir: str = str(DEFAULT_CERT_OUTPUT)
    cert_language: str = "de"
    show_ssd_warning: bool = True

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_FILE.exists():
            config = cls()
            config.save()
            return config
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            company_data = data.pop("company", {})
            company = CompanyInfo(**company_data)
            return cls(company=company, **data)
        except (json.JSONDecodeError, TypeError):
            return cls()


def _audit_counter_reset(reason: str) -> None:
    """Best-effort audit log entry when the cert counter is reset.

    Kept local to avoid a hard dependency on core.log (which imports this
    module). Silently swallows filesystem errors so the counter path
    never raises on audit failure.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        stamp = datetime.now().isoformat(timespec="seconds")
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as log:
            log.write(f"[{stamp}] cert_counter_reset: {reason}\n")
    except OSError:
        pass


def get_next_cert_number() -> int:
    """Atomically read, increment, and persist the cert counter.

    Uses a Windows file lock (``msvcrt.locking``) around the
    read-modify-write so two concurrent wipe workers cannot produce the
    same cert number. If the counter file is missing or corrupted
    (non-numeric, empty, or implausibly large), it is transparently
    reset to 0 and an audit log entry is written; the caller still
    receives the next number (i.e. 1).
    """
    CERT_COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)

    with _CERT_COUNTER_LOCK:
        # Ensure the file exists INSIDE the lock so concurrent threads don't
        # race on its creation and then trip PermissionError when another
        # thread already holds the file open in r+ mode.
        if not CERT_COUNTER_FILE.exists():
            CERT_COUNTER_FILE.write_text("0", encoding="utf-8")

        with open(CERT_COUNTER_FILE, "r+", encoding="utf-8") as f:
            locked = False
            if msvcrt is not None:
                try:
                    # LK_LOCK blocks until the region can be locked. Cross-
                    # process safety; in-process concurrency is already
                    # handled by _CERT_COUNTER_LOCK above.
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    locked = True
                except OSError:
                    # Could not acquire the lock (disk error, etc). Fall
                    # through without the lock rather than crashing the wipe.
                    locked = False

            try:
                raw = f.read().strip()
                current = 0
                if not raw:
                    _audit_counter_reset("empty counter file")
                elif len(raw) > _CERT_COUNTER_MAX_DIGITS:
                    _audit_counter_reset(f"counter value too long ({len(raw)} chars)")
                else:
                    try:
                        parsed = int(raw)
                        if parsed < 0:
                            _audit_counter_reset(f"negative counter value: {parsed}")
                        else:
                            current = parsed
                    except ValueError:
                        _audit_counter_reset(f"non-numeric counter contents: {raw!r}")

                next_num = current + 1
                f.seek(0)
                f.truncate()
                f.write(str(next_num))
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # fsync can fail on some filesystems; the write itself is
                    # still durable enough for our audit trail purposes.
                    pass
                return next_num
            finally:
                if locked and msvcrt is not None:
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
