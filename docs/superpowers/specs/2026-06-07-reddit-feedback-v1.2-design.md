# StickShredder v1.2 — Reddit-Feedback Update (Design Spec)

**Date:** 2026-06-07
**Author:** Robin Oertel (autonomous build by Claude)
**Branch:** `feature/v1.2-reddit-feedback`
**Status:** Approved scope ("Großer Wurf"); building autonomously.

---

## 1. Background

A Reddit thread on r/(German IT) reviewed StickShredder. Several technical
requests came out of it. v1.1 already shipped full read-back verification and
fixed the dead screenshot links. This update tackles the four remaining asks:

| # | Feature | Requested by | Decision |
|---|---------|--------------|----------|
| ① | Wipe **internal SATA disks**, not just USB | Leberkassemmel2 | Finish the started work + close 2 safety gaps |
| ② | **Bad-sector** detection + report on certificate | Leberkassemmel2 | Tolerate, count, report |
| ③ | **HPA/DCO** (hidden disk areas) detection | Leberkassemmel2 | **Detect + report only** (no removal) |
| ④ | **ATA Secure Erase** (firmware-level erase for SSD/flash) | Agile_Living6493 | **Experimental, opt-in, hidden by default** |

Two product decisions (made by Robin):
- **③ HPA/DCO:** detect and report on the certificate; do **not** unlock/remove
  the hidden area (lower risk, verifiable without special hardware).
- **④ Secure Erase:** ship as **experimental, opt-in**, hidden behind an explicit
  flag/toggle with a clear "untested on your hardware" warning. Never a default.

**Hard constraint:** This is a data-destruction tool. No feature may weaken the
existing "never wipe the Windows disk" guarantee. Where the tool cannot be
*certain* which physical disk hosts Windows, it must **refuse** (fail-safe),
not proceed.

**Testing reality:** No physical USB stick or spare disk is available right now,
and Windows Sandbox is disabled. Therefore ③ and ④ (which talk to real drive
firmware) are validated by (a) unit tests that mock the ctypes/IOCTL layer and
(b) full demo-mode simulation — **not** by real-hardware runs. They ship clearly
labelled experimental until hardware-verified.

---

## 2. Current architecture (relevant pieces)

- `wipe/device.py` — WMI enumeration → `DeviceInfo`; raw disk open/close;
  `_resolve_system_physical_drive_index()` system-disk guard; `open_physical_drive()`
  last-line-of-defence. **Gap:** only enumerates disks that have a drive letter
  (walks `Win32_LogicalDisk`); letterless/raw internal disks are invisible.
- `wipe/methods.py` — `WipeMethod` ABC, `execute()`, `_run_single_pass()`.
  `_write_block()` raises `OSError` on `WriteFile` failure → **one bad sector
  currently aborts the whole wipe.** `WipeResult` is the result DTO.
- `wipe/verify.py` — `sample_verify` / `full_verify`; `VerifyResult` already
  carries `error_count` + `mismatch_offsets`.
- `wipe/demo.py` — mirrors the real wipe on a temp file (the only path testable
  without hardware). **Everything new must be mirrored here.**
- `cert/generator.py` — `CertificateData` dataclass + `generate_certificate()`;
  section builders. New data → new optional fields + a new section builder.
- `cli.py` — `cmd_wipe` builds `cert_kwargs`, writes CSV, `_resolve_wipe_method`,
  argparse subcommands.
- `core/log.py` — `CSV_HEADERS` + `log_wipe_to_csv`.
- `gui/main_window.py`, `gui/wipe_worker.py` — Qt UI + worker thread.

All DTO changes use **optional fields with safe defaults** (the codebase already
does this for backward compat — see `CertificateData` v1.1 fields).

---

## 3. Feature designs

### ② Bad-sector tolerance + reporting  *(Phase A — first, safest, fully testable)*

**Behavior:** When a `WriteFile` fails on a block, do not abort. Record the byte
offset, seek to the next block, continue. Count total failed blocks + bytes; keep
the first N (=100) offsets. A wipe that completes with bad sectors is
`success=True` but carries `bad_sector_count > 0`; the certificate and CSV show a
clear warning. A configurable safety ceiling (`max_bad_fraction`, default e.g.
10% of blocks) flips the wipe to failure to avoid "successfully wiped a dead
drive" nonsense.

