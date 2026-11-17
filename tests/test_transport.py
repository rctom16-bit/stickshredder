"""Tests for wipe.transport — one drive-command path per disk, chosen up front.

No hardware: ``kernel32.DeviceIoControl`` is replaced by ``FakeStack``, which
answers the benign storage-stack queries (device number, adapter/device
descriptor, SCSI address) the way Windows would for a given disk. The port
driver lookup in the registry and the system-disk list are faked too.
"""

from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

import wipe.drive_probe as dp
import wipe.nvme as nvme
import wipe.passthrough as pt
import wipe.sat as sat
import wipe.transport as tr

FAKE_HANDLE = 0x7A0

GET_NUMBER = tr.IOCTL_STORAGE_GET_DEVICE_NUMBER
QUERY = tr.IOCTL_STORAGE_QUERY_PROPERTY
GET_ADDRESS = tr.IOCTL_SCSI_GET_ADDRESS
BENIGN = {GET_NUMBER, QUERY, GET_ADDRESS}


# ── Descriptor builders ──────────────────────────────────────────────────

def make_adapter_descriptor(*, bus_type: int, alignment_mask: int = 0x3,
                            max_transfer: int = 0x20000) -> bytes:
    d = tr.STORAGE_ADAPTER_DESCRIPTOR()
    d.Version = d.Size = ctypes.sizeof(d)
    d.MaximumTransferLength = max_transfer
    d.MaximumPhysicalPages = 33
    d.AlignmentMask = alignment_mask
    d.BusType = bus_type
    return bytes(d)


def make_device_descriptor(*, bus_type: int, vendor: str = "", product: str = "",
                           removable: bool = False) -> bytes:
    fixed = tr.STORAGE_DEVICE_DESCRIPTOR.RawDeviceProperties.offset
    strings = b""
    offsets = []
    for text in (vendor, product):
        if text:
            offsets.append(fixed + len(strings))
            strings += text.encode("ascii") + b"\0"
        else:
            offsets.append(0)
    d = tr.STORAGE_DEVICE_DESCRIPTOR()
    d.Version = ctypes.sizeof(d)
    d.Size = fixed + len(strings)
    d.RemovableMedia = int(removable)
    d.VendorIdOffset, d.ProductIdOffset = offsets
    d.BusType = bus_type
    return bytes(d)[:fixed] + strings


class FakeStack:
    """``DeviceIoControl`` stand-in for one disk's storage stack.

    ``adapter`` / ``device`` are descriptor bytes (``None`` → the query
    fails); ``port`` is the SCSI port number (``None`` → the query fails).
    Any other IOCTL goes to ``other`` (default: fails).
    """

    def __init__(self, *, disk_number=1, device_type=tr.FILE_DEVICE_DISK,
                 adapter=None, device=None, port=None, other=None):
        self.disk_number = disk_number
        self.device_type = device_type
        self.adapter = adapter
        self.device = device
        self.port = port
        self.other = other
        self.calls: list[tuple[int, int | None]] = []

    @staticmethod
    def _answer(data, out_addr, out_size, bytesret):
        n = min(len(data), out_size)
        ctypes.memmove(out_addr, data, n)
        bytesret._obj.value = n
        return 1

    @staticmethod
    def _fail(error=1):
        ctypes.set_last_error(error)
        return 0

    def __call__(self, handle, ioctl, in_addr, in_size, out_addr, out_size, bytesret, overlapped):
        assert handle == FAKE_HANDLE
        if ioctl == GET_NUMBER:
            self.calls.append((ioctl, None))
            if self.disk_number is None:
                return self._fail()
            n = tr.STORAGE_DEVICE_NUMBER(self.device_type, self.disk_number, 0)
            return self._answer(bytes(n), out_addr, out_size, bytesret)
        if ioctl == QUERY:
            prop = tr.STORAGE_PROPERTY_QUERY.from_address(in_addr).PropertyId
            self.calls.append((ioctl, prop))
            if prop in (tr.StorageAdapterProperty, tr.StorageDeviceProperty):
                data = self.adapter if prop == tr.StorageAdapterProperty else self.device
                if data is None:
                    return self._fail(50)
                return self._answer(data, out_addr, out_size, bytesret)
        elif ioctl == GET_ADDRESS:
            self.calls.append((ioctl, None))
            if self.port is None:
                return self._fail()
            addr = tr.SCSI_ADDRESS(ctypes.sizeof(tr.SCSI_ADDRESS), self.port, 0, 0, 0)
            return self._answer(bytes(addr), out_addr, out_size, bytesret)
        else:
            self.calls.append((ioctl, None))
        if self.other is not None:
            return self.other(handle, ioctl, in_addr, in_size, out_addr, out_size,
                              bytesret, overlapped)
        return self._fail()

    @property
    def ioctls(self) -> set[int]:
        return {ioctl for ioctl, _ in self.calls}


