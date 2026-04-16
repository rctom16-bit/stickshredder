"""User settings and company info management."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP_NAME = "StickShredder"
APP_VERSION = "1.0.0"
CONFIG_DIR = Path.home() / ".stickshredder"
CONFIG_FILE = CONFIG_DIR / "config.json"
AUDIT_LOG_FILE = CONFIG_DIR / "audit.log"
CERT_COUNTER_FILE = CONFIG_DIR / "cert_counter.txt"
WIPE_HISTORY_FILE = CONFIG_DIR / "wipe_history.csv"
DEFAULT_CERT_OUTPUT = CONFIG_DIR / "certificates"


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


def get_next_cert_number() -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CERT_COUNTER_FILE.exists():
        CERT_COUNTER_FILE.write_text("0", encoding="utf-8")
    current = int(CERT_COUNTER_FILE.read_text(encoding="utf-8").strip())
    next_num = current + 1
    CERT_COUNTER_FILE.write_text(str(next_num), encoding="utf-8")
    return next_num
