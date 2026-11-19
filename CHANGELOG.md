# Changelog

All notable changes to StickShredder are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-11-19

Driven by the community feedback on 1.x: a certificate you can check, honest
results, internal disks, hidden areas and bad sectors, and a hardware erase
for SSDs. Includes everything from 1.1.1 and 1.1.2. (The 1.2.0 version
prepared in June was never released; its work is part of 2.0.)

### Added

- **Signed certificates.** Every certificate is signed (Ed25519) with a key
  StickShredder creates for the installation on first use and stores protected
  by Windows (DPAPI). The certificate carries an "Echtheit / Authenticity"
  section with a QR code, the verification code and the key's fingerprint, an
  overall result line, and its data as a PDF attachment
  (`stickshredder-certificate.json`).
- **Verify page** (`docs/verify/`, served at
  `https://rctom16-bit.github.io/stickshredder/verify/`): checks a certificate
  from its QR code, verification code or PDF entirely in the browser — nothing
  is uploaded. English and German, light and dark, works on phones.
- **Key card:** a one-page PDF with company, fingerprint and QR code to file
  with the IT documentation or hand to auditors. Scanning its QR code adds the
  key as trusted on the verify page.
- **Three verify levels** everywhere (GUI, CLI, verify page): genuine from a
  trusted key / signature valid but key unknown / invalid or altered.
- **Tamper-evident wipe history:** each entry carries a hash over the previous
  one; the newest hash is written into every signed certificate. Older history
  files are migrated (with a backup).
- **New CLI commands:** `verify-cert`, `keycard`, `trust add/list/remove`,
  `key export/import` (password-protected file), `history --verify`,
  `info --device`, and `unlock`.
- **Recommendations:** `stickshredder info --device` and the GUI pick the
  procedure by device type (HDD, SSD, USB stick / SD card): one pass of zeros
  plus full verification, with the NIST SP 800-88 category and warnings (SSD
  spare areas; protection class 3 → destroy physically). It reads Windows'
  device information only. `wipe` without `--method` uses the recommendation.
- **Three-level result:** Successful / With limitations / Failed — the same in
  the GUI, CLI, history and certificate. `wipe` exits `0`, `3` or `1`.
- **NIST SP 800-88 category** on the certificate next to the DIN 66399 level
  ("Clear" for overwriting, "Purge" for a hardware erase).
- **Internal disks:** physical disks without a drive letter are listed and can
  be wiped after an explicit opt-in (GUI "Interne Festplatten erlauben", CLI
  `--allow-internal`, plus a model-name confirmation in the CLI).
- **Bad sectors:** a sector that cannot be written because of a media error is
  skipped, counted and listed on the certificate and in the history instead of
  stopping the wipe. More than 10 % unwritable still fails the wipe.
- **Hidden areas (HPA/DCO):** directly connected SATA disks are asked before
  the wipe whether part of them is hidden from Windows. Findings are reported
  on the certificate and in the history; nothing is changed on the disk.
- **Hardware erase (Beta)** for SATA SSDs connected directly: the SSD erases
  itself (ATA Secure Erase, enhanced when offered), then one pass of zeros and
  a full verification. Behind „Beta-Funktionen aktivieren“ in Optionen (GUI) or
  `--method secure-erase --experimental` (CLI). The drive is asked first and
  the erase stops before anything is written if it is frozen, locked, already
  has a password or does not support it. The temporary password is shown and
  logged before it is set; the PC is kept awake. **Not tested on real
  hardware yet.**
- **Unlock** („SSD entsperren …“, `stickshredder unlock --experimental`):
  removes StickShredder's temporary password from a drive an interrupted
  hardware erase left locked. One try only.
- **New window (design "Signal"):** pages Löschen, Zertifikate, Schlüssel and
  Optionen; light and dark theme (follows Windows unless chosen); German and
  English; the disk list refreshes when a USB device is plugged in or removed;
  certificate preview with the real QR code; typed LÖSCHEN/DELETE confirmation.
- **Zertifikate page:** history with search, open PDF/folder, CSV export, chain
  status, and „Zertifikat prüfen“. **Schlüssel page:** fingerprint, key card,
  trusted keys (add from a key-card PDF, rename, remove). **Optionen page:**
  company data, logo, certificate folder and language, wipe defaults, theme,
  language, Beta switch, demo mode.
- **Demo mode** for the hardware erase: a demo SSD that pretends to erase
  itself.
- `docs/hardware-safety.md`: which drive commands StickShredder sends, on which
  path, and why.

### Changed

- **Full verification is the default** (CLI, GUI, settings); the default
  method is one pass of zeros. The GUI offers full or sample verification.
- **3-pass and 7-pass methods** stay selectable but are never recommended;
  choosing them shows that they add time, not security.
- `--method` is optional for `wipe`. `--enhanced` is kept for compatibility
  and no longer needed.
- **Only media errors count as bad sectors.** "Access denied" (Windows mounted
  the disk again) and every other write error stop the wipe as failed.
- The history's `result` column records `OK` / `LIMITED` / `FAILED`; older
  `SUCCESS` / `FAILED` entries are still read correctly.
- The connection type comes from Windows' storage bus type; SD, MMC and
  external hard-disk media count as external.
- Version 2.0.0.

### Security

- **Drive commands follow strict rules:** one path per disk chosen from bus
  type and driver, only Microsoft's in-box drivers (`storahci`, `stornvme`),
  never the system disk, never a second path after a failure. USB bridges get
  read-only queries only. (A pre-release probe that tried command families one
  after another crashed Windows on the development PC; this never shipped.)
- The pre-write safety check now also runs before a hardware erase, before an
  unlock and before "reformat after wipe", and accepts disks without a drive
  letter. The reformat script refuses boot/system disks before `Clear-Disk`.