def usb_stick(**kw) -> FakeStack:
    """The SanDisk stick of the crash reports: USBSTOR, vendor 'USB'."""
    return FakeStack(adapter=make_adapter_descriptor(bus_type=tr.BusTypeUsb),
                     device=make_device_descriptor(bus_type=tr.BusTypeUsb, vendor="USB",
                                                   product=" SanDisk 3.2Gen1",
                                                   removable=True), **kw)


def sat_bridge(**kw) -> FakeStack:
    """A SATA SSD in a USB enclosure whose bridge reports vendor 'ATA'."""
    return FakeStack(adapter=make_adapter_descriptor(bus_type=tr.BusTypeUsb),
                     device=make_device_descriptor(bus_type=tr.BusTypeUsb, vendor="ATA",
                                                   product="Samsung SSD 870"), **kw)


def sata_disk(port=0, **kw) -> FakeStack:
    return FakeStack(adapter=make_adapter_descriptor(bus_type=tr.BusTypeSata),
                     device=make_device_descriptor(bus_type=tr.BusTypeSata),
                     port=port, **kw)


def nvme_disk(port=0, **kw) -> FakeStack:
    return FakeStack(adapter=make_adapter_descriptor(bus_type=tr.BusTypeNvme),
                     device=make_device_descriptor(bus_type=tr.BusTypeNvme, vendor="NVMe",
                                                   product="WD_BLACK SN850X"),
                     port=port, **kw)


@pytest.fixture
def stack(monkeypatch):
    """Install a FakeStack; default: no system disk but disk 0, driver per port."""
    state = {"stack": FakeStack(), "system": {0}, "drivers": {0: "storahci"},
             "log": []}

    monkeypatch.setattr(tr.kernel32, "DeviceIoControl",
                        lambda *a: state["stack"](*a))
    monkeypatch.setattr(tr, "current_system_disks", lambda: state["system"])
    monkeypatch.setattr(tr, "_driver_for_scsi_port",
                        lambda port: state["drivers"].get(port))
    monkeypatch.setattr(tr, "audit_log", state["log"].append)
    return state


# ── choose_transport: the rules ──────────────────────────────────────────

def choose(**overrides):
    args = dict(adapter_bus_type=tr.BusTypeSata, device_bus_type=tr.BusTypeSata,
                alignment_mask=0x3, max_transfer_length=0x20000, vendor_id="",
                port_driver="storahci")
    args.update(overrides)
    return tr.choose_transport(**args)


@pytest.mark.parametrize("bus", [tr.BusTypeSata, tr.BusTypeAta])
def test_sata_on_microsoft_ahci_driver_is_ata(bus):
    assert choose(adapter_bus_type=bus, device_bus_type=bus)[0] == "ata"


@pytest.mark.parametrize("driver", ["iaStorVD21", "iaStorAC", "rcraid", None])
def test_sata_on_any_other_driver_is_none(driver):
    transport, reason = choose(port_driver=driver)

    assert transport == "none"
    assert str(driver) in reason


def test_nvme_on_microsoft_nvme_driver_is_nvme():
    assert choose(adapter_bus_type=tr.BusTypeNvme, device_bus_type=tr.BusTypeNvme,
                  port_driver="stornvme")[0] == "nvme"


