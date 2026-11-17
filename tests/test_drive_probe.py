"""Tests for wipe.drive_probe — one drive-command path per disk, chosen up front.

``CreateFileW`` and ``DeviceIoControl`` are faked for every module involved
(transport, passthrough, sat, nvme). A ``FakeDrive`` answers the benign
storage-stack queries like a real SATA disk, SAT bridge, NVMe SSD or plain
USB stick would, plus whatever firmware commands that disk understands.
Every IOCTL and every ATA command is recorded, so the tests can prove what
was — and what was never — sent. Nothing here touches real hardware.
"""

from __future__ import annotations

import ctypes
import struct

import pytest

import wipe.drive_probe as dp
import wipe.nvme as nvme
import wipe.passthrough as pt
import wipe.sat as sat
import wipe.smart as smart
import wipe.transport as tr
from tests.test_nvme import make_health_log, make_identify_controller
from tests.test_sat import make_identify
from tests.test_smart import make_smart_table
from tests.test_transport import (
    FAKE_HANDLE,
    FakeStack,
    make_adapter_descriptor,
    make_device_descriptor,
)

GENERIC_WRITE = 0x40000000

ATA_IOCTL = pt.IOCTL_ATA_PASS_THROUGH_DIRECT
SCSI_IOCTL = sat.IOCTL_SCSI_PASS_THROUGH_DIRECT
QUERY_IOCTL = tr.IOCTL_STORAGE_QUERY_PROPERTY
BENIGN = {tr.IOCTL_STORAGE_GET_DEVICE_NUMBER, QUERY_IOCTL, tr.IOCTL_SCSI_GET_ADDRESS}
PROTOCOL_PROPERTIES = {tr.StorageAdapterProtocolSpecificProperty,
                       tr.StorageDeviceProtocolSpecificProperty}

# Every ATA (command, features) the probe may send — all of them only read.
READ_ONLY_ATA = {(0xEC, 0x00), (0x27, 0x00), (0xB1, 0xC2), (0xB0, 0xD0)}


def _dco_image(max_lba: int) -> bytes:
    words = [0] * 256
    words[0] = 0x0002
    for i in range(4):
        words[3 + i] = (max_lba >> (16 * i)) & 0xFFFF
    return struct.pack("<256H", *words)


