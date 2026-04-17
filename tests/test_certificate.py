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

from cert.generator import (
    CertificateData,
    _build_styles,
    _build_verification_elements,
    _format_offsets_hex,
    _format_seconds,
    format_capacity,
    format_duration,
    generate_certificate,
)
from reportlab.platypus import Paragraph, Table


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


# ── _format_offsets_hex ──────────────────────────────────────────────

def test_format_offsets_hex_basic():
    assert _format_offsets_hex([0x400000, 0x10000200]) == "0x00400000, 0x10000200"


def test_format_offsets_hex_caps_at_limit():
    offsets = [i * 0x1000 for i in range(1, 16)]  # 15 offsets
    result = _format_offsets_hex(offsets, limit=10)
    parts = result.split(", ")
    assert len(parts) == 10
    assert parts[0] == "0x00001000"
    assert parts[9] == "0x0000A000"


def test_format_offsets_hex_empty():
    assert _format_offsets_hex([]) == ""


def test_format_offsets_hex_default_limit_10():
    offsets = [i * 0x100 for i in range(1, 21)]  # 20 offsets
    result = _format_offsets_hex(offsets)
    assert len(result.split(", ")) == 10


# ── _format_seconds ──────────────────────────────────────────────────

def test_format_seconds_basic():
    assert _format_seconds(1234.5) == "00:20:34"


def test_format_seconds_zero():
    assert _format_seconds(0.0) == "00:00:00"


def test_format_seconds_large():
    # 26h 3m 4s = 93784 seconds
    assert _format_seconds(93784.0) == "26:03:04"


def test_format_seconds_negative_clamped():
    assert _format_seconds(-5.0) == "00:00:00"


# ── _build_verification_elements — inspecting Paragraph text ─────────

def _paragraph_texts(elements: list) -> str:
    """Collect all Paragraph text content from a list of flowables (including
    recursively from Table cells) into one big string for substring assertions."""
    parts: list[str] = []

    def _walk(flow):
        if isinstance(flow, Paragraph):
            # reportlab Paragraph keeps the original text in .text
            parts.append(getattr(flow, "text", "") or "")
        elif isinstance(flow, Table):
            # Table._cellvalues holds the cell data (list of rows)
            cells = getattr(flow, "_cellvalues", None) or []
            for row in cells:
                for cell in row:
                    if isinstance(cell, list):
                        for item in cell:
                            _walk(item)
                    else:
                        _walk(cell)

    for el in elements:
        _walk(el)
    return "\n".join(parts)


def test_cert_renders_sample_verify_section():
    data = _make_cert_data(
        verify_method="sample",
        sectors_checked=100,
        verification_hash="deadbeef" * 8,
        language="both",
    )
    styles = _build_styles()
    elements = _build_verification_elements(data, styles, data.language)
    text = _paragraph_texts(elements)
    # sample mode label should appear in EN or DE
    assert "Sample" in text or "Probe" in text
    # sector count shown (formatted with . as thousands separator)
    assert "100" in text
    # hash shown
    assert "deadbeef" in text


def test_cert_renders_full_verify_section():
    data = _make_cert_data(
        verify_method="full",
        verify_bytes=int(32e9),
        verify_pattern="zeros",
        verify_duration_seconds=1234.5,
        verify_error_count=0,
        language="en",
    )
    styles = _build_styles()
    elements = _build_verification_elements(data, styles, data.language)
    text = _paragraph_texts(elements)
    assert "Full" in text or "Vollständig" in text
    assert "Expected Pattern" in text
    assert "zeros" in text
    # bytes formatted via format_capacity -> GB range
    assert "GB" in text
    # duration formatted HH:MM:SS from 1234.5s -> 00:20:34
    assert "00:20:34" in text


def test_cert_shows_first_10_mismatch_offsets_on_failure():
    offsets = [i * 0x1000 for i in range(1, 16)]  # 15 offsets
    data = _make_cert_data(
        verify_method="full",
        verify_bytes=int(32e9),
        verify_pattern="zeros",
        verify_error_count=15,
        verify_mismatch_offsets=offsets,
        verification_passed=False,
        language="en",
    )
    styles = _build_styles()
    elements = _build_verification_elements(data, styles, data.language)
    text = _paragraph_texts(elements)
    # First 10 formatted offsets must appear
    for i in range(1, 11):
        assert f"0x{i * 0x1000:08X}" in text
    # 11th-15th should NOT appear
    assert "0x0000B000" not in text
    assert "0x0000F000" not in text
    # Error count shown
    assert "15" in text


def test_cert_verify_none_shows_skipped():
    data = _make_cert_data(verify_method="none", language="both")
    styles = _build_styles()
    elements = _build_verification_elements(data, styles, data.language)
    text = _paragraph_texts(elements)
    assert "Skipped" in text or "Nicht durchgeführt" in text


@patch("cert.generator.audit_log")
def test_cert_backward_compat_no_verify_fields(mock_log, tmp_path):
    """A pre-v1.1 caller doesn't pass any of the new verify_* fields."""
    # Build CertificateData passing ONLY the pre-v1.1 fields (no verify_method, etc.)
    data = CertificateData(
        cert_number=7,
        date=datetime(2026, 4, 15, 14, 30, 0),
        operator="Legacy Caller",
        client_reference="",
        asset_tag="",
        device_model="Kingston",
        device_manufacturer="Kingston",
        serial_number="LEGACY-001",
        capacity_bytes=16 * 1024**3,
        filesystem="exFAT",
        connection_type="USB",
        wipe_method="ZeroFill",
        sicherheitsstufe="H-2",
        schutzklasse=2,
        passes=1,
        start_time=datetime(2026, 4, 15, 14, 0, 0),
        end_time=datetime(2026, 4, 15, 14, 25, 0),
        verification_passed=True,
        sectors_checked=50,
        verification_hash="b" * 64,
        company_name="Legacy GmbH",
        company_address="Altstr. 1",
        company_logo_path="",
        language="de",
    )
    # Defaults should give sample-mode rendering and no crash
    assert data.verify_method == "sample"
    assert data.verify_bytes == 0
    assert data.verify_pattern == ""
    assert data.verify_error_count == 0
    assert data.verify_mismatch_offsets == []
    assert data.verify_duration_seconds == 0.0

    out = str(tmp_path / "legacy.pdf")
    result_path = generate_certificate(data, out)
    p = Path(result_path)
    assert p.exists()
    assert p.stat().st_size > 0
    assert p.read_bytes()[:5] == b"%PDF-"


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