@pytest.mark.parametrize("driver", ["iaStorVD21", "iaStorVD", "storahci", None])
def test_nvme_behind_vendor_raid_or_vmd_driver_is_none(driver):
    # The system NVMe of the crash reports sits behind Intel RST VMD (iaStorVD21).
    transport, reason = choose(adapter_bus_type=tr.BusTypeNvme,
                               device_bus_type=tr.BusTypeNvme, port_driver=driver)

    assert transport == "none"
    assert str(driver) in reason


def test_driver_names_compare_case_insensitively():
    assert choose(port_driver="StorAHCI")[0] == "ata"
    assert choose(adapter_bus_type=tr.BusTypeNvme, device_bus_type=tr.BusTypeNvme,
                  port_driver="StorNVMe")[0] == "nvme"


@pytest.mark.parametrize("vendor", ["ATA", "ATA     ", "ata"])
def test_usb_device_identifying_as_ata_is_sat(vendor):
    assert choose(adapter_bus_type=tr.BusTypeUsb, device_bus_type=tr.BusTypeUsb,
                  vendor_id=vendor, port_driver=None)[0] == "sat"


@pytest.mark.parametrize("vendor", ["USB", "SanDisk", "ASMT", "JMicron", "Realtek", ""])
def test_plain_usb_mass_storage_is_none(vendor):
    transport, reason = choose(adapter_bus_type=tr.BusTypeUsb,
                               device_bus_type=tr.BusTypeUsb, vendor_id=vendor,
                               port_driver=None)

    assert transport == "none"
    assert "ATA" in reason


def test_usb_never_gets_nvme_or_ata_direct_even_on_inbox_drivers():
    for driver in ("stornvme", "storahci"):
        assert choose(adapter_bus_type=tr.BusTypeUsb, device_bus_type=tr.BusTypeUsb,
                      vendor_id="USB", port_driver=driver)[0] == "none"


@pytest.mark.parametrize("bus", [tr.BusTypeUnknown, tr.BusTypeScsi, tr.BusTypeRAID,
                                 tr.BusTypeSas, 12, 14, 16])
def test_other_bus_types_are_none(bus):
    assert choose(adapter_bus_type=bus, device_bus_type=bus)[0] == "none"


def test_adapter_and_device_bus_types_must_agree():
    transport, reason = choose(adapter_bus_type=tr.BusTypeRAID,
                               device_bus_type=tr.BusTypeNvme, port_driver="stornvme")

    assert transport == "none"
    assert "disagree" in reason


def test_missing_bus_type_is_none():
    assert choose(adapter_bus_type=None)[0] == "none"
    assert choose(device_bus_type=None)[0] == "none"


def test_alignment_beyond_a_page_is_none():
    # Data buffers are page aligned; a stricter adapter would reject them.
    assert choose(alignment_mask=0x1FFF)[0] == "none"
    assert choose(alignment_mask=0xFFF)[0] == "ata"
    assert choose(alignment_mask=None)[0] == "none"


def test_pass_through_needs_room_for_one_sector():
    assert choose(max_transfer_length=256)[0] == "none"
    assert choose(adapter_bus_type=tr.BusTypeUsb, device_bus_type=tr.BusTypeUsb,
                  vendor_id="ATA", port_driver=None, max_transfer_length=256)[0] == "none"


# ── system_disk_refusal ──────────────────────────────────────────────────

def test_system_disk_is_refused():
    assert tr.system_disk_refusal(0, {0}) is not None


def test_non_system_disk_is_allowed():
    assert tr.system_disk_refusal(1, {0}) is None


@pytest.mark.parametrize("system", [None, set()])
def test_unknown_system_disks_refuse_everything(system):
    assert tr.system_disk_refusal(1, system) is not None


# ── inspect_handle ───────────────────────────────────────────────────────

def test_usb_stick_gets_no_drive_commands(stack):
    stack["stack"] = usb_stick()

    info = tr.inspect_handle(FAKE_HANDLE)

    assert info.transport == "none"
    assert info.disk_number == 1
    assert info.vendor_id == "USB"
    assert info.removable_media is True
    assert stack["stack"].ioctls <= BENIGN
    assert GET_ADDRESS not in stack["stack"].ioctls