class FakeDrive(FakeStack):
    """One disk: storage-stack answers plus the firmware commands it knows.

    ``ata`` / ``sat_cmds`` map (command, features) → response (bytes, or an
    int native max LBA for 0x27); a missing key means the drive aborts.
    ``nvme_data`` maps (data type, request value) → payload. ``None`` for a
    whole transport means its IOCTL fails.
    """

    def __init__(self, *, ata=None, sat_cmds=None, nvme_data=None, **stack):
        super().__init__(other=self._firmware, **stack)
        self.ata = None if ata is None else dict(ata)
        self.sat = None if sat_cmds is None else dict(sat_cmds)
        self.nvme = None if nvme_data is None else dict(nvme_data)
        self.ata_commands: list[tuple[str, int, int]] = []
        self.protocol_queries: list[tuple[int, int, int]] = []

    def _firmware(self, handle, ioctl, in_addr, in_size, out_addr, out_size, bytesret, ov):
        if ioctl == ATA_IOCTL:
            return self._ata(in_addr)
        if ioctl == SCSI_IOCTL:
            return self._sat(in_addr)
        if ioctl == QUERY_IOCTL:
            return self._nvme(in_addr, out_addr, out_size, bytesret)
        return self._fail()

    def _ata(self, addr):
        aptd = pt.ATA_PASS_THROUGH_DIRECT.from_address(addr)
        cmd, feat = aptd.CurrentTaskFile[6], aptd.CurrentTaskFile[0]
        self.ata_commands.append(("ata", cmd, feat))
        if self.ata is None:
            return self._fail(5)
        resp = self.ata.get((cmd, feat))
        if resp is None:
            aptd.CurrentTaskFile[6] = 0x51  # ERR: command aborted
            aptd.CurrentTaskFile[0] = 0x04
            return 1
        if isinstance(resp, int):  # READ NATIVE MAX ADDRESS EXT
            for i in range(3):
                aptd.CurrentTaskFile[2 + i] = (resp >> (8 * i)) & 0xFF
                aptd.PreviousTaskFile[2 + i] = (resp >> (24 + 8 * i)) & 0xFF
        elif aptd.DataBuffer:
            ctypes.memmove(aptd.DataBuffer, resp, len(resp))
        aptd.CurrentTaskFile[6] = 0x50
        return 1

    def _sat(self, addr):
        sptd = sat.SCSI_PASS_THROUGH_DIRECT.from_address(addr)
        cmd, feat = sptd.Cdb[14], sptd.Cdb[4]
        self.ata_commands.append(("sat", cmd, feat))
        if self.sat is None:
            return self._fail(5)
        resp = self.sat.get((cmd, feat))
        if resp is None:
            sptd.ScsiStatus = 0x02  # CHECK CONDITION
            return 1
        ctypes.memmove(sptd.DataBuffer, resp, len(resp))
        sptd.ScsiStatus = 0
        return 1

    def _nvme(self, in_addr, out_addr, out_size, bytesret):
        prop = tr.STORAGE_PROPERTY_QUERY.from_address(in_addr).PropertyId
        fields = struct.unpack("<10I", ctypes.string_at(in_addr + 8, 40))
        dtype, value = fields[1], fields[2]
        self.protocol_queries.append((prop, dtype, value))
        payload = None if self.nvme is None else self.nvme.get((dtype, value))
        if payload is None:
            return self._fail(50)
        out = struct.pack("<II", 48, 48) + struct.pack(
            "<10I", 3, dtype, value, 0, 40, len(payload), 0, 0, 0, 0) + payload
        n = min(len(out), out_size)
        ctypes.memmove(out_addr, out, n)
        bytesret._obj.value = n
        return 1


# ── Disk profiles ────────────────────────────────────────────────────────

def sata_disk(disk_number=2, **fw) -> FakeDrive:
    return FakeDrive(disk_number=disk_number, port=1,
                     adapter=make_adapter_descriptor(bus_type=tr.BusTypeSata),
                     device=make_device_descriptor(bus_type=tr.BusTypeSata), **fw)


def sat_bridge(disk_number=3, **fw) -> FakeDrive:
    return FakeDrive(disk_number=disk_number,
                     adapter=make_adapter_descriptor(bus_type=tr.BusTypeUsb),
                     device=make_device_descriptor(bus_type=tr.BusTypeUsb, vendor="ATA",
                                                   product="Samsung SSD 870"), **fw)


def nvme_disk(disk_number=4, port=2, **fw) -> FakeDrive:
    return FakeDrive(disk_number=disk_number, port=port,
                     adapter=make_adapter_descriptor(bus_type=tr.BusTypeNvme),
                     device=make_device_descriptor(bus_type=tr.BusTypeNvme, vendor="NVMe",
                                                   product="WD_BLACK SN850X"), **fw)


def usb_stick(disk_number=1, **fw) -> FakeDrive:
    return FakeDrive(disk_number=disk_number,
                     adapter=make_adapter_descriptor(bus_type=tr.BusTypeUsb),
                     device=make_device_descriptor(bus_type=tr.BusTypeUsb, vendor="USB",
                                                   product=" SanDisk 3.2Gen1",
                                                   removable=True), **fw)


SATA_IDENTIFY = make_identify(
    model="Samsung SSD 870 EVO 500GB",
    lba48_sectors=976773168,
    word59=(1 << 12) | (1 << 13) | (1 << 15),  # SANITIZE: crypto + block erase
    word128=0x0001 | 0x0008 | 0x0020,          # security supported, frozen, enhanced
)
SMART_TABLE = make_smart_table({5: 2, 197: 1, 198: 0})
NVME_DATA = {
    (1, 1): make_identify_controller(model="WD_BLACK SN850X 1000GB", sanicap=0b011),
    (2, 2): make_health_log(percentage_used=9, media_errors=0),
}

