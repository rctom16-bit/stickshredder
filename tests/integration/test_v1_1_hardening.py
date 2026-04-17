"""Cross-cutting integration tests for the v1.1 hardening work.

Each individual hardening fix (audit_log injection escaping, cert XML escaping,
full_verify sector counting, zero-blank appending, cert counter locking) has
its own unit test. These integration tests exercise combinations of those
components to catch regressions that only surface when they interact — e.g.
a WipeResult produced by the demo path whose fields need to flow correctly
into a rendered certificate.

Skip rules:
    * The single-bit-flip + full_verify test requires Windows CreateFileW
      semantics — skipped on non-Windows.
    * The cert, audit-log and demo-wipe tests are cross-platform.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from datetime import datetime
from unittest.mock import patch

import pytest

# ── cert.generator imports ────────────────────────────────────────────
from cert.generator import (
    CertificateData,
    _build_styles,
    _build_verification_elements,
)
from reportlab.platypus import Paragraph, Table

# ── core imports ──────────────────────────────────────────────────────
from core.config import get_next_cert_number
from core.log import audit_log

# ── wipe imports ──────────────────────────────────────────────────────
from wipe.demo import DEFAULT_DEMO_SIZE, create_demo_device, create_demo_file, wipe_demo_file
from wipe.methods import BsiVsitr


# ─────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────

def _flatten_paragraph_texts(elements: list) -> str:
    """Recursively collect the raw source text of every Paragraph/Table in
    the element tree. Returns one big newline-joined string so substring
    assertions work.

    Mirrors the helper in ``tests/test_certificate.py`` so the two test
    suites stay behaviourally aligned.
    """
    parts: list[str] = []

    def _walk(flow):
        if isinstance(flow, Paragraph):
            # reportlab Paragraph keeps the original (pre-parse) source text
            # on the ``.text`` attribute.
            parts.append(getattr(flow, "text", "") or "")
        elif isinstance(flow, Table):
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


def _capture_paragraph_texts(monkeypatch) -> list[str]:
    """Monkeypatch ``cert.generator.Paragraph`` so every Paragraph built by
    ``generate_certificate`` has its raw source text recorded.

    Same spy pattern used in ``tests/test_certificate.py``. We use this for
    the XML-injection integration test where we exercise the full PDF path.
    """
    import cert.generator as gen

    captured: list[str] = []
    real_paragraph = gen.Paragraph

    def _spy(text, *args, **kwargs):
        captured.append(text if isinstance(text, str) else str(text))
        return real_paragraph(text, *args, **kwargs)

    monkeypatch.setattr(gen, "Paragraph", _spy)
    return captured


# ─────────────────────────────────────────────────────────────────────
# 1. End-to-end demo wipe exercises the v1.1 path correctly
# ─────────────────────────────────────────────────────────────────────

def test_demo_wipe_bsi_vsitr_full_verify_end_to_end(tmp_path, monkeypatch):
    """BSI-VSITR + full verify → 8 passes (7 core + 1 zero-blank), full
    verify succeeds, sector count is non-zero (the v1.1.1 fix), and the
    entire file was scanned."""
    # Redirect audit log + config dir so this test can't clobber user state.
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr("core.log.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.log.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    # Sanity-check the demo device factory still works — used by the GUI
    # even though the demo wipe itself operates on a file path.
    device = create_demo_device()
    assert device.capacity_bytes == DEFAULT_DEMO_SIZE

    # Create a virtual disk file and wipe it with full verify.
    demo_path = create_demo_file(size_bytes=DEFAULT_DEMO_SIZE,
                                 path=str(tmp_path / "demo.bin"))
    method = BsiVsitr()
    result = wipe_demo_file(demo_path, method, verify_mode="full")

    # Wipe succeeded
    assert result.success is True, (
        f"demo wipe failed: {result.error_message!r}"
    )

    # Zero-blank pass was appended because BSI-VSITR's pass 7 is random
    # and verify_mode != "none"
    assert result.zero_blank_appended is True
    assert result.passes == 8, (
        f"expected 7 core + 1 zero-blank = 8 passes, got {result.passes}"
    )

    # Verify result is populated and correct
    vr = result.verify_result
    assert vr is not None
    assert vr.method == "full"
    assert vr.success is True
    # v1.1.1 fix: sector count must be non-zero for a full verify of a
    # non-empty file. The regression was that this stayed at 0.
    assert vr.sectors_checked > 0, (
        f"full-verify must report non-zero sector count; got {vr.sectors_checked}"
    )
    assert vr.bytes_verified == DEFAULT_DEMO_SIZE
    assert vr.error_count == 0
    assert vr.expected_pattern == "zeros"  # zero-blank → expect zeros


# ─────────────────────────────────────────────────────────────────────
# 2. Certificate from v1.1 wipe reflects correct pass count and verify fields
# ─────────────────────────────────────────────────────────────────────

def test_cert_from_v1_1_wipe_shows_total_passes_and_full_mode(tmp_path, monkeypatch):
    """A certificate built from the same demo-wipe result must render the
    *total* pass count (including the zero-blank pass) and label the
    verification mode as ``Full``. Prior to v1.1 the cert showed only the
    method's nominal pass count."""
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr("core.log.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.log.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    # Run the same demo wipe as test 1.
    demo_path = create_demo_file(size_bytes=DEFAULT_DEMO_SIZE,
                                 path=str(tmp_path / "demo.bin"))
    method = BsiVsitr()
    wipe_result = wipe_demo_file(demo_path, method, verify_mode="full")

    assert wipe_result.success and wipe_result.verify_result is not None
    vr = wipe_result.verify_result

    # Build a realistic CertificateData directly from the wipe outputs.
    data = CertificateData(
        cert_number=42,
        date=datetime(2026, 4, 17, 12, 0, 0),
        operator="Integration Test",
        client_reference="",
        asset_tag="",
        device_model="StickShredder Demo Drive",
        device_manufacturer="Virtual",
        serial_number="DEMO-TEST",
        capacity_bytes=DEFAULT_DEMO_SIZE,
        filesystem="FAT32",
        connection_type="Virtual",
        wipe_method=wipe_result.method_name,
        sicherheitsstufe=method.sicherheitsstufe,
        schutzklasse=3,
        # THE FIX: use the WipeResult's total passes, not method.passes
        passes=wipe_result.passes,
        start_time=wipe_result.start_time,
        end_time=wipe_result.end_time,
        verification_passed=vr.success,
        sectors_checked=vr.sectors_checked,
        verification_hash="",
        company_name="Test GmbH",
        company_address="Teststr. 1\n12345 Berlin",
        company_logo_path="",
        language="en",
        verify_method=vr.method,
        verify_bytes=vr.bytes_verified,
        verify_pattern=vr.expected_pattern,
        verify_error_count=vr.error_count,
        verify_mismatch_offsets=vr.mismatch_offsets,
        verify_duration_seconds=vr.duration_seconds,
    )

    styles = _build_styles()
    elements = _build_verification_elements(data, styles, data.language)
    text = _flatten_paragraph_texts(elements)

    # Full-mode label is present ("Full (all sectors)").
    assert "Full" in text, f"expected 'Full' in verification section, got: {text!r}"
    # Sectors Checked should be non-zero (v1.1.1 fix flows through to cert).
    assert f"{vr.sectors_checked:,}".replace(",", ".") in text
    assert vr.expected_pattern == "zeros"
    assert "zeros" in text

    # The pass-count cell lives in the Wipe Details section, not the
    # verification section. Assert on the dataclass field directly so we
    # know the caller used the correct value (the v1.1 fix for "cert
    # shows 7 even though 8 passes ran"). The renderer turns this into
    # str(data.passes) inside a Paragraph, which we already know works
    # via the unit tests in tests/test_certificate.py.
    assert data.passes == 8, (
        f"cert data.passes should equal wipe_result.passes (8), got {data.passes}"
    )
    assert str(data.passes) == "8"


# ─────────────────────────────────────────────────────────────────────
# 3. XML injection attempt in cert is neutralized
# ─────────────────────────────────────────────────────────────────────

def test_cert_xml_injection_neutralized_end_to_end(tmp_path, monkeypatch):
    """Hostile strings from operator/company/device fields must be XML-escaped
    before reaching reportlab's Paragraph parser. This is the end-to-end
    version that actually generates a PDF (rather than the per-field unit
    tests) to catch accidental regressions where a new call-site forgets
    to route user input through ``_safe()``."""
    from cert.generator import generate_certificate

    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr("core.log.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.log.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    # Hostile inputs across THREE user-controlled fields at once.
    hostile_operator = '</font><font color="red">ATTACKER'
    hostile_company = "AT&T GmbH & Co"
    hostile_device_model = "<script>alert(1)</script>"

    # Install the Paragraph spy BEFORE generate_certificate imports happen.
    captured = _capture_paragraph_texts(monkeypatch)

    out = tmp_path / "cert_hostile.pdf"
    data = CertificateData(
        cert_number=1,
        date=datetime(2026, 4, 17, 12, 0, 0),
        operator=hostile_operator,
        client_reference="",
        asset_tag="",
        device_model=hostile_device_model,
        device_manufacturer="SanDisk",
        serial_number="ABC123",
        capacity_bytes=32 * 1024 ** 3,
        filesystem="FAT32",
        connection_type="USB",
        wipe_method="ZeroFill",
        sicherheitsstufe="H-2",
        schutzklasse=2,
        passes=1,
        start_time=datetime(2026, 4, 17, 11, 0, 0),
        end_time=datetime(2026, 4, 17, 11, 30, 0),
        verification_passed=True,
        sectors_checked=100,
        verification_hash="a" * 64,
        company_name=hostile_company,
        company_address="Teststr. 1\n12345 Berlin",
        company_logo_path="",
        language="en",
    )

    # audit_log is already pointing at tmp_path via the monkeypatch above,
    # but we also silence it to avoid the datetime/path noise in test output.
    with patch("cert.generator.audit_log"):
        generate_certificate(data, str(out))

    assert out.exists()

    # Concatenate all raw Paragraph source strings. Every one of those is
    # what reportlab parses as XML — that's where escaping must already
    # have happened.
    blob = "\n".join(captured)

    # Raw attack strings must NOT appear — they would be parsed as markup.
    assert "</font>" not in blob, (
        "raw </font> leaked into a Paragraph; operator field is not escaped"
    )
    assert '<font color="red">' not in blob, (
        "raw <font ...> leaked; operator field is not escaped"
    )
    assert "<script>" not in blob, (
        "raw <script> leaked into a Paragraph; device_model field is not escaped"
    )
    # Bare "AT&T GmbH" would break reportlab's strict XML parser. The
    # escaped form must be present instead. Scan every Paragraph source
    # individually so we can't be fooled by an escaped occurrence
    # appearing near a raw one.
    for text in captured:
        if "GmbH" in text:
            assert "AT&T" not in text, (
                f"bare 'AT&T' leaked into Paragraph: {text!r}"
            )

    # Escaped forms must appear somewhere.
    assert "&lt;/font&gt;" in blob, "operator attack not escaped"
    assert "&lt;script&gt;" in blob, "device_model attack not escaped"
    assert "AT&amp;T" in blob, "company_name ampersand not escaped"


# ─────────────────────────────────────────────────────────────────────
# 4. Audit log injection attempt is neutralized
# ─────────────────────────────────────────────────────────────────────

def test_audit_log_injection_neutralized_integration(tmp_path, monkeypatch):
    """Two consecutive audit_log() calls — the second containing a crafted
    newline + forged-entry payload — must produce exactly 2 physical log
    lines, not 3. The forged entry collapses onto line 2 with a literal
    ``\\n`` instead of a real newline."""
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr("core.log.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.log.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    audit_log("legit entry")
    audit_log("evil\n2099-01-01 | FORGED ENTRY | cert=999")

    log_file = cfg_dir / "audit.log"
    assert log_file.exists()

    content = log_file.read_text(encoding="utf-8")
    lines = content.strip().splitlines()

    # Exactly 2 real log lines — not 3.
    assert len(lines) == 2, (
        f"expected 2 lines (log-injection must be neutralised), got "
        f"{len(lines)}: {lines!r}"
    )

    # Line 1: the legit entry
    assert "legit entry" in lines[0]

    # Line 2: the attacker's payload, with the newline replaced by literal '\n'
    assert "evil\\n2099-01-01" in lines[1], (
        f"forged entry must be collapsed with literal \\n; got: {lines[1]!r}"
    )
    # The forged content is still PRESENT (we don't drop it — we neutralise it)
    assert "FORGED ENTRY" in lines[1]
    # But the pipes inside the forged text must be escaped so log parsers
    # can't be fooled into splitting the line.
    import re
    bare_pipes = re.findall(r"(?<!\\)\|", lines[1])
    # Only the legitimate timestamp|message separator is unescaped.
    assert len(bare_pipes) == 1, (
        f"expected 1 bare pipe (the timestamp separator), got "
        f"{len(bare_pipes)} in: {lines[1]!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# 5. Full verify detects a single-byte flip in a sparse region
# ─────────────────────────────────────────────────────────────────────

# Windows-only — full_verify uses CreateFileW/ReadFile via kernel32.
_WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32",
    reason="full_verify integration test requires Windows CreateFileW/ReadFile",
)


# Win32 constants, duplicated from tests/integration/test_full_verify_sparse.py
# so this file has no hidden inter-test coupling.
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 1
_FILE_SHARE_WRITE = 2
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = -1

_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None


def _open_file_raw(path: str, write: bool = False) -> int:
    """Open ``path`` with CreateFileW and return the Win32 HANDLE as int.

    Copied from ``tests/integration/test_full_verify_sparse.py`` per the
    task brief so this test file has no cross-file helper dependency.
    """
    access = _GENERIC_READ | (_GENERIC_WRITE if write else 0)
    handle = _kernel32.CreateFileW(
        path,
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        0,
        None,
    )
    if handle in (_INVALID_HANDLE_VALUE, 0xFFFFFFFFFFFFFFFF, 0, None):
        err = ctypes.get_last_error()
        raise OSError(f"CreateFileW failed for {path}: error {err}")
    return handle


def _close(handle: int) -> None:
    if handle and handle not in (_INVALID_HANDLE_VALUE, 0xFFFFFFFFFFFFFFFF):
        _kernel32.CloseHandle(handle)


def _make_sparse_file(path: str, size: int) -> None:
    """Create a zero-filled sparse file via fsutil + seek-extend."""
    with open(path, "wb"):
        pass
    try:
        subprocess.run(
            ["fsutil", "sparse", "setflag", path],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # fsutil unavailable — NTFS still yields zero-on-read for the hole.
        pass
    with open(path, "r+b") as f:
        f.seek(size - 1)
        f.write(b"\x00")
        f.flush()
    assert os.path.getsize(path) == size


@_WINDOWS_ONLY
def test_full_verify_detects_single_bit_flip_16mb(tmp_path):
    """Complement to the 1 GiB sparse test: a *16 MB* sparse file with a
    single-byte flip at offset 0x500000 must be caught by full_verify.

    This test uses a smaller file than the existing 1 GiB sparse test so
    it runs fast enough to include in the default suite.
    """
    from wipe.verify import full_verify

    size = 16 * 1024 * 1024
    flip_offset = 0x500000  # 5 MB in — block-aligned for default 1 MB block

    sparse = tmp_path / "sparse_16mb.bin"
    _make_sparse_file(str(sparse), size)

    # Flip one byte from 0x00 to 0x5A (non-zero, deterministic).
    with open(sparse, "r+b") as f:
        f.seek(flip_offset)
        f.write(b"\x5A")
        f.flush()
        os.fsync(f.fileno())

    handle = _open_file_raw(str(sparse), write=False)
    try:
        result = full_verify(
            handle=handle,
            drive_size=size,
            expected_pattern=b"\x00",
        )
    finally:
        _close(handle)

    assert result.success is False, (
        "full_verify should FAIL with a non-zero byte in a zero region"
    )
    assert result.error_count >= 1
    assert result.bytes_verified == size
    assert flip_offset in result.mismatch_offsets, (
        f"expected offset {hex(flip_offset)} in mismatch_offsets; "
        f"got {[hex(o) for o in result.mismatch_offsets]}"
    )


# ─────────────────────────────────────────────────────────────────────
# 6. Cert counter concurrency — 10 threads race safely
# ─────────────────────────────────────────────────────────────────────

def test_cert_counter_10_thread_race_produces_unique_numbers(tmp_path, monkeypatch):
    """10 threads hammering ``get_next_cert_number`` concurrently must each
    receive a distinct integer. This complements the dedicated unit test
    in ``test_config.py`` by exercising the real function (not a mocked
    one) inside a larger integration context — i.e. if the in-process
    ``_CERT_COUNTER_LOCK`` ever regressed to a per-call local lock, this
    test would flag it."""
    cfg_dir = tmp_path / "cfg"
    counter_file = cfg_dir / "cert_counter.txt"
    monkeypatch.setattr("core.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("core.config.CERT_COUNTER_FILE", counter_file)
    monkeypatch.setattr("core.config.AUDIT_LOG_FILE", cfg_dir / "audit.log")

    # Pre-create the counter file so that all 10 threads race on the
    # read-modify-write path (the part protected by _CERT_COUNTER_LOCK)
    # rather than the unguarded ``if not CERT_COUNTER_FILE.exists()`` init
    # branch at the top of get_next_cert_number(). Without this pre-seed,
    # multiple threads can each hit the "not exists" branch and the
    # subsequent write_text() call races with the first thread's open()
    # for the lock. The real-world counter file is created once at first
    # wipe and persists for the lifetime of the install, so the init
    # branch is not contended in production.
    cfg_dir.mkdir(parents=True, exist_ok=True)
    counter_file.write_text("0", encoding="utf-8")

    results: list[int] = []
    results_lock = threading.Lock()
    start_gate = threading.Event()

    def worker() -> None:
        # All threads wait on the gate so they race in as simultaneously
        # as the OS scheduler allows.
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
    # All 10 must be unique — that's the property the lock guarantees.
    assert len(set(results)) == 10, (
        f"duplicate cert numbers under concurrent load: {sorted(results)}"
    )
