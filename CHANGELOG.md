# Changelog

All notable changes to StickShredder are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-06-07

This release is driven by community feedback. It tackles the limits of pure
overwrite wiping: bad sectors, internal disks, hidden disk areas, and
firmware-level erase.

### Added

- **Bad-sector tolerance and reporting.** A failed `WriteFile` on a block no
  longer aborts the whole wipe. The block offset is recorded, the pass seeks
  past it and continues, and the defect count + first 100 offsets are reported
  on the certificate (new "Bad Sectors / Defekte Sektoren" section), in the CLI
  summary, and in a new `bad_sectors` CSV column. A bad-fraction ceiling
  (default 10%) still fails the wipe outright when most of the drive is
  unwritable, so a dying drive is never reported as "successfully wiped".
- **Internal (non-removable) disk support.** Physical disks without a drive
  letter (raw / unmounted internal data disks) are now enumerated and can be
  wiped. Gated behind an explicit opt-in: CLI `--allow-internal` and a GUI
  "Allow internal drives" checkbox (off by default). Internal wipes require an
  extra model-name confirmation in the CLI. Letterless disks are selectable via
  `--device PhysicalDriveN`.
- **`is_safe_to_wipe()` central safety gate** used by both CLI and GUI. The
  Windows system disk is always refused; internal drives are refused without
  opt-in; and — fail-safe — internal drives are refused whenever the system
  disk cannot be positively identified.
- **ATA pass-through foundation** (`wipe/passthrough.py`): a defensive
  `IOCTL_ATA_PASS_THROUGH_DIRECT` wrapper (`ata_identify`,
  `read_native_max_address`, `device_configuration_identify_max`,
  `send_ata_command`). Never raises; returns `None` when a device rejects ATA
  pass-through (common on USB bridges).
- **HPA/DCO detection** (`wipe/hidden_areas.py`, report-only). Before wiping, the
  drive is probed for a Host Protected Area or Device Configuration Overlay —
  regions hidden from the OS that an overwrite never reaches. Findings are
  warned about and recorded on the certificate (new "Hidden Areas (HPA/DCO)"
  section) and in a new `hidden_area` CSV column. The drive is never modified
  (no SET MAX / DCO commands).
- **Experimental ATA Secure Erase** (`wipe/secure_erase.py`). New CLI method
  `--method secure-erase`, gated behind `--experimental` (plus `--enhanced`),
  issues the drive's own firmware erase (SECURITY SET PASSWORD / ERASE PREPARE /
  ERASE UNIT) instead of overwriting. Refuses on frozen / locked / unsupported
  drives. Clearly marked experimental on the certificate. **Untested on real
  hardware** — see SECURITY.md. (GUI exposure is a planned follow-up.)

### Changed

- **`APP_VERSION` corrected from `1.0.0` to `1.2.0`.** It had been left at
  `1.0.0` through the 1.1 line, so certificates printed the wrong tool version
  in their footer. All version strings (pyproject, installer, GUI title/about,
  Qt application version, certificate footer) now read `1.2.0`.

### Security

- **System-drive guard hardened** to resolve the PhysicalDrive index that hosts
  the Windows boot volume (not just the drive letter) and refuse to open it in
  `open_physical_drive()`. With internal-disk wiping now possible, the guard
  fails safe: if the Windows disk cannot be identified, internal wipes are
  refused rather than risked.
- **Boot / EFI / system-partition protection.** The guard no longer protects
  only the disk holding the `\Windows` directory — it now flags *every* physical
  disk that carries a boot, EFI System, or active partition (via the Storage
  namespace `MSFT_Partition` `IsBoot`/`IsSystem`/`IsActive` flags). This closes
  the dangerous case where the partition that *boots* Windows lives on a
  different physical disk than the OS files (e.g. after cloning to a new SSD, or
  on multi-disk builds): wiping that disk would leave the machine unbootable.
  Internal wipes are additionally refused (fail-safe) whenever the boot-partition
  layout cannot be read.

### Tests

- 302 passing (up from 218). New: bad-sector tolerance/ceiling/dedup, letterless
  enumeration, `is_safe_to_wipe` gate, boot/EFI/system-partition disk protection,
  ATA pass-through parsing, HPA/DCO detection, secure-erase state machine, CLI
  secure-erase + HPA wiring, and the GUI internal-drive safety gate.

## [1.1.0] - 2026-04-17

### Added

- **"Reformat after wipe" feature** (GUI checkbox + CLI `--reformat` flag). Supported filesystems: FAT32, exFAT, NTFS. Creates a fresh MBR/GPT partition spanning the full drive and formats it so the device is immediately usable post-wipe. PowerShell-based (`Clear-Disk`, `New-Partition`, `Format-Volume`). Optional `--reformat-label NAME` sets the volume label; `--reformat-partition {MBR,GPT}` selects the partition style. Default remains `none` (preserves 1.0.x / pre-1.1 behavior of leaving the drive unpartitioned).
- **New `FormatResult` dataclass** in `wipe/format.py`; new optional `format_result` field on `WipeResult`; new optional `reformat_performed` / `reformat_filesystem` / `reformat_label` fields on `CertificateData`.
- **New "Reformat / Formatierung" section** on the PDF certificate (German + English) shown when a reformat was performed. All values are XML-escaped before rendering.
- **New CSV history columns** `reformat` and `reformat_label` record the filesystem and volume label used (or `NONE` if the reformat step was skipped).
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