**Touch points:**
- `methods.py`: `_run_single_pass` gains tolerant write; `WipeResult` gains
  `bad_sector_count: int`, `bad_sector_offsets: list[int]`, `bad_sector_bytes: int`.
  New helper `_write_block_tolerant()` or a flag on `_write_block`.
- `verify.py`: already reports read errors — surface them similarly.
- `demo.py`: mirror; add a test seam to inject simulated bad blocks
  (e.g. an injectable "fail at offset" set) so it is unit-testable.
- `cert/generator.py`: `CertificateData` gains `bad_sector_count`,
  `bad_sector_offsets`, `bad_sector_bytes`; new "Bad Sectors / Defekte Sektoren"
  section (only rendered when count > 0), bilingual, offsets hex-formatted.
- `cli.py`: show bad-sector count in summary; pass to cert; CSV.
- `core/log.py`: `CSV_HEADERS` += `bad_sectors`.
- GUI: show bad-sector count in the result.

**Tests:** demo wipe with injected bad blocks → asserts count/offsets; cert
renders the section; ceiling flips to failure; zero bad sectors → no section.

### ① Finish internal-disk support  *(Phase B)*

**Behavior:** Internal SATA/NVMe disks become wipe-able, but only behind an
explicit opt-in and with loud warnings. The Windows disk stays hard-blocked.

**Close the two gaps:**
1. **Letterless enumeration.** Also walk `Win32_DiskDrive` directly (not only
   lettered logical disks) so internal data disks / raw disks appear. Merge with
   the letter-based map; dedupe by physical index. Disks with no letter get
   `drive_letter=""`, `filesystem="RAW"`.
2. **Fail-safe guard.** If `_resolve_system_physical_drive_index()` returns
   `None` (cannot identify the Windows disk) **and** the target is internal/
   non-removable, **refuse**. Also extend the guard to cover the disk holding the
   active/boot/EFI system partition, not just the one with the `C:` letter.

**Opt-in + friction (internal, non-removable, non-system only):**
- CLI: new `--allow-internal` flag; without it, internal disks are listed but
  refused for wiping. With it: SSD wear-leveling warning + extra confirmation
  (already partly present) + require typing the **device model** (not just
  `DELETE`) for internal disks.
- GUI: internal disks shown in a distinct warning style; a checkbox
  "Interne Festplatten anzeigen/erlauben" gated with a warning dialog.
- BitLocker / open-handle warnings already exist — keep and surface.

**Touch points:** `device.py` (enumeration + guard), `cli.py` (flag + friction),
`gui/*` (toggle + styling), tests.

**Tests:** mock WMI to present a letterless internal disk → appears; system disk
index unresolved + internal target → refused; sibling partition on system disk →
flagged (already covered); USB on a different disk → safe (already covered).

### ③ HPA/DCO detection (report-only)  *(Phase C)*

