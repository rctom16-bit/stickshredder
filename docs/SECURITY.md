# Security Considerations / Sicherheitshinweise

This document provides an honest assessment of StickShredder's security capabilities and limitations. It is intended for IT administrators evaluating the tool for use in their organization.

Dieses Dokument enthält eine ehrliche Bewertung der Sicherheitsfähigkeiten und -einschränkungen von StickShredder. Es richtet sich an IT-Administratoren, die das Tool für den Einsatz in ihrer Organisation evaluieren.

---

## Table of Contents

- [Software Limitations](#software-limitations)
- [SSD and Flash Wear Leveling](#ssd-and-flash-wear-leveling)
- [HDD vs. SSD: Effectiveness of Overwrite Methods](#hdd-vs-ssd-effectiveness-of-overwrite-methods)
- [Not a Substitute for Physical Destruction](#not-a-substitute-for-physical-destruction)
- [DEKRA / DIN Certification Disclaimer](#dekra--din-certification-disclaimer)
- [When to Use Commercial Tools Instead](#when-to-use-commercial-tools-instead)
- [Responsible Disclosure](#responsible-disclosure)
- [Contact](#contact)

---

## Software Limitations

StickShredder is a **software-based wipe tool**. This means it operates within the constraints of the host operating system and the device's storage controller. It does not have direct access to the physical storage medium at the hardware level.

Key implications:

- **Operating system dependency:** StickShredder relies on the Windows storage stack to address sectors on the target device. If the OS cannot see a sector, StickShredder cannot overwrite it.
- **Controller abstraction:** Modern storage devices (especially SSDs and flash drives) place a controller between the host and the physical storage cells. The controller may remap, reserve, or hide sectors in ways that are invisible to software running on the host.
- **Device firmware:** The behavior of the storage device depends on its firmware. StickShredder has no ability to verify firmware behavior, detect hidden partitions managed by the controller (HPA, DCO), or bypass firmware-level protections.
- **Functional device required:** The target device must be operational and writable. StickShredder cannot wipe a device that is physically damaged, read-only, or otherwise non-functional.
- **No verification of physical erasure:** StickShredder verifies that overwrite operations complete without I/O errors. It cannot verify that the underlying physical cells have actually been overwritten, as this would require hardware-level access that is not available through standard OS interfaces.

---

## SSD and Flash Wear Leveling

This section explains why overwrite-based wipe methods have inherent limitations on SSDs and flash-based USB drives. This is not a limitation of StickShredder specifically -- it applies to all software-based overwrite tools.

### How Wear Leveling Works

Flash memory cells (NAND) have a limited number of write/erase cycles before they wear out. To extend the lifespan of the device, SSD and flash drive controllers implement **wear leveling**: an algorithm that distributes write operations across all available cells as evenly as possible, rather than writing repeatedly to the same physical locations.

When the host operating system writes data to logical sector N, the controller may physically store that data in any available cell. The controller maintains an internal mapping table (Flash Translation Layer, or FTL) that translates logical addresses to physical locations. This mapping changes dynamically with every write operation.

### Why This Matters for Secure Wiping

When StickShredder overwrites logical sector N with zeros or random data, the controller may write the new data to a **different physical cell** than the one that previously held the original data. The original cell may be:

- **Marked as "stale" but not erased:** The controller knows the data is no longer current, but the physical cells still contain the original data until the controller's garbage collection process erases them.
- **Moved to the over-provisioned area:** SSDs and some flash drives reserve a percentage of their total capacity (typically 7-28%) as over-provisioning. These cells are not addressable by the host and are used by the controller for wear leveling, garbage collection, and bad block replacement. Data remnants in this area are inaccessible to software-based wiping.
- **Remapped due to bad block management:** If a cell is detected as failing, the controller remaps it to a spare cell. The data in the original (now-bad) cell may remain but is no longer addressable by the host.

### Over-Provisioning in Detail

Over-provisioning refers to the difference between the raw flash capacity and the user-addressable capacity of an SSD or flash drive. For example, a "256 GB" SSD might contain 280 GB of raw NAND flash, with 24 GB reserved for the controller's internal use.

This reserved space is used for:

- **Wear leveling buffers:** Temporary storage for data being relocated
- **Garbage collection workspace:** Space for the controller to consolidate and erase blocks
- **Bad block replacement:** Spare cells to replace failing ones
- **Performance optimization:** Free blocks for write operations without waiting for erase cycles

Data fragments stored in the over-provisioned area are **completely inaccessible** through standard host interfaces. No amount of software-based overwriting can reach this space.

### Controller-Managed Block Remapping

Flash memory is organized in pages (typically 4-16 KB) and blocks (typically 256 KB - 4 MB). The controller can only erase data at the block level, but it writes at the page level. When updating data, the controller writes new pages to a clean block and marks the old pages as invalid. The old data persists until the entire block is erased during garbage collection.

This means that at any given time, the device may contain multiple physical copies of data that has been "overwritten" from the host's perspective. The number and duration of these remnants depends on the controller's firmware implementation, which varies between manufacturers and models and is generally not documented publicly.

### Practical Impact

For a typical 32 GB USB flash drive:

- Approximately 2-4 GB may be reserved as over-provisioning (not addressable by the host)
- An unknown number of remapped bad blocks may contain data remnants
- The controller's garbage collection behavior is unpredictable and firmware-dependent

**Conclusion:** Software-based overwriting on SSDs and flash drives provides a significant barrier to casual data recovery but **cannot guarantee** that all physical copies of the data have been eliminated. For high-sensitivity data, this is not sufficient.

### Recommendations for Flash/SSD Media

For data requiring guaranteed destruction on SSD or flash media:

1. **ATA Secure Erase / NVMe Format:** These are firmware-level commands that instruct the controller to erase all cells, including over-provisioned and remapped areas. Not all devices implement these commands correctly or at all. Support for ATA Secure Erase is planned for a future version of StickShredder.

2. **Cryptographic Erase (if supported):** Some self-encrypting drives (SEDs) support cryptographic erase, which destroys the encryption key, rendering all data on the drive unrecoverable. This is instantaneous and effective, but only available on devices with hardware encryption.

3. **Physical Destruction:** Shredding, disintegration, or incineration of the storage device. This is the only method that provides absolute certainty. Use a DIN 66399-certified destruction service for documented disposal.

---

## HDD vs. SSD: Effectiveness of Overwrite Methods

### Traditional Hard Drives (HDDs)

On traditional magnetic hard drives, data is stored on rotating platters in a predictable, linearly addressable manner. When the host writes to sector N, the data is physically written to a specific location on the platter. Overwriting sector N replaces the magnetic signal at that exact physical location.

**For HDDs, software-based overwriting is effective.** A single-pass overwrite (e.g., Zero-Fill) is sufficient to prevent recovery with standard software tools. Multi-pass overwrites (e.g., 3-Pass Random, BSI VSITR) provide additional assurance against advanced forensic recovery techniques, though modern research suggests that even a single pass is sufficient on modern high-density HDDs.

StickShredder's wipe methods are **fully effective** on traditional HDDs.

### Solid-State Drives (SSDs) and Flash Drives

As described in the [previous section](#ssd-and-flash-wear-leveling), overwrite methods have inherent limitations on SSDs and flash-based devices due to wear leveling, over-provisioning, and controller-managed block remapping.

StickShredder's wipe methods provide **partial protection** on SSDs and flash drives:

- They overwrite all user-addressable sectors, which eliminates data from the logical address space
- They prevent recovery with standard software-based forensic tools in most cases
- They **cannot guarantee** elimination of data remnants in over-provisioned areas, remapped blocks, or controller-managed reserved space

### Summary Table

| Aspect | HDD | SSD / Flash |
|---|---|---|
| Overwrite reaches all data | Yes | No (over-provisioning, remapping) |
| Single-pass sufficient | Yes (for most use cases) | No guarantee |
| Multi-pass adds security | Marginal (on modern drives) | Does not address fundamental limitation |
| Suitable for Schutzklasse 1 | Yes | Yes (with caveats) |
| Suitable for Schutzklasse 2 | Yes | Use ATA Secure Erase or physical destruction |
| Suitable for Schutzklasse 3 | Physical destruction recommended | Physical destruction required |

---

## Not a Substitute for Physical Destruction

StickShredder is a software-based tool. It **cannot and does not** replace physical destruction of data carriers for the following scenarios:

- **Schutzklasse 3 (Very High Protection):** The minimum security level H-5 requires physical destruction or equivalent measures that are beyond the capability of software-based tools.
- **High-sensitivity SSD/flash data:** Where absolute certainty of data elimination is required on flash-based media.
- **Defective or non-functional devices:** Devices that cannot be written to cannot be wiped by software.
- **Regulatory or contractual mandates:** Where laws, contracts, or industry standards explicitly require physical destruction with certified documentation.

For physical destruction, use a DIN 66399-certified destruction service that provides an official destruction certificate (Vernichtungsnachweis). This is fundamentally different from the deletion certificates generated by StickShredder.

---

## DEKRA / DIN Certification Disclaimer

**StickShredder is not certified by DEKRA, DIN, BSI, TUV, or any other certification body.**

The deletion certificates generated by StickShredder are structured according to the terminology and conventions of DIN 66399 / ISO 21964. They reference Schutzklassen (Protection Classes), Sicherheitsstufen (Security Levels), and media types as defined in the standard. This is done to provide a useful, standards-aware documentation format that aligns with the terminology familiar to German IT administrators.

However, these certificates:

- Do **not** constitute proof of DIN 66399 compliance
- Have **not** been validated by any official certification body
- Do **not** carry the legal weight of certificates issued by certified destruction services
- Are generated by open-source software provided without warranty

The certificates are intended as internal documentation tools for organizations that want to record their data disposal processes in a structured, standards-referenced format. They are not intended to replace certified processes where such certification is legally required.

---

## When to Use Commercial Tools Instead

Consider using a commercially certified wipe tool or destruction service in the following situations:

1. **Legal mandate:** Your organization is subject to regulations that explicitly require the use of certified data destruction tools or services (e.g., certain banking, healthcare, or government regulations).

2. **Contractual obligation:** Contracts with clients, partners, or regulators require certified proof of data destruction.

3. **Audit requirements:** Your organization is subject to audits (ISO 27001, SOC 2, BSI IT-Grundschutz) where certified data destruction documentation is expected.

4. **Schutzklasse 3 data:** Data classified at the highest protection level should always be handled with certified tools and/or physical destruction.

5. **Liability concerns:** If improper data destruction could expose your organization to significant legal or financial liability, the cost of certified tools (typically EUR 200-500/year) is a reasonable investment.

6. **SSD/flash with sensitive data:** For guaranteed erasure of SSDs, look for tools that support ATA Secure Erase with vendor-level verification.

Commercial tools in this space include Blancco Drive Eraser, Certus Erasure, and others that hold relevant certifications. This is not an endorsement of any specific product.

---

## Responsible Disclosure

If you discover a security vulnerability in StickShredder -- for example, a scenario where data is not being overwritten as expected, a flaw in the certificate generation process, or any other issue that could compromise the security of the wipe operation -- please report it responsibly.

### How to Report

1. **Do not** open a public GitHub issue for security vulnerabilities
2. Send a detailed report via email to the address listed in the [Contact](#contact) section below
3. Include:
   - A description of the vulnerability
   - Steps to reproduce
   - The affected version of StickShredder
   - Your assessment of the severity and impact
4. Allow a reasonable time (90 days) for the issue to be addressed before public disclosure

### What Qualifies as a Security Issue

- Data not being overwritten correctly (sectors skipped, patterns not applied)
- System drive protection bypass (wipe of a system drive becoming possible)
- Certificate forgery or manipulation vulnerabilities
- Privilege escalation through StickShredder
- Any behavior that could lead to incomplete wipe without user awareness

### What Does Not Qualify

- The inherent SSD/flash wear leveling limitation (this is documented and expected)
- Feature requests for additional wipe methods
- General software bugs that do not impact the security of the wipe operation

---

## Contact

- **Author:** Robin Oertel
- **Email:** roertel16@gmail.com
- **GitHub:** https://github.com/roertel16/stickshredder
- **Security reports:** roertel16@gmail.com (subject line: "StickShredder Security Report")

For general questions and feature requests, please use the GitHub issue tracker.

---

*This document is provided for informational purposes. StickShredder is open-source software provided under the MIT license, without warranty of any kind, express or implied. See the [LICENSE](../LICENSE) file for full terms.*
