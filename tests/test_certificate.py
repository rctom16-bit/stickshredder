"""Tests for cert.generator — format_capacity, format_duration, generate_certificate."""

import ctypes
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock ctypes.windll before any imports that touch wipe.*
if not hasattr(ctypes, "windll"):
    ctypes.windll = MagicMock()
ctypes.windll.kernel32 = MagicMock()
sys.modules.setdefault("wmi", MagicMock())

from cert.generator import format_capacity, format_duration, generate_certificate, CertificateData


# ── format_capacity ──────────────────────────────────────────────────

def test_format_capacity_bytes():
    assert format_capacity(500) == "500.00 B"


def test_format_capacity_kb():
    assert format_capacity(2048) == "2.00 KB"


def test_format_capacity_mb():
    assert format_capacity(10 * 1024**2) == "10.00 MB"


def test_format_capacity_gb():
    assert format_capacity(32 * 1024**3) == "32.00 GB"


def test_format_capacity_tb():
    assert format_capacity(2 * 1024**4) == "2.00 TB"


def test_format_capacity_zero():
    assert format_capacity(0) == "0.00 B"


def test_format_capacity_negative_raises():
    with pytest.raises(ValueError):
        format_capacity(-1)


# ── format_duration ──────────────────────────────────────────────────

def test_format_duration_basic():
    start = datetime(2026, 1, 1, 10, 0, 0)
    end = datetime(2026, 1, 1, 11, 30, 45)
    assert format_duration(start, end) == "01:30:45"


def test_format_duration_zero():
    t = datetime(2026, 1, 1, 12, 0, 0)
    assert format_duration(t, t) == "00:00:00"


def test_format_duration_negative():
    start = datetime(2026, 1, 1, 12, 0, 0)
    end = datetime(2026, 1, 1, 11, 0, 0)
    assert format_duration(start, end) == "00:00:00"


def test_format_duration_large():
    start = datetime(2026, 1, 1, 0, 0, 0)
    end = datetime(2026, 1, 2, 2, 3, 4)  # 26h 3m 4s
    assert format_duration(start, end) == "26:03:04"


# ── generate_certificate ────────────────────────────────────────────

def _make_cert_data(**overrides) -> CertificateData:
    defaults = dict(
        cert_number=1,
        date=datetime(2026, 4, 15, 14, 30, 0),
        operator="Max Mustermann",
        client_reference="",
        asset_tag="",
        device_model="SanDisk Ultra",
        device_manufacturer="SanDisk",
        serial_number="ABC123DEF",
        capacity_bytes=32 * 1024**3,
        filesystem="FAT32",
        connection_type="USB",
        wipe_method="ZeroFill",
        sicherheitsstufe="H-2",
        schutzklasse=2,
        passes=1,
        start_time=datetime(2026, 4, 15, 14, 0, 0),
        end_time=datetime(2026, 4, 15, 14, 25, 0),
        verification_passed=True,
        sectors_checked=100,
        verification_hash="a" * 64,
        company_name="Test GmbH",
        company_address="Teststr. 1\n12345 Berlin",
        company_logo_path="",
        language="de",
    )
    defaults.update(overrides)
    return CertificateData(**defaults)


@patch("cert.generator.audit_log")
def test_generate_certificate_creates_pdf(mock_log, tmp_path):
    out = str(tmp_path / "cert.pdf")
    data = _make_cert_data()
    result_path = generate_certificate(data, out)
    p = Path(result_path)
    assert p.exists()
    assert p.stat().st_size > 0
    # PDF magic bytes
    header = p.read_bytes()[:5]
    assert header == b"%PDF-"


@patch("cert.generator.audit_log")
def test_generate_certificate_german(mock_log, tmp_path):
    out = str(tmp_path / "cert_de.pdf")
    data = _make_cert_data(language="de")
    result_path = generate_certificate(data, out)
    assert Path(result_path).exists()


@patch("cert.generator.audit_log")
def test_generate_certificate_english(mock_log, tmp_path):
    out = str(tmp_path / "cert_en.pdf")
    data = _make_cert_data(language="en")
    result_path = generate_certificate(data, out)
    assert Path(result_path).exists()


@patch("cert.generator.audit_log")
def test_generate_certificate_both_languages(mock_log, tmp_path):
    out = str(tmp_path / "cert_both.pdf")
    data = _make_cert_data(language="both")
    result_path = generate_certificate(data, out)
    assert Path(result_path).exists()


@patch("cert.generator.audit_log")
def test_generate_certificate_with_logo(mock_log, tmp_path):
    # Create a minimal valid 1x1 red PNG
    png_path = tmp_path / "logo.png"
    _write_tiny_png(png_path)

    out = str(tmp_path / "cert_logo.pdf")
    data = _make_cert_data(company_logo_path=str(png_path))
    result_path = generate_certificate(data, out)
    assert Path(result_path).exists()
    assert Path(result_path).stat().st_size > 0


@patch("cert.generator.audit_log")
def test_generate_certificate_missing_logo_ignored(mock_log, tmp_path):
    out = str(tmp_path / "cert_nolog.pdf")
    data = _make_cert_data(company_logo_path="/nonexistent/logo.png")
    # Should not raise — logo is optional
    result_path = generate_certificate(data, out)
    assert Path(result_path).exists()


@patch("cert.generator.audit_log")
def test_generate_certificate_subdirectory_created(mock_log, tmp_path):
    out = str(tmp_path / "deep" / "nested" / "cert.pdf")
    data = _make_cert_data()
    result_path = generate_certificate(data, out)
    assert Path(result_path).exists()


# ── Helper ────────────────────────────────────────────────────────────

def _write_tiny_png(path: Path) -> None:
    """Write a minimal valid 1x1 red PNG file."""
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1 RGB
    raw_row = b"\x00\xff\x00\x00"  # filter byte + R G B
    idat_data = zlib.compress(raw_row)

    with open(path, "wb") as f:
        f.write(sig)
        f.write(_chunk(b"IHDR", ihdr_data))
        f.write(_chunk(b"IDAT", idat_data))
        f.write(_chunk(b"IEND", b""))