def test_system_disk_gets_nothing_but_the_device_number_query(stack):
    stack["stack"] = nvme_disk(disk_number=0)
    stack["drivers"] = {0: "stornvme"}

    info = tr.inspect_handle(FAKE_HANDLE)

    assert info.transport == "none"
    assert info.disk_number == 0
    assert "system" in info.reason
    assert stack["stack"].calls == [(GET_NUMBER, None)]


def test_unknown_system_layout_gets_nothing_but_the_device_number_query(stack):
    stack["stack"] = sata_disk(disk_number=3)
    stack["system"] = None

    info = tr.inspect_handle(FAKE_HANDLE)

    assert info.transport == "none"
    assert stack["stack"].calls == [(GET_NUMBER, None)]


def test_system_list_can_be_passed_in(stack):
    stack["stack"] = sata_disk(disk_number=2)
    stack["system"] = None  # would refuse if it were consulted

    assert tr.inspect_handle(FAKE_HANDLE, system_disks={0}).transport == "ata"


def test_handle_of_another_disk_than_expected_is_refused(stack):
    stack["stack"] = sata_disk(disk_number=2)

    info = tr.inspect_handle(FAKE_HANDLE, expected_disk=3)

    assert info.transport == "none"
    assert stack["stack"].calls == [(GET_NUMBER, None)]


def test_device_number_failure_is_none(stack):
    stack["stack"] = sata_disk(disk_number=None)

    info = tr.inspect_handle(FAKE_HANDLE)

    assert info.transport == "none"
    assert info.disk_number is None
    assert stack["stack"].calls == [(GET_NUMBER, None)]


def test_handle_that_is_not_a_disk_is_none(stack):
    stack["stack"] = sata_disk(device_type=0x02)  # FILE_DEVICE_CD_ROM

    assert tr.inspect_handle(FAKE_HANDLE).transport == "none"
    assert stack["stack"].calls == [(GET_NUMBER, None)]


def test_sata_disk_on_storahci_is_ata(stack):
    stack["stack"] = sata_disk(disk_number=2, port=1)
    stack["drivers"] = {1: "storahci"}

    info = tr.inspect_handle(FAKE_HANDLE)

    assert info.transport == "ata"
    assert info.port_driver == "storahci"
    assert info.alignment_mask == 0x3
    assert stack["stack"].ioctls == BENIGN


def test_nvme_disk_on_stornvme_is_nvme(stack):
    stack["stack"] = nvme_disk(disk_number=2)
    stack["drivers"] = {0: "stornvme"}

    assert tr.inspect_handle(FAKE_HANDLE).transport == "nvme"


def test_nvme_disk_behind_intel_vmd_is_none(stack):
    stack["stack"] = nvme_disk(disk_number=2)
    stack["drivers"] = {0: "iaStorVD21"}

    info = tr.inspect_handle(FAKE_HANDLE)

    assert info.transport == "none"
    assert "iaStorVD21" in info.reason


def test_unknown_port_driver_is_none(stack):
    stack["stack"] = sata_disk(disk_number=2, port=None)

    assert tr.inspect_handle(FAKE_HANDLE).transport == "none"


def test_sat_bridge_is_sat(stack):
    stack["stack"] = sat_bridge(disk_number=4)

    info = tr.inspect_handle(FAKE_HANDLE)

    assert info.transport == "sat"
    assert info.vendor_id == "ATA"
    assert info.product_id == "Samsung SSD 870"


@pytest.mark.parametrize("missing", ["adapter", "device"])
def test_failed_descriptor_query_is_none(stack, missing):
    s = sata_disk(disk_number=2)
    setattr(s, missing, None)
    stack["stack"] = s

    assert tr.inspect_handle(FAKE_HANDLE).transport == "none"


def test_truncated_adapter_descriptor_is_none(stack):
    s = sata_disk(disk_number=2)
    s.adapter = s.adapter[:tr.STORAGE_ADAPTER_DESCRIPTOR.BusType.offset]
    stack["stack"] = s

    assert tr.inspect_handle(FAKE_HANDLE).transport == "none"