**Behavior:** Before wiping, probe the drive for a Host Protected Area (HPA) and
Device Configuration Overlay (DCO). If the native max address exceeds the
accessible max, hidden sectors exist → record and **report on the certificate as
a warning** ("Achtung: X versteckte Sektoren (HPA/DCO) gefunden — diese werden
durch Überschreiben NICHT erreicht; physische Vernichtung erwägen"). Do **not**
modify the drive.

**New module `wipe/hidden_areas.py`** (agent-built):
- `detect_hidden_areas(handle, device_id) -> HiddenAreaInfo` using ATA
  `IDENTIFY DEVICE` + `READ NATIVE MAX ADDRESS (EXT)` and DCO
  `DEVICE CONFIGURATION IDENTIFY`, via `wipe/passthrough.py`.
- `HiddenAreaInfo`: `supported: bool`, `hpa_present: bool`, `hpa_hidden_sectors: int`,
  `dco_present: bool`, `dco_hidden_sectors: int`, `accessible_max: int`,
  `native_max: int`, `error: str | None`. Never raises; returns `supported=False`
  on any failure (USB bridges often block ATA passthrough).

**Touch points:** `cert/generator.py` (fields + warning section), `cli.py`
(probe + display), `demo.py` (simulate a drive with/without HPA), `core/log.py`
(CSV `hidden_area`), GUI (warning line). CSV/cert only.

**Tests:** mocked passthrough returning native>accessible → detected; USB/no
support → `supported=False`, no crash, cert omits or notes "not probed".

### ④ ATA Secure Erase (experimental, opt-in)  *(Phase D)*

**Behavior:** A wipe "method" that issues the drive's own erase command instead
of overwriting: SATA → `SECURITY SET PASSWORD` + `SECURITY ERASE UNIT`
(enhanced if supported); NVMe → `Format NVM` / sanitize. Hidden behind an
explicit opt-in (`--method secure-erase` requires `--experimental`, or a GUI
"experimental features" toggle) with a prominent "untested on your hardware,
may brick the drive, no progress bar" warning. Never offered as a default.

**New module `wipe/secure_erase.py`** (agent-built):
- `secure_erase(handle, device_id, enhanced=False, progress=None) -> SecureEraseResult`
  via `wipe/passthrough.py`. Detects support via IDENTIFY (SECURITY feature set;
  whether frozen). If frozen/unsupported → return a clear unsupported result,
  do nothing.
- `SecureEraseResult`: `success`, `method` ("ata-secure-erase"/"ata-enhanced"/
  "nvme-format"/"unsupported"/"frozen"), `duration_seconds`, `error`,
  `supported`, `frozen`.
- Integrate into the certificate as the wipe method (`wipe_method="ATA Secure
  Erase (experimental)"`, passes shown as N/A, a clear note that this is a
  firmware command not a verified overwrite). Verification semantics differ —
  after secure erase, sample/full verify can still read back zeros if requested.

**Shared foundation — new module `wipe/passthrough.py`** (agent-built, dependency
of ③ and ④):
- Thin wrapper over `IOCTL_ATA_PASS_THROUGH_DIRECT` (ATA) and
  `IOCTL_SCSI_PASS_THROUGH_DIRECT` (NVMe/USB) with correct ctypes structures,
  explicit `argtypes/restype`, `use_last_error`, 64-bit-safe.
- `identify_device(handle) -> IdentifyData | None`; `send_ata_command(...)`;
  helpers to read the IDENTIFY words we need (LBA48 max, security status, etc.).
- Never raises across the public surface; returns `None`/typed errors so callers
  degrade gracefully (USB bridges frequently reject passthrough).

**Touch points:** `cli.py` (method + `--experimental` gate), `gui/*` (gated
toggle), `cert/generator.py`, `demo.py` (simulate erase), `core/log.py`.

**Tests:** mocked passthrough — supported drive erases; frozen drive →
"frozen"; unsupported/USB → "unsupported"; demo path returns success and zeros.

---

## 4. Build order (waves)

Each phase ends green (full `pytest`) and is committed separately on the branch.

- **Phase A — Bad sectors** (controller edits existing files): methods, demo,
  cert, cli, csv, tests. *Highest value-to-risk; fully testable.*
- **Phase B — Internal disks** (controller): device enumeration + fail-safe
  guard, cli flag + friction, gui toggle, tests.
- **Wave 1 (parallel agents, Opus, new files only):**
  - Agent P → `wipe/passthrough.py` + `tests/test_passthrough.py`
  - Agent H → `wipe/hidden_areas.py` + `tests/test_hidden_areas.py` (HPA/DCO)
  - Agent S → `wipe/secure_erase.py` + `tests/test_secure_erase.py`
  (H and S code against the passthrough interface defined in §3/④; controller
  reconciles the real interface during integration.)
- **Phase C+D integration** (controller): wire hidden_areas + secure_erase into
  cli, gui, cert, demo, csv; gate secure-erase behind `--experimental`; tests.
- **Phase E — Docs**: README (new sections + sharpen DBAN/nwipe comparison +
  TRIM note), CHANGELOG, version bump to **1.2.0** (`pyproject.toml`, installer).

Agents must be told: **plain commit message, no Co-Authored-By, no AI
attribution** — but agents here only create files; the controller commits.

## 5. Out of scope (explicitly)

- HPA/DCO **removal** (decided report-only).
- Secure Erase as a default/first-class method (decided experimental-only).
- Non-Windows platforms.
- Real-hardware validation of ③/④ (no hardware available; deferred, labelled).

## 6. Definition of done

- `pytest` green (target: existing 218 + new tests, all passing).
- Demo mode mirrors every new behavior (Robin's only manual test path).
- Certificate renders new sections correctly (bad sectors, HPA/DCO, secure erase).
- README + CHANGELOG updated; version 1.2.0.
- Nothing pushed to GitHub — left on the branch for Robin's review.
- A wake-up summary documenting decisions, experimental caveats, and the
  hardware-testing TODO.