### Security

- **ReportLab XML injection.** All user-supplied strings (operator, company name, device model, serial number, asset tag, client reference, etc.) now pass through `xml.sax.saxutils.escape()` before being rendered into PDF `Paragraph` elements. A serial number or company name containing `<`, `>`, or `&` could previously corrupt the certificate layout or inject ReportLab markup.
- **System drive detection no longer trusts `%SystemRoot%`.** The guard that prevents wiping the active Windows installation now calls `GetWindowsDirectoryW` (Win32 API) instead of reading the `SystemRoot` environment variable, which a non-elevated caller could override in the launching process.
- **Audit log injection.** `audit_log()` now sanitizes newlines, carriage returns, and pipe characters in every field before writing. Previously, a crafted device serial number or asset tag containing `\n` or `|` could forge fake log entries in `audit.log`.
- **UAC relaunch argument quoting.** The elevation helper in `main.py` now builds its `ShellExecuteW` parameter string via `subprocess.list2cmdline`, with a proper frozen/source distinction so PyInstaller builds do not duplicate `argv[0]`. The previous `" ".join(sys.argv)` was vulnerable to argument injection when the install path or any CLI argument contained spaces or quote characters.

### Fixed

- **Progress bar stuck at 0% + ETA stuck on "Calculating..." on drives >2 GiB.** Root cause: Qt `Signal(int, ...)` marshals to signed 32-bit, so byte counts above 2,147,483,647 wrapped to negative values and the `if total_bytes > 0` guard silently skipped the UI update. Fixed by declaring byte-count signal fields as `'qlonglong'` (int64). Verified on a real 128 GB wipe.
- **`manage-bde` / `handle.exe` output with non-ASCII bytes** caused `UnicodeDecodeError` in the subprocess reader thread, which left `result.stdout = None` and crashed device enumeration with `'NoneType' object has no attribute 'lower'`. Drives silently disappeared from the device list. Now guarded with `(result.stdout or "").lower()` and a broad `except Exception` on the probes.
- **Wipe progress UI showed the wrong device name** (full device list used instead of the selected subset), making the wipe appear to target the system drive even though the actual I/O was against the selected USB. The underlying wipe target was always correct; only the label was wrong.
- **Physical-drive `FSCTL_LOCK_VOLUME` removed** — some USB controllers deadlocked `WriteFile` when the physical-drive handle was locked. The prior `dismount_volume` already gives us exclusive access, so the extra lock was redundant and occasionally harmful.
- **`CSV_HEADERS` in `core/log.py`** now includes the new reformat columns so `csv.DictWriter` doesn't raise `ValueError` at runtime.
- **ctypes prototypes declared** for every `kernel32` and `shell32` call (`argtypes` / `restype`). Prevents 64-bit `HANDLE` truncation on Python builds without automatic pointer promotion and enables reliable diagnostics via `ctypes.get_last_error()`.
- **Cancellation path.** `InterruptedError` raised from inside `WipeMethod.execute()` now propagates out instead of being caught by the generic `OSError` handler. The GUI and CLI no longer report a user-initiated cancel as "wipe failed".
- **Certificate counter concurrency.** `get_next_cert_number()` now takes a Windows file lock (`msvcrt.locking`) plus an in-process `threading.Lock`, preventing two concurrent batch wipes from being issued the same certificate number.
- **Windows platform check at startup.** `main.py` now exits cleanly on non-Windows platforms with a hint about nwipe / ShredOS, instead of crashing with `AttributeError: module 'ctypes' has no attribute 'windll'` during import.
- **CLI admin check** for the `wipe` subcommand. The CLI now fails fast with a clear "run as Administrator" message instead of surfacing a deep `CreateFileW` error once the wipe loop starts.
- **DIN 66399 pass count on certificate.** The PDF and CSV now report `wipe_result.passes` (the actual number of passes that ran, including any appended zero-blanking pass) instead of `wipe_method.passes` (the nominal count). Previously, BSI-VSITR wipes with verification enabled were under-reported as 7 passes when 8 had run.
- **`sectors_checked` in full verify mode.** The certificate no longer shows "Sectors Checked: 0" on full-mode scans; the sample-mode field is now only populated when sample verification actually ran.
- **Progress bar freezing at 98%.** The 100% progress callback guard was inverted; on drives whose size is an exact multiple of the block size, the final `100%` tick is now emitted correctly.
- **Progress callback spam.** The wipe loop now batches progress updates to every 50 MB instead of firing on every 1 MB block. Reduces GUI event queue pressure on large drives.
- **`full_verify` redundant `SetFilePointerEx`.** The verify hot loop no longer re-seeks before each `ReadFile` — Windows auto-advances the file pointer after a successful read. ~2-5% verify throughput improvement.
- **Zero block pre-allocation.** The full-verify inner loop no longer rebuilds `b"\x00" * block_size` for every iteration; the reference buffer is allocated once outside the loop.

### Tests

- 201 tests passing (up from 135 mid-cycle, 96 on the 1.0.x line). New: 9 format.py unit tests, 5 CLI reformat tests, 4 cert reformat tests, 3 GUI reformat tests + 2 new GUI regression tests, 6 reformat integration tests.