# Every firmware command a drive could answer — offered to every profile, so
# a test fails if the probe sends one the chosen transport does not allow.
ANSWERS_EVERYTHING = dict(
    ata={(0xEC, 0x00): SATA_IDENTIFY, (0x27, 0x00): 976773167, (0xB0, 0xD0): SMART_TABLE},
    sat_cmds={(0xEC, 0x00): SATA_IDENTIFY, (0xB0, 0xD0): SMART_TABLE},
    nvme_data=NVME_DATA,
)


@pytest.fixture
def env(monkeypatch):
    """Patch every kernel32 entry point the probe can reach."""
    for mod in (dp, pt, sat, smart, nvme, tr):
        monkeypatch.setattr(mod, "audit_log", lambda msg: None)

    state = {"drive": usb_stick(), "opens": [], "closes": [], "open_ok": True,
             "system": {0},
             "drivers": {0: "iaStorVD21", 1: "storahci", 2: "stornvme"}}

    def create_file(path, access, share, security, disposition, flags, template):
        state["opens"].append({"path": path, "access": access, "share": share,
                               "disposition": disposition})
        return FAKE_HANDLE if state["open_ok"] else -1

    def close_handle(handle):
        state["closes"].append(handle)
        return 1

    def dioctl(*args):
        return state["drive"](*args)

    monkeypatch.setattr(dp.kernel32, "CreateFileW", create_file)
    monkeypatch.setattr(dp.kernel32, "CloseHandle", close_handle)
    for mod in (pt, sat, nvme, tr):
        monkeypatch.setattr(mod.kernel32, "DeviceIoControl", dioctl)
    monkeypatch.setattr(tr, "current_system_disks", lambda: state["system"])
    monkeypatch.setattr(tr, "_driver_for_scsi_port", lambda port: state["drivers"].get(port))
    return state


def _protocol_query_sent(drive: FakeDrive) -> bool:
    return any(ioctl == QUERY_IOCTL and prop in PROTOCOL_PROPERTIES
               for ioctl, prop in drive.calls) or bool(drive.protocol_queries)


# ── probe_drive: handle discipline ───────────────────────────────────────

def test_opens_query_only_and_closes(env):
    dp.probe_drive(r"\\.\PhysicalDrive1")

    assert env["opens"] == [{"path": r"\\.\PhysicalDrive1", "access": 0,
                             "share": 3, "disposition": 3}]
    assert env["closes"] == [FAKE_HANDLE]


def test_never_opens_with_write_access(env):
    for drive, path in ((sata_disk(), r"\\.\PhysicalDrive2"),
                        (sat_bridge(), r"\\.\PhysicalDrive3"),
                        (nvme_disk(), r"\\.\PhysicalDrive4"),
                        (usb_stick(), r"\\.\PhysicalDrive1")):
        env["drive"] = drive
        dp.probe_drive(path)

    assert env["opens"]
    assert all(o["access"] & GENERIC_WRITE == 0 for o in env["opens"])


def test_system_disk_is_never_opened(env):
    env["drive"] = nvme_disk(disk_number=0, port=0)

    caps = dp.probe_drive(r"\\.\PhysicalDrive0")

    assert caps.transport == "none"
    assert "system disk" in caps.note
    assert env["opens"] == []
    assert env["drive"].calls == []


def test_unknown_system_layout_opens_nothing(env):
    env["system"] = None

    caps = dp.probe_drive(r"\\.\PhysicalDrive1")

    assert caps.transport == "none"
    assert env["opens"] == []


@pytest.mark.parametrize("path", ["garbage", r"\\.\PhysicalDriveX", r"\\.\C:", "", None])
def test_anything_but_a_physical_drive_path_opens_nothing(env, path):
    assert dp.probe_drive(path).transport == "none"
    assert env["opens"] == []


def test_open_failure_returns_none_transport_without_ioctls(env):
    env["open_ok"] = False

    caps = dp.probe_drive(r"\\.\PhysicalDrive7")

    assert caps.transport == "none"
    assert caps.smart.available is False
    assert env["drive"].calls == []
    assert env["closes"] == []


