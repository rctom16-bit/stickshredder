"""Tests for wipe.drive_probe — which hardware path a disk answers on.

``CreateFileW`` and ``DeviceIoControl`` are faked for every module involved
(passthrough, sat, nvme), so one fake "drive" answers ATA pass-through, SCSI
pass-through and storage queries the way a real SATA disk, USB bridge, NVMe
SSD or plain USB stick would. Every command the probe sends is recorded and
checked against the read-only list.
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
from tests.test_nvme import make_health_log, make_identify_controller
from tests.test_sat import make_identify
from tests.test_smart import make_smart_table

FAKE_HANDLE = 0x99
GENERIC_WRITE = 0x40000000

ATA_IOCTL = pt.IOCTL_ATA_PASS_THROUGH_DIRECT
SCSI_IOCTL = sat.IOCTL_SCSI_PASS_THROUGH_DIRECT
QUERY_IOCTL = nvme.IOCTL_STORAGE_QUERY_PROPERTY

# Every ATA (command, features) the probe may send — all of them only read.
READ_ONLY_ATA = {(0xEC, 0x00), (0x27, 0x00), (0xB1, 0xC2), (0xB0, 0xD0)}


def _dco_image(max_lba: int) -> bytes:
    words = [0] * 256
    words[0] = 0x0002
    for i in range(4):
        words[3 + i] = (max_lba >> (16 * i)) & 0xFFFF
    return struct.pack("<256H", *words)


class FakeDrive:
    """Answers the three IOCTLs like one physical disk would.

    ``ata`` / ``sat`` map (command, features) → response; a response is
    bytes (data-in payload) or an int (native max LBA for 0x27). A missing
    key means the drive aborts the command. ``None`` for a whole transport
    means the IOCTL itself fails (not supported / access denied).
    """

    def __init__(self, *, ata=None, sat_cmds=None, nvme_data=None, fail_error=5):
        self.ata = ata
        self.sat = sat_cmds
        self.nvme = nvme_data
        self.fail_error = fail_error
        self.ioctls: list[int] = []
        self.ata_commands: list[tuple[str, int, int]] = []

    def _fail(self):
        ctypes.set_last_error(self.fail_error)
        return 0

    def device_io_control(self, handle, ioctl, in_addr, in_size, out_addr, out_size,
                          bytesret, overlapped):
        assert handle == FAKE_HANDLE
        self.ioctls.append(ioctl)
        if ioctl == ATA_IOCTL:
            return self._ata(in_addr)
        if ioctl == SCSI_IOCTL:
            return self._sat(in_addr)
        if ioctl == QUERY_IOCTL:
            return self._nvme(in_addr, out_addr, out_size)
        return self._fail()

    def _ata(self, addr):
        aptd = pt.ATA_PASS_THROUGH_DIRECT.from_address(addr)
        cmd, feat = aptd.CurrentTaskFile[6], aptd.CurrentTaskFile[0]
        self.ata_commands.append(("ata", cmd, feat))
        if self.ata is None:
            return self._fail()
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
            return self._fail()
        resp = self.sat.get((cmd, feat))
        if resp is None:
            sptd.ScsiStatus = 0x02  # CHECK CONDITION
            return 1
        ctypes.memmove(sptd.DataBuffer, resp, len(resp))
        sptd.ScsiStatus = 0
        return 1

    def _nvme(self, in_addr, out_addr, out_size):
        if self.nvme is None:
            return self._fail()
        fields = struct.unpack("<10I", ctypes.string_at(in_addr + 8, 40))
        dtype, value = fields[1], fields[2]
        payload = self.nvme.get((dtype, value))
        if payload is None:
            return self._fail()
        out = struct.pack("<II", 48, 48) + struct.pack(
            "<10I", 3, dtype, value, 0, 40, len(payload), 0, 0, 0, 0) + payload
        ctypes.memmove(out_addr, out, min(len(out), out_size))
        return 1


@pytest.fixture
def env(monkeypatch):
    """Patch every kernel32 entry point the probe can reach."""
    for mod in (dp, pt, sat, smart, nvme):
        monkeypatch.setattr(mod, "audit_log", lambda msg: None)

    state = {"drive": FakeDrive(), "opens": [], "closes": [], "open_ok": True}

    def create_file(path, access, share, security, disposition, flags, template):
        state["opens"].append({"path": path, "access": access, "share": share,
                               "disposition": disposition})
        return FAKE_HANDLE if state["open_ok"] else -1

    def close_handle(handle):
        state["closes"].append(handle)
        return 1

    def dioctl(*args):
        return state["drive"].device_io_control(*args)

    monkeypatch.setattr(dp.kernel32, "CreateFileW", create_file)
    monkeypatch.setattr(dp.kernel32, "CloseHandle", close_handle)
    for mod in (pt, sat, nvme):
        monkeypatch.setattr(mod.kernel32, "DeviceIoControl", dioctl)
    return state


SATA_IDENTIFY = make_identify(
    model="Samsung SSD 870 EVO 500GB",
    lba48_sectors=976773168,
    word59=(1 << 12) | (1 << 13) | (1 << 15),  # SANITIZE: crypto + block erase
    word128=0x0001 | 0x0008 | 0x0020,          # security supported, frozen, enhanced
)
SMART_TABLE = make_smart_table({5: 2, 197: 1, 198: 0})


# ── Handle discipline ────────────────────────────────────────────────────

def test_opens_query_only_and_closes(env):
    dp.probe_drive(r"\\.\PhysicalDrive1")

    assert env["opens"] == [{"path": r"\\.\PhysicalDrive1", "access": 0,
                             "share": 3, "disposition": 3}]
    assert env["closes"] == [FAKE_HANDLE]


def test_never_reopens_with_more_access_when_queries_fail(env):
    env["drive"] = FakeDrive()  # nothing answers

    dp.probe_drive(r"\\.\PhysicalDrive1")

    assert len(env["opens"]) == 1
    assert all(o["access"] & GENERIC_WRITE == 0 for o in env["opens"])


def test_open_failure_returns_none_transport_without_ioctls(env):
    env["open_ok"] = False

    caps = dp.probe_drive(r"\\.\PhysicalDrive7")

    assert caps.transport == "none"
    assert caps.smart.available is False
    assert env["drive"].ioctls == []
    assert env["closes"] == []


# ── Fall-through order ───────────────────────────────────────────────────

def test_ata_direct_answers_first(env):
    env["drive"] = FakeDrive(ata={
        (0xEC, 0x00): SATA_IDENTIFY,
        (0x27, 0x00): 976773167,       # native max LBA == accessible - 1 → no HPA
        (0xB0, 0xD0): SMART_TABLE,
    })

    caps = dp.probe_drive(r"\\.\PhysicalDrive2")

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
    assert set(env["drive"].ioctls) == {ATA_IOCTL}


def test_ata_direct_reports_hpa_from_native_max(env):
    hidden = 2_000_000
    env["drive"] = FakeDrive(ata={
        (0xEC, 0x00): SATA_IDENTIFY,
        (0x27, 0x00): 976773167 + hidden,
        (0xB0, 0xD0): SMART_TABLE,
    })

    caps = dp.probe_drive(r"\\.\PhysicalDrive2")

    assert caps.hpa_status == f"found:{hidden * 512}"


def test_ata_direct_reports_hidden_area_from_dco(env):
    hidden = 1_000
    env["drive"] = FakeDrive(ata={
        (0xEC, 0x00): SATA_IDENTIFY,
        (0x27, 0x00): 976773167,
        (0xB1, 0xC2): _dco_image(976773167 + hidden),
    })

    caps = dp.probe_drive(r"\\.\PhysicalDrive2")

    assert caps.hpa_status == f"found:{hidden * 512}"
    assert caps.smart.available is False  # SMART aborted, probe still fine


def test_ata_direct_hpa_unknown_when_native_max_and_dco_unavailable(env):
    env["drive"] = FakeDrive(ata={(0xEC, 0x00): SATA_IDENTIFY})

    caps = dp.probe_drive(r"\\.\PhysicalDrive2")

    assert caps.transport == "ata"
    assert caps.hpa_status == "unknown"


def test_falls_through_to_sat_bridge(env):
    env["drive"] = FakeDrive(sat_cmds={
        (0xEC, 0x00): SATA_IDENTIFY,
        (0xB0, 0xD0): SMART_TABLE,
        (0xB1, 0xC2): _dco_image(976773167),
    })

    caps = dp.probe_drive(r"\\.\PhysicalDrive3")

    assert caps.transport == "sat"
    assert caps.identify_model == "Samsung SSD 870 EVO 500GB"
    assert caps.security_frozen is True
    assert caps.smart.source == "sat"
    assert caps.smart.pending == 1
    assert caps.hpa_status == "none"
    assert env["drive"].ioctls[0] == ATA_IOCTL
    assert QUERY_IOCTL not in env["drive"].ioctls


def test_sat_hpa_found_via_dco(env):
    env["drive"] = FakeDrive(sat_cmds={
        (0xEC, 0x00): SATA_IDENTIFY,
        (0xB1, 0xC2): _dco_image(976773167 + 4096),
    })

    assert dp.probe_drive(r"\\.\PhysicalDrive3").hpa_status == f"found:{4096 * 512}"


def test_sat_hpa_unknown_without_dco(env):
    env["drive"] = FakeDrive(sat_cmds={(0xEC, 0x00): SATA_IDENTIFY})

    assert dp.probe_drive(r"\\.\PhysicalDrive3").hpa_status == "unknown"


def test_falls_through_to_nvme(env):
    env["drive"] = FakeDrive(nvme_data={
        (1, 1): make_identify_controller(model="WD_BLACK SN850X 1000GB", sanicap=0b011),
        (2, 2): make_health_log(percentage_used=9, media_errors=0),
    })

    caps = dp.probe_drive(r"\\.\PhysicalDrive0")

    assert caps.transport == "nvme"
    assert caps.identify_model == "WD_BLACK SN850X 1000GB"
    assert caps.sanitize_crypto is True
    assert caps.sanitize_block is True
    assert caps.security_supported is False
    assert caps.enhanced_erase is False
    assert caps.hpa_status == "n/a"
    assert caps.smart.source == "nvme"
    assert caps.smart.percentage_used == 9
    order = [i for n, i in enumerate(env["drive"].ioctls) if i not in env["drive"].ioctls[:n]]
    assert order == [ATA_IOCTL, SCSI_IOCTL, QUERY_IOCTL]


def test_nothing_answers_gives_none(env):
    env["drive"] = FakeDrive()

    caps = dp.probe_drive(r"\\.\PhysicalDrive1")

    assert caps.transport == "none"
    assert caps.identify_model is None
    assert not (caps.security_supported or caps.security_frozen or caps.enhanced_erase
                or caps.sanitize_crypto or caps.sanitize_block)
    assert caps.hpa_status == "unknown"
    assert caps.smart.available is False
    assert caps.smart.source == "none"


def test_bridge_faking_success_with_empty_identify_is_not_sat(env):
    env["drive"] = FakeDrive(sat_cmds={(0xEC, 0x00): bytes(512)})

    assert dp.probe_drive(r"\\.\PhysicalDrive1").transport == "none"


def test_never_raises_when_ioctl_layer_explodes(env, monkeypatch):
    def boom(*args):
        raise RuntimeError("driver exploded")

    for mod in (pt, sat, nvme):
        monkeypatch.setattr(mod.kernel32, "DeviceIoControl", boom)

    caps = dp.probe_drive(r"\\.\PhysicalDrive1")

    assert caps.transport == "none"
    assert env["closes"] == [FAKE_HANDLE]


def test_never_raises_when_open_explodes(env, monkeypatch):
    def boom(*args):
        raise OSError("no such device")

    monkeypatch.setattr(dp.kernel32, "CreateFileW", boom)

    assert dp.probe_drive(r"\\.\PhysicalDrive9").transport == "none"


# ── Read-only guarantee ──────────────────────────────────────────────────

@pytest.mark.parametrize("drive", [
    FakeDrive(ata={(0xEC, 0x00): SATA_IDENTIFY, (0x27, 0x00): 976773167,
                   (0xB1, 0xC2): _dco_image(976773167), (0xB0, 0xD0): SMART_TABLE}),
    FakeDrive(sat_cmds={(0xEC, 0x00): SATA_IDENTIFY, (0xB1, 0xC2): _dco_image(976773167),
                        (0xB0, 0xD0): SMART_TABLE}),
    FakeDrive(nvme_data={(1, 1): make_identify_controller(), (2, 2): make_health_log()}),
    FakeDrive(),
], ids=["ata", "sat", "nvme", "none"])
def test_probe_only_sends_read_commands(env, drive):
    env["drive"] = drive

    dp.probe_drive(r"\\.\PhysicalDrive1")

    assert set(drive.ioctls) <= {ATA_IOCTL, SCSI_IOCTL, QUERY_IOCTL}
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
