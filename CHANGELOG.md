# Changelog

All notable changes to StickShredder are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-04-17

### Added

- **Full verification mode.** New `full_verify()` in `wipe/verify.py` reads every sector of the drive after wiping and compares against the expected pattern, detecting "silently failing" sectors that return stale data after a successful `WriteFile`. Reports up to 100 mismatching byte offsets for forensic follow-up.
- **`--verify {none,sample,full}`** CLI flag replaces the old `--no-verify`. Default `sample` preserves 1.0.x behavior.
- **GUI "Full verification" checkbox** with bilingual label ("Vollständige Verifikation (verdoppelt Laufzeit)" / "Full verification (doubles runtime)") in the wipe configuration panel. Progress display gains a two-phase UI: wipe bar, verify bar, and a phase badge (Wiping → Verifying → Done).
- **`VerifyResult` dataclass** with `success`, `method`, `bytes_verified`, `expected_pattern`, `error_count`, `mismatch_offsets`, `duration_seconds`, and legacy sample-mode fields for backward compatibility.
- **Automatic zero-blanking pass** for random-data methods (`3-Pass Random`, `BSI-VSITR`, custom-random) when verification is enabled. Without this, random data is indistinguishable from corrupted data without storing the PRNG seed. The extra pass is recorded on the certificate and in `WipeResult.zero_blank_appended`.
- **Extended PDF certificate** with a richer verification section: for sample mode shows sectors checked and SHA-256 hash; for full mode shows verified bytes (GB), expected pattern, duration, error count, and the first 10 mismatch offsets (hex-formatted) on failure. All strings translated for DE/EN/bilingual output.
- **Integration test** `tests/integration/test_full_verify_sparse.py` — 1 GB sparse file round-trip (success on all-zeros, failure after a random write at a known offset).

### Changed

- **`verify_wipe()` renamed to `sample_verify()`** in `wipe/verify.py`. The old name is kept as a deprecated alias.
- **`VerificationResult` → `VerifyResult`** (old name is an alias). The constructor kwarg is now `success=` instead of `passed=`. Internal callers (`cli.py`, `wipe/demo.py`, `gui/wipe_worker.py`) have been migrated.
- **`WipeMethod.execute()`** gained `verify_mode` and `verify_progress_callback` parameters. `verify_mode="none"` keeps the pre-1.1 behavior (no verify, no zero-blanking pass). Default is `"none"` at the API level; CLI and GUI set their own defaults (`sample`).
- **`WipeResult`** gained optional `verify_result` and `zero_blank_appended` fields.
- **CSV history** records verification outcomes as `SKIPPED` / `SAMPLE-PASSED` / `SAMPLE-FAILED` / `FULL-PASSED` / `FULL-FAILED`. The history table widens the verification column to fit.
- **README** gains a "Verification Modes" section (EN + DE) and the wipe-methods table now has a "Verify Support" column. Random-method pass counts are shown as `3 (+1)` / `7 (+1)` with a footnote explaining the blanking pass.

### Tests

- 135 tests passing (up from 96 on the 1.0.x line). 12 new GUI tests (pytest-qt), 13 new certificate tests, 11 new wipe-methods tests, 9 new full-verify unit tests, 5 new CLI tests, 2 new integration tests.
