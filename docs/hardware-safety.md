# Hardware safety: how StickShredder talks to drive firmware

Wiping a drive means writing sectors, like any other program does. For a few
features StickShredder also asks the drive's firmware directly: model and
security state (for hardware erase), hidden areas (HPA/DCO) and health
(SMART). These *drive commands* go past the file system straight to the
storage driver, so they follow the strict rules on this page.

## Why these rules exist

During development, a pre-release version of the read-only drive probe tried
one command family after another until a drive answered. On the development
PC this crashed Windows twice within seconds (stop code `0x139`
KERNEL_SECURITY_CHECK_FAILURE, parameter `0x1D`: corrupted kernel memory
pool). The probe had sent NVMe queries to a USB flash drive and to the system
NVMe drive, which runs on a vendor storage driver (Intel Rapid Storage
Technology with VMD) instead of Microsoft's NVMe driver. No crash dump could
be written, so the faulting driver is not known. Both requests were built
exactly as Microsoft documents them — the problem was where they went. The
fix is therefore a rule set that decides, before anything is sent, whether a
disk may receive drive commands at all.

## The rules

1. **The system disk never receives a drive command.** StickShredder never
   wipes the disk Windows runs or boots from, so it never needs to ask that
   disk anything either. A disk counts as a system disk when it holds the
   Windows folder or Windows reports it as boot or system disk (the same check
   that blocks wiping it). If the system disks cannot be determined, *no* disk
   receives drive commands.
2. **One path per disk, chosen before the first command.** StickShredder asks
   Windows which bus the disk is on and which driver serves it, then picks
   exactly one path (table below) — or none. It never tries a second path when
   the first one fails.
3. **Only Microsoft's in-box drivers receive firmware commands.** Vendor RAID
   and VMD drivers (for example Intel RST `iaStorVD`/`iaStorAC`, AMD RAID)
   present disks with the same bus types but handle these requests in their
   own code. Disks behind them get no drive commands.
4. **Read-only by construction**, except the explicitly enabled, experimental
   hardware erase. The SAT path refuses every ATA command that is not on a
   short read-only list before anything is sent.
5. **Every structure matches the Windows SDK.** See "How the code is checked".

## Choosing the path

| Windows reports for the disk | Port driver | Path | Drive commands |
|---|---|---|---|
| SATA or ATA bus | `storahci` (Microsoft AHCI) | `ata` | ATA commands via `IOCTL_ATA_PASS_THROUGH_DIRECT` |
| USB bus, device vendor id `ATA` | `USBSTOR` / `UASPStor` | `sat` | ATA commands wrapped in SCSI ATA PASS-THROUGH (16) |
| NVMe bus | `stornvme` (Microsoft NVMe) | `nvme` | NVMe Identify and health log queries |
| anything else, or any doubt | — | `none` | none |

"Anything else" includes USB flash drives and memory cards, USB enclosures
whose bridge reports its own chip maker as vendor (for example `ASMT` or
`JMicron`) instead of the SAT-standard `ATA`, NVMe drives in USB enclosures,
disks on vendor RAID/VMD drivers, SAS/SCSI/virtual disks, and every disk where
adapter and device report different bus types or a Windows answer is missing.

Before choosing, StickShredder only sends questions Windows' storage drivers
answer from what they already know (Explorer and Disk Management send the
same ones):

| Request | Answers |
|---|---|
| `IOCTL_STORAGE_GET_DEVICE_NUMBER` | which disk an open handle belongs to |
| `IOCTL_STORAGE_QUERY_PROPERTY` with `StorageAdapterProperty` / `StorageDeviceProperty` | bus type, vendor id, buffer alignment, transfer limit |
| `IOCTL_SCSI_GET_ADDRESS` + registry `HKLM\HARDWARE\DEVICEMAP\Scsi` | which port driver serves the disk (SATA and NVMe only) |

A system disk receives at most the first question, and only when StickShredder
was handed an already-open handle; the probe does not even open it.