def test_truncated_device_descriptor_is_none(stack):
    s = sata_disk(disk_number=2)
    s.device = s.device[:tr.STORAGE_DEVICE_DESCRIPTOR.BusType.offset]
    stack["stack"] = s

    assert tr.inspect_handle(FAKE_HANDLE).transport == "none"


def test_vendor_offset_outside_the_answer_is_ignored(stack):
    s = sat_bridge(disk_number=4)
    raw = bytearray(s.device)
    tr.STORAGE_DEVICE_DESCRIPTOR.from_buffer(raw).VendorIdOffset = 5000
    s.device = bytes(raw)
    stack["stack"] = s

    info = tr.inspect_handle(FAKE_HANDLE)

    assert info.vendor_id == ""
    assert info.transport == "none"


def test_descriptor_queries_use_sdk_sized_buffers(stack, monkeypatch):
    seen = []
    inner = sata_disk(disk_number=2)

    def spy(handle, ioctl, in_addr, in_size, out_addr, out_size, bytesret, overlapped):
        seen.append((ioctl, in_size, out_size))
        return inner(handle, ioctl, in_addr, in_size, out_addr, out_size, bytesret, overlapped)

    monkeypatch.setattr(tr.kernel32, "DeviceIoControl", spy)

    tr.inspect_handle(FAKE_HANDLE)

    assert (GET_NUMBER, 0, ctypes.sizeof(tr.STORAGE_DEVICE_NUMBER)) in seen
    assert (GET_ADDRESS, 0, ctypes.sizeof(tr.SCSI_ADDRESS)) in seen
    queries = [s for s in seen if s[0] == QUERY]
    assert all(in_size == ctypes.sizeof(tr.STORAGE_PROPERTY_QUERY) for _, in_size, _ in queries)
    assert all(out_size >= ctypes.sizeof(tr.STORAGE_ADAPTER_DESCRIPTOR) for *_, out_size in queries)


def test_decision_is_written_to_the_audit_log(stack):
    stack["stack"] = usb_stick()

    tr.inspect_handle(FAKE_HANDLE)

    assert any("disk 1" in line and "transport=none" in line for line in stack["log"])


def test_inspect_never_raises(stack, monkeypatch):
    def boom(*args):
        raise OSError("driver exploded")

    monkeypatch.setattr(tr.kernel32, "DeviceIoControl", boom)

    assert tr.inspect_handle(FAKE_HANDLE).transport == "none"


# ── Port driver lookup ───────────────────────────────────────────────────

def test_driver_for_scsi_port_reads_the_device_map(monkeypatch):
    opened = []

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(root, path):
        opened.append((root, path))
        return Key()

    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE="HKLM",
        OpenKey=open_key,
        QueryValueEx=lambda key, name: ("iaStorVD21", 1) if name == "Driver" else None,
    )
    monkeypatch.setattr(tr, "winreg", fake_winreg)

    assert tr._driver_for_scsi_port(0) == "iaStorVD21"
    assert opened == [("HKLM", r"HARDWARE\DEVICEMAP\Scsi\Scsi Port 0")]


def test_driver_for_scsi_port_none_when_missing(monkeypatch):
    def open_key(root, path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(tr, "winreg", SimpleNamespace(HKEY_LOCAL_MACHINE="HKLM",
                                                      OpenKey=open_key,
                                                      QueryValueEx=None))

    assert tr._driver_for_scsi_port(7) is None


# ── Test isolation: no real drive command can escape a unit test ─────────

@pytest.mark.parametrize("mod", [pt, sat, nvme, tr], ids=lambda m: m.__name__)
def test_unit_tests_never_reach_the_real_device_io_control(mod):
    assert not isinstance(mod.kernel32.DeviceIoControl, ctypes._CFuncPtr)


def test_unit_tests_never_open_a_real_disk_from_the_probe():
    assert not isinstance(dp.kernel32.CreateFileW, ctypes._CFuncPtr)
