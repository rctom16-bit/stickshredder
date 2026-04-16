"""Tests for core.config — AppConfig, CompanyInfo, get_next_cert_number."""

import json
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


# ── Constants ─────────────────────────────────────────────────────────

def test_constants():
    assert APP_NAME == "StickShredder"
    assert isinstance(APP_VERSION, str)
    assert len(APP_VERSION) > 0