## Every drive command StickShredder can send

### Path `ata` — `IOCTL_ATA_PASS_THROUGH_DIRECT`

| Command | Code | What it does | Used by |
|---|---|---|---|
| IDENTIFY DEVICE | `ECh` | reads model, serial number, size, security and sanitize support | probe, HPA/DCO check, hardware erase |
| READ NATIVE MAX ADDRESS EXT | `27h` | reads the full size including a Host Protected Area | probe, HPA/DCO check |
| DEVICE CONFIGURATION IDENTIFY | `B1h` / feature `C2h` | reads the factory size (Device Configuration Overlay) | probe, HPA/DCO check |
| SMART READ DATA | `B0h` / feature `D0h` | reads the health attribute table | probe |
| SECURITY SET PASSWORD, ERASE PREPARE, ERASE UNIT | `F1h`, `F3h`, `F4h` | **erases the drive** with its built-in Secure Erase | experimental hardware erase only, after explicit opt-in and confirmation |

### Path `sat` — `IOCTL_SCSI_PASS_THROUGH_DIRECT` with ATA PASS-THROUGH (16)

Only IDENTIFY DEVICE, DEVICE CONFIGURATION IDENTIFY and SMART READ DATA, as
28-bit PIO data-in commands. Any other ATA command is refused before a request
is built.

### Path `nvme` — `IOCTL_STORAGE_QUERY_PROPERTY`, protocol-specific

| Query | Property | What it reads |
|---|---|---|
| Identify Controller (CNS `01h`) | `StorageAdapterProtocolSpecificProperty` | model, serial number, firmware, sanitize and format capabilities |
| SMART / Health Information log (LID `02h`) | `StorageDeviceProtocolSpecificProperty` | percentage used, media errors |

Each query goes to the one property Microsoft documents for it; a refusal is
final (no retry with the other property). If Identify fails, the health log is
not requested.

### Access rights

The probe opens a disk *query-only* (neither read nor write access). NVMe
queries work on such a handle. Windows only accepts ATA and SCSI pass-through
on a handle opened for reading **and** writing, which needs administrator
rights; StickShredder sends them only on such a handle (an elevated wipe
session) and never tries them on a query-only handle.

## How the code is checked

- **Structure layout.** `tools/struct_check/struct_check.c` is compiled
  against the Windows SDK (10.0.26100, x64) and prints the size and field
  offsets of every structure StickShredder hands to Windows, plus every IOCTL
  code and constant it uses (`expected_x64.txt`). `tests/test_struct_layout.py`
  compares each Python structure, field, offset and constant with that output.
  The program only prints numbers; it opens no device.
- **Buffers.** Data buffers are page aligned (satisfying every adapter
  alignment requirement below 4 KiB; adapters asking for more get path
  `none`). Every length passed to Windows is taken from the buffer actually
  allocated. NVMe requests have exactly the size of Microsoft's samples, and
  every answer is validated (version, size, data offset, bytes returned)
  before it is read.
- **Simulated drives only.** The automated tests answer every request with
  simulated drives and check what was — and what was never — sent to each kind
  of disk. A test fixture blocks every real drive command, so no test can reach
  real hardware.

## Test status on real hardware

| Path | Status |
|---|---|
| `ata` — SATA disk on `storahci` | untested on real hardware |
| `sat` — ATA drive behind a USB bridge reporting vendor `ATA` | untested on real hardware |
| `nvme` — NVMe disk on `stornvme` | untested on real hardware |
| `none` | sends no drive commands |

All drive-command paths are untested on real hardware so far. Until a path is
confirmed, treat the features that use it — capability display, HPA/DCO check,
SMART, hardware erase — as beta. Overwrite wipes and their verification only
write and read sectors; they use none of these firmware commands.

The rules live in `src/wipe/transport.py`; the commands in
`src/wipe/passthrough.py` (ATA), `src/wipe/sat.py` (SAT) and
`src/wipe/nvme.py` (NVMe).