def test_handle_that_turns_out_to_be_another_disk_gets_nothing_else(env):
    env["drive"] = sata_disk(disk_number=5, **ANSWERS_EVERYTHING)

    caps = dp.probe_drive(r"\\.\PhysicalDrive2")

    assert caps.transport == "none"
    assert env["drive"].calls == [(tr.IOCTL_STORAGE_GET_DEVICE_NUMBER, None)]
    assert env["closes"] == [FAKE_HANDLE]


# ── probe_drive: the two crash scenarios of 17.11.2026 ───────────────────

def test_usb_stick_gets_no_nvme_or_ata_command(env):
    # Crash scenario 1: the SanDisk stick on USBSTOR received NVMe
    # protocol queries (adapter, then device property) by trial and error.
    env["drive"] = usb_stick(**ANSWERS_EVERYTHING)

    caps = dp.probe_drive(r"\\.\PhysicalDrive1")

    drive = env["drive"]
    assert caps.transport == "none"
    assert drive.ioctls <= BENIGN
    assert not _protocol_query_sent(drive)
    assert drive.ata_commands == []


def test_system_nvme_behind_intel_vmd_gets_nothing(env):
    # Crash scenario 2: the system NVMe (Intel RST VMD, iaStorVD21) received
    # NVMe protocol queries. Now the system disk is not even opened.
    env["drive"] = nvme_disk(disk_number=0, port=0, **ANSWERS_EVERYTHING)

    assert dp.probe_drive(r"\\.\PhysicalDrive0").transport == "none"
    assert env["opens"] == []
    assert env["drive"].calls == []


def test_non_system_nvme_behind_intel_vmd_gets_only_benign_queries(env):
    env["drive"] = nvme_disk(disk_number=4, port=0, **ANSWERS_EVERYTHING)

    caps = dp.probe_drive(r"\\.\PhysicalDrive4")

    assert caps.transport == "none"
    assert "iaStorVD21" in caps.note
    assert env["drive"].ioctls <= BENIGN
    assert not _protocol_query_sent(env["drive"])


# ── probe_drive: NVMe on stornvme ────────────────────────────────────────

def test_nvme_on_stornvme_sends_identify_then_health_once_each(env):
    env["drive"] = nvme_disk(**ANSWERS_EVERYTHING)

    caps = dp.probe_drive(r"\\.\PhysicalDrive4")

    assert caps.transport == "nvme"
    assert caps.identify_model == "WD_BLACK SN850X 1000GB"
    assert caps.sanitize_crypto is True
    assert caps.sanitize_block is True
    assert caps.security_supported is False
    assert caps.enhanced_erase is False
    assert caps.hpa_status == "n/a"
    assert caps.smart.source == "nvme"
    assert caps.smart.percentage_used == 9
    assert env["drive"].protocol_queries == [
        (tr.StorageAdapterProtocolSpecificProperty, 1, 1),   # Identify Controller
        (tr.StorageDeviceProtocolSpecificProperty, 2, 2),    # SMART / health log
    ]
    assert env["drive"].ata_commands == []


def test_nvme_identify_refused_stops_without_retry_or_health_query(env):
    env["drive"] = nvme_disk(nvme_data={(2, 2): make_health_log()})

    caps = dp.probe_drive(r"\\.\PhysicalDrive4")

    assert caps.transport == "nvme"
    assert caps.identify_model is None
    assert caps.smart.available is False
    assert env["drive"].protocol_queries == [(tr.StorageAdapterProtocolSpecificProperty, 1, 1)]


# ── probe_drive: ATA / SAT need a read/write handle ──────────────────────

@pytest.mark.parametrize("make, path, expected", [
    (sata_disk, r"\\.\PhysicalDrive2", "ata"),
    (sat_bridge, r"\\.\PhysicalDrive3", "sat"),
])
def test_query_only_probe_sends_no_pass_through(env, make, path, expected):
    # Pass-through IOCTLs need read+write access; on the query-only handle
    # they would only be refused, so they are not sent at all.
    env["drive"] = make(**ANSWERS_EVERYTHING)

    caps = dp.probe_drive(path)

    assert caps.transport == expected
    assert caps.identify_model is None
    assert "read/write" in caps.note
    assert env["drive"].ioctls <= BENIGN
    assert env["drive"].ata_commands == []
    assert not _protocol_query_sent(env["drive"])


