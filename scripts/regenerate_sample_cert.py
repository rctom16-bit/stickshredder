"""Regenerate docs/examples/sample-certificate.pdf with v1.1 full-verify output.

Run from repo root:
    python scripts/regenerate_sample_cert.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from cert.generator import CertificateData, generate_certificate


def main() -> int:
    now = datetime(2026, 4, 17, 14, 32, 11)
    start = now
    end = start + timedelta(minutes=48, seconds=22)

    data = CertificateData(
        cert_number=42,
        date=now,
        operator="Robin Oertel",
        client_reference="ACME GmbH (internal IT)",
        asset_tag="IT-2026-0042",
        device_model="SanDisk Ultra USB 3.0",
        device_manufacturer="SanDisk",
        serial_number="4C530001230620116213",
        capacity_bytes=32_212_254_720,
        filesystem="FAT32",
        connection_type="USB",
        wipe_method="BSI-VSITR",
        sicherheitsstufe="4+",
        schutzklasse=2,
        passes=8,
        start_time=start,
        end_time=end,
        verification_passed=True,
        sectors_checked=0,
        verification_hash="",
        company_name="StickShredder Demo GmbH",
        company_address="Beispielstrasse 1\n12345 Beispielstadt\nDeutschland",
        company_logo_path="",
        language="both",
        verify_method="full",
        verify_bytes=32_212_254_720,
        verify_pattern="zeros",
        verify_error_count=0,
        verify_mismatch_offsets=[],
        verify_duration_seconds=1_452.7,
    )

    output = REPO_ROOT / "docs" / "examples" / "sample-certificate.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    path = generate_certificate(data, str(output))
    print(f"Wrote: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