- The system-disk guard fails safe for internal disks: if the Windows disk or
  the boot layout cannot be read, every internal disk is refused. An MBR
  "active" partition alone does not make a disk a system disk, so bootable USB
  installer sticks stay wipeable.
- The private signing key is never shown; it leaves the PC only as a
  password-protected export.
- Hardware erase: a drive that already has a password is refused before
  anything is set; after the drive rejects the unlock password the unlock is
  not offered again for that disk in the session; the window cannot be closed
  while an SSD erases itself or is being unlocked.
- A disk is refused when one of its volumes also lies on another disk (it is
  never force-dismounted), and the disk's identity is checked again after its
  volumes are locked, so a stick swapped at the last moment is never wiped.

### Fixed

- **Disks made with Rufus can be wiped.** Rufus adds a small read-only
  partition („UEFI_NTFS“) at the end of the disk; Windows refused every write
  into it, so wipes stopped at about 99.99 % (no certificate was issued).
  StickShredder now removes the partition table right before overwriting, so
  no partition can block writes or be mounted again during the wipe. If
  Windows still blocks a write, the message says so plainly. Affects 1.1.x as
  well.
- Serial numbers that are empty or all zeros (many USB enclosures, e.g.
  Realtek RTL9210B, report `0000000000000000`) show „vom Gerät nicht
  geliefert“ / "not reported by the device" in the window, the certificate,
  the history and the CLI. The signed certificate keeps the raw value.
- "Reformat after wipe" writes its start, success or failure (with the
  reason) to the audit log.

- A USB enclosure using UAS (e.g. Realtek RTL9210B) was shown as an internal
  NVMe disk.
- Missing administrator rights are named as such when a volume cannot be
  opened (Win32 error 5).
- **The device list is fast again** (about 1 s instead of about 5 s per
  drive). BitLocker is now detected without administrator rights.
- `list` counts disks, not drive letters.
- **Volumes without a drive letter, or mounted in a folder,** are dismounted
  and locked too before a wipe, like lettered volumes.
- **Reformat after wipe** waits until the drive is released. A failed reformat
  never changes the wipe result or the certificate.
- **SSD or USB stick with protection class 3:** the Löschen page, the CLI and
  the certificate say that overwriting cannot reach the reserve areas and that
  the medium has to be destroyed physically — also after a hardware erase.
- The certificate's QR code scans reliably (modules at least 0.5 mm), and its
  sections are no longer split across pages.
- **BitLocker disks can be wiped in the CLI as in the GUI,** with a warning
  that all data will be irrevocably deleted.
- The demo result shows the device name.
- The certificate says „aufgebaut nach DIN 66399 / ISO 21964“ and „nicht von
  DEKRA, DIN oder einer anderen Stelle zertifiziert“.

### Removed

- The old main window and the options, history and settings dialogs (replaced
  by the new pages).
- The 1.x screenshots in `docs/screenshots/`.

## [1.1.2] - 2026-11-18

### Fixed

- **Wipes could fail after a few seconds with "WriteFile failed with error code 5".** StickShredder dismounted the drive's volumes and then released them right away. As soon as anything touched the drive (e.g. Explorer), Windows mounted the file system again and refused every further raw write into it. The volumes now stay locked until wipe and verification are finished. If a volume is in use, the dismount is forced and the lock taken afterwards; if it still cannot be locked, the wipe is refused before anything is written. Found in a real wipe of a 64 GB USB stick; no certificate was issued for the failed attempt.
- The CLI no longer sends a volume lock to the physical-drive handle (meant for volumes; it could stall some USB controllers). The GUI already skipped it.

## [1.1.1] - 2026-11-17

### Security

- **Every partition of the Windows disk is now blocked, not just `C:`.** Previously the system guard compared drive letters only. A second partition on the same physical disk (e.g. a `D:` data partition) was listed as "INTERNAL" and could be selected; wiping it writes to the whole physical disk from sector 0 and destroys the Windows installation. The guard now works per physical disk. If the Windows disk cannot be determined, all non-USB disks are blocked.
- **Identity re-check right before writing.** The disk behind `\\.\PhysicalDriveN` is re-read immediately before it is opened; if serial number, model or size changed (e.g. a stick was swapped after the list was refreshed) or it is now the system disk, the wipe is refused.
- **All volumes of the target disk are dismounted**, not only the selected drive letter. If one cannot be dismounted, the wipe is refused instead of continuing with a warning. A failed volume lock no longer blocks the (forced) dismount.
- **The disk the PC booted from is blocked too**, even when it is not the disk Windows is on (e.g. the EFI partition of an older install on a second internal disk). Uses the Windows storage stack's boot/system flags.
- The drive letters of the target disk are re-read right before dismounting, so a partition mounted after the list was refreshed is dismounted as well, and a letter that moved to another disk is left alone.
- **Selecting two partitions of the same disk wipes it once**, not twice. The CLI confirmation now names every drive letter on the disk, not just the one passed to `--device`.
- Model and serial number are HTML-escaped in the confirmation dialog.

### Fixed

- Window title, About dialog and the certificate footer showed version 1.0.0. The version now comes from a single source.
- Missing umlauts in the GUI ("Erkannte Datenträger", "Löschoptionen").
- Device scan printed `UnicodeDecodeError` tracebacks on German Windows: `manage-bde` / `handle.exe` output is now decoded with the console (OEM) code page.
- README Quick Start CLI examples used flags that do not exist (`--list`, `--drive`, `--cert-output`, `random3`, `vsitr`). They now match the real commands.
- README build command pointed at `installer/stickshredder.spec`; the spec file is in the repository root.

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