# ── probe_handle: a read/write handle from the caller ────────────────────

def test_probe_handle_sata_reads_identify_hpa_and_smart(env):
    env["drive"] = sata_disk(**ANSWERS_EVERYTHING)

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.transport == "ata"
    assert caps.identify_model == "Samsung SSD 870 EVO 500GB"
    assert caps.security_supported is True
    assert caps.security_frozen is True
    assert caps.enhanced_erase is True
    assert caps.sanitize_crypto is True
    assert caps.sanitize_block is True
    assert caps.hpa_status == "none"
    assert caps.smart.available is True
    assert caps.smart.source == "ata"
    assert caps.smart.reallocated == 2
    assert env["drive"].ioctls <= BENIGN | {ATA_IOCTL}
    assert {t for t, _, _ in env["drive"].ata_commands} == {"ata"}
    assert not _protocol_query_sent(env["drive"])


def test_probe_handle_reports_hpa_from_native_max(env):
    hidden = 2_000_000
    env["drive"] = sata_disk(ata={(0xEC, 0x00): SATA_IDENTIFY,
                                  (0x27, 0x00): 976773167 + hidden,
                                  (0xB0, 0xD0): SMART_TABLE})

    assert dp.probe_handle(FAKE_HANDLE).hpa_status == f"found:{hidden * 512}"


def test_probe_handle_reports_hidden_area_from_dco(env):
    hidden = 1_000
    env["drive"] = sata_disk(ata={(0xEC, 0x00): SATA_IDENTIFY,
                                  (0x27, 0x00): 976773167,
                                  (0xB1, 0xC2): _dco_image(976773167 + hidden)})

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.hpa_status == f"found:{hidden * 512}"
    assert caps.smart.available is False  # SMART aborted, probe still fine


def test_probe_handle_hpa_unknown_without_native_max_and_dco(env):
    env["drive"] = sata_disk(ata={(0xEC, 0x00): SATA_IDENTIFY})

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.transport == "ata"
    assert caps.hpa_status == "unknown"


def test_probe_handle_ata_identify_failure_sends_nothing_more(env):
    env["drive"] = sata_disk(ata={})  # drive aborts IDENTIFY

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.transport == "ata"
    assert caps.identify_model is None
    assert env["drive"].ata_commands == [("ata", 0xEC, 0x00)]


def test_probe_handle_sat_bridge(env):
    env["drive"] = sat_bridge(**ANSWERS_EVERYTHING,)
    env["drive"].sat[(0xB1, 0xC2)] = _dco_image(976773167)

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.transport == "sat"
    assert caps.identify_model == "Samsung SSD 870 EVO 500GB"
    assert caps.security_frozen is True
    assert caps.smart.source == "sat"
    assert caps.smart.pending == 1
    assert caps.hpa_status == "none"
    assert env["drive"].ioctls <= BENIGN | {SCSI_IOCTL}
    assert {t for t, _, _ in env["drive"].ata_commands} == {"sat"}
    assert not _protocol_query_sent(env["drive"])


def test_probe_handle_sat_hpa_found_via_dco(env):
    env["drive"] = sat_bridge(sat_cmds={(0xEC, 0x00): SATA_IDENTIFY,
                                        (0xB1, 0xC2): _dco_image(976773167 + 4096)})

    assert dp.probe_handle(FAKE_HANDLE).hpa_status == f"found:{4096 * 512}"


def test_probe_handle_sat_hpa_unknown_without_dco(env):
    env["drive"] = sat_bridge(sat_cmds={(0xEC, 0x00): SATA_IDENTIFY})

    assert dp.probe_handle(FAKE_HANDLE).hpa_status == "unknown"


