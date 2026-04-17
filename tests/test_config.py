"""Tests for core.config — AppConfig, CompanyInfo, get_next_cert_number."""

import json
import threading
import pytest

from core.config import AppConfig, CompanyInfo, get_next_cert_number, APP_NAME, APP_VERSION


# ── AppConfig defaults ────────────────────────────────────────────────

def test_appconfig_default_values():
    cfg = AppConfig()
    assert cfg.operator_name == ""
    assert cfg.default_wipe_method == "standard"
    assert cfg.default_schutzklasse == 2
    assert cfg.cert_language == "de"
    assert cfg.show_ssd_warning is True


def test_appconfig_default_company():
    cfg = AppConfig()
    assert isinstance(cfg.company, CompanyInfo)
    assert cfg.company.name == ""
    assert cfg.company.address == ""
    assert cfg.company.logo_path == ""


# ── Save / Load roundtrip ────────────────────────────────────────────

def test_appconfig_save_load_roundtrip(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CONFIG_FILE", cfg_file)

    original = AppConfig(
        company=CompanyInfo(name="ACME GmbH", address="Berlin", logo_path="/logo.png"),
        operator_name="Max",
        default_wipe_method="bsi",
        default_schutzklasse=3,
        cert_language="en",
        show_ssd_warning=False,
    )
    original.save()

    loaded = AppConfig.load()
    assert loaded.operator_name == "Max"
    assert loaded.company.name == "ACME GmbH"
    assert loaded.default_wipe_method == "bsi"
    assert loaded.default_schutzklasse == 3
    assert loaded.cert_language == "en"
    assert loaded.show_ssd_warning is False


def test_appconfig_load_creates_default_when_missing(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CONFIG_FILE", cfg_file)

    loaded = AppConfig.load()
    assert loaded.operator_name == ""
    assert cfg_file.exists()


def test_appconfig_load_corrupted_json(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text("NOT VALID JSON {{{", encoding="utf-8")
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CONFIG_FILE", cfg_file)

    loaded = AppConfig.load()
    # Should fall back to defaults
    assert loaded.operator_name == ""
    assert loaded.default_wipe_method == "standard"


def test_appconfig_load_invalid_keys(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(json.dumps({"company": {}, "bogus_key": 999}), encoding="utf-8")
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CONFIG_FILE", cfg_file)

    # TypeError from unexpected kwarg -> falls back to defaults
    loaded = AppConfig.load()
    assert loaded.operator_name == ""


# ── CompanyInfo ───────────────────────────────────────────────────────

def test_company_info_fields():
    ci = CompanyInfo(name="Test Corp", address="123 Main St", logo_path="/img/logo.png")
    assert ci.name == "Test Corp"
    assert ci.address == "123 Main St"
    assert ci.logo_path == "/img/logo.png"


# ── get_next_cert_number ──────────────────────────────────────────────

def test_get_next_cert_number_starts_at_one(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    counter_file = cfg_dir / "cert_counter.txt"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)

    assert get_next_cert_number() == 1


def test_get_next_cert_number_increments(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    counter_file = cfg_dir / "cert_counter.txt"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)

    assert get_next_cert_number() == 1
    assert get_next_cert_number() == 2
    assert get_next_cert_number() == 3


def test_get_next_cert_number_resumes(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    counter_file = cfg_dir / "cert_counter.txt"
    counter_file.write_text("42", encoding="utf-8")
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)

    assert get_next_cert_number() == 43


# ── get_next_cert_number: hardening (TOCTOU, corruption, concurrency) ─

def test_get_next_cert_number_increments_exactly_by_one(tmp_path, monkeypatch):
    """Two sequential calls must return N and N+1 exactly."""
    cfg_dir = tmp_path / "config"
    counter_file = cfg_dir / "cert_counter.txt"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)
    monkeypatch.setattr("core.config.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    first = get_next_cert_number()
    second = get_next_cert_number()
    assert second == first + 1


def test_get_next_cert_number_handles_corrupted_file(tmp_path, monkeypatch):
    """Non-numeric counter contents reset gracefully to 0, then increment."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    counter_file = cfg_dir / "cert_counter.txt"
    counter_file.write_text("notanumber", encoding="utf-8")
    audit_log = cfg_dir / "audit.log"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)
    monkeypatch.setattr("core.config.AUDIT_LOG_FILE", audit_log)

    # Should not raise; resets to 0 then returns 1.
    assert get_next_cert_number() == 1
    # Counter is persisted back as "1".
    assert counter_file.read_text(encoding="utf-8").strip() == "1"
    # Audit log should mention the reset.
    assert audit_log.exists()
    assert "cert_counter_reset" in audit_log.read_text(encoding="utf-8")


def test_get_next_cert_number_handles_missing_file(tmp_path, monkeypatch):
    """If the counter file doesn't exist, it is created and returns 1."""
    cfg_dir = tmp_path / "config"
    counter_file = cfg_dir / "cert_counter.txt"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)
    monkeypatch.setattr("core.config.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    # Pre-condition: file really does not exist.
    assert not counter_file.exists()

    assert get_next_cert_number() == 1
    assert counter_file.exists()
    assert counter_file.read_text(encoding="utf-8").strip() == "1"


def test_get_next_cert_number_handles_oversized_file(tmp_path, monkeypatch):
    """Implausibly long counter values are treated as corruption."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    counter_file = cfg_dir / "cert_counter.txt"
    # 15 digits — well past the 10-digit guard.
    counter_file.write_text("123456789012345", encoding="utf-8")
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)
    monkeypatch.setattr("core.config.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    assert get_next_cert_number() == 1


def test_get_next_cert_number_concurrent(tmp_path, monkeypatch):
    """10 concurrent callers must each receive a unique value in [1, 10].

    Demonstrates the Windows file lock (msvcrt.locking) serializes the
    read-modify-write, eliminating the TOCTOU race where two workers
    otherwise produce duplicate cert numbers.
    """
    cfg_dir = tmp_path / "config"
    counter_file = cfg_dir / "cert_counter.txt"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)
    monkeypatch.setattr("core.config.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    results: list[int] = []
    results_lock = threading.Lock()
    start_gate = threading.Event()

    def worker() -> None:
        # All threads block on the gate so they race into the function
        # as close to simultaneously as possible.
        start_gate.wait()
        value = get_next_cert_number()
        with results_lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    start_gate.set()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "worker thread hung (possible deadlock)"

    assert len(results) == 10
    assert len(set(results)) == 10, f"duplicate cert numbers: {sorted(results)}"
    assert sorted(results) == list(range(1, 11))


# ── Constants ─────────────────────────────────────────────────────────

def test_constants():
    assert APP_NAME == "StickShredder"
    assert isinstance(APP_VERSION, str)
    assert len(APP_VERSION) > 0
