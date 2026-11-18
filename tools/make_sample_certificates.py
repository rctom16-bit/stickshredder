"""Render the signed sample certificates into docs/examples/.

    python tools/make_sample_certificates.py [output-dir]

sample-certificate-2.0-de.pdf      German
sample-certificate-2.0-de-en.pdf   German / English

Demo data only (made-up disk, serial, company and operator). Signed with a
throwaway key that exists only in memory for this run — never this PC's key,
never stored — so the samples check as "signature valid, key unknown". The
version printed on them is APP_VERSION. Config and audit log go to a temp
profile; no disk is touched.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from screenshots_v2 import DEMO_OPERATOR, _isolate_profile  # noqa: E402

SAMPLES = (("de", "sample-certificate-2.0-de.pdf"),
           ("both", "sample-certificate-2.0-de-en.pdf"))


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "examples"
    out_dir.mkdir(parents=True, exist_ok=True)
    _isolate_profile(Path(tempfile.mkdtemp(prefix="stickshredder_samples_")))

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from cert.generator import CertificateData, generate_certificate
    from cert.signing import SigningKey
    from core.config import APP_VERSION
    from wipe.verify import SECTOR_SIZE

    key = SigningKey(Ed25519PrivateKey.generate())  # throwaway, in memory only
    capacity = 61_953_654_784
    start = datetime(2026, 11, 18, 9, 2, 14)
    end = datetime(2026, 11, 18, 10, 36, 41)
    history_hash = hashlib.sha256(b"StickShredder sample certificate").hexdigest()

    for lang, name in SAMPLES:
        data = CertificateData(
            cert_number=42, date=end, operator=DEMO_OPERATOR,
            client_reference="Ticket 4711", asset_tag="INV-0815",
            device_model="Generic USB Flash Disk", device_manufacturer="",
            serial_number="DEMO00000001", capacity_bytes=capacity, filesystem="exFAT",
            connection_type="USB", wipe_method="ZeroFill", sicherheitsstufe="1-2",
            schutzklasse=2, passes=1, start_time=start, end_time=end,
            verification_passed=True, sectors_checked=capacity // SECTOR_SIZE,
            verification_hash="", company_name="Muster IT GmbH",
            company_address="Königstraße 12\n90402 Nürnberg", company_logo_path="",
            language=lang, verify_method="full", verify_bytes=capacity,
            verify_pattern="zeros", verify_error_count=0, verify_duration_seconds=2710.0,
            media_type="flash",
        )
        print(generate_certificate(data, str(out_dir / name), signing_key=key,
                                   history_hash=history_hash))
    print(f"StickShredder {APP_VERSION}, throwaway key {key.fingerprint()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