def test_bridge_faking_success_with_empty_identify_gets_no_further_command(env):
    env["drive"] = sat_bridge(sat_cmds={(0xEC, 0x00): bytes(512),
                                        (0xB0, 0xD0): SMART_TABLE})

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.transport == "sat"
    assert caps.identify_model is None
    assert env["drive"].ata_commands == [("sat", 0xEC, 0x00)]


def test_probe_handle_nvme(env):
    env["drive"] = nvme_disk(**ANSWERS_EVERYTHING)

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.transport == "nvme"
    assert caps.smart.percentage_used == 9
    assert env["drive"].ata_commands == []


def test_probe_handle_usb_stick_sends_no_firmware_command(env):
    env["drive"] = usb_stick(**ANSWERS_EVERYTHING)

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.transport == "none"
    assert env["drive"].ioctls <= BENIGN
    assert env["drive"].ata_commands == []
    assert not _protocol_query_sent(env["drive"])


def test_probe_handle_on_system_disk_sends_only_the_device_number_query(env):
    env["drive"] = sata_disk(disk_number=0, **ANSWERS_EVERYTHING)

    caps = dp.probe_handle(FAKE_HANDLE)

    assert caps.transport == "none"
    assert env["drive"].calls == [(tr.IOCTL_STORAGE_GET_DEVICE_NUMBER, None)]


# ── Never raises ─────────────────────────────────────────────────────────

def test_never_raises_when_ioctl_layer_explodes(env, monkeypatch):
    def boom(*args):
        raise RuntimeError("driver exploded")

    for mod in (pt, sat, nvme, tr):
        monkeypatch.setattr(mod.kernel32, "DeviceIoControl", boom)

    caps = dp.probe_drive(r"\\.\PhysicalDrive1")

    assert caps.transport == "none"
    assert env["closes"] == [FAKE_HANDLE]
    assert dp.probe_handle(FAKE_HANDLE).transport == "none"


def test_never_raises_when_open_explodes(env, monkeypatch):
    def boom(*args):
        raise OSError("no such device")

    monkeypatch.setattr(dp.kernel32, "CreateFileW", boom)

    assert dp.probe_drive(r"\\.\PhysicalDrive9").transport == "none"


def test_never_raises_when_system_disk_lookup_explodes(env, monkeypatch):
    def boom():
        raise RuntimeError("WMI down")

    monkeypatch.setattr(tr, "current_system_disks", boom)

    assert dp.probe_drive(r"\\.\PhysicalDrive1").transport == "none"
    assert env["opens"] == []


# ── Read-only guarantee ──────────────────────────────────────────────────

@pytest.mark.parametrize("make, allowed", [
    (sata_disk, BENIGN | {ATA_IOCTL}),
    (sat_bridge, BENIGN | {SCSI_IOCTL}),
    (nvme_disk, BENIGN),
    (usb_stick, BENIGN),
], ids=["ata", "sat", "nvme", "none"])
def test_probe_handle_only_sends_read_commands_of_its_transport(env, make, allowed):
    drive = make(**ANSWERS_EVERYTHING)
    env["drive"] = drive

    dp.probe_handle(FAKE_HANDLE)

    assert drive.ioctls <= allowed
    assert {(cmd, feat) for _, cmd, feat in drive.ata_commands} <= READ_ONLY_ATA


# ── HPA status helpers ───────────────────────────────────────────────────

@pytest.mark.parametrize("status, de, en", [
    ("none", "keiner", "none"),
    # Same GB as DeviceInfo.capacity_gb (1024**3), so the numbers line up.
    ("found:1610612736", "gefunden: 1,5 GB versteckt", "found: 1.5 GB hidden"),
    ("unknown", "nicht prüfbar", "not checkable"),
    ("n/a", "nicht zutreffend", "not applicable"),
])
def test_describe_hpa(status, de, en):
    assert dp.describe_hpa(status, "de") == de
    assert dp.describe_hpa(status, "en") == en


def test_hpa_hidden_bytes():
    assert dp.hpa_hidden_bytes("found:4096") == 4096
    assert dp.hpa_hidden_bytes("none") == 0
    assert dp.hpa_hidden_bytes("unknown") is None
    assert dp.hpa_hidden_bytes("n/a") is None
