"""Every ctypes mirror of a Windows structure matches the Windows SDK (x64).

Ground truth: ``tools/struct_check/expected_x64.txt``, printed by a C program
compiled against the Windows SDK headers (``tools/struct_check/build.cmd``).
It lists sizeof / offsetof / constant values exactly as the compiler sees
them. A structure the kernel reads with a different size or field offset than
the one we hand it is how a "read-only" query corrupts kernel memory — so
every mirrored struct, field and IOCTL constant is pinned here.
"""

from __future__ import annotations

import ctypes
import re
from pathlib import Path

import pytest

import wipe.device as device
import wipe.nvme as nvme
import wipe.passthrough as pt
import wipe.sat as sat
import wipe.transport as transport

pytestmark = pytest.mark.skipif(
    ctypes.sizeof(ctypes.c_void_p) != 8, reason="the ground truth is the x64 layout"
)

TRUTH_FILE = (
    Path(__file__).resolve().parent.parent / "tools" / "struct_check" / "expected_x64.txt"
)
_LINE = re.compile(r"^(sizeof|offsetof|value) (.+?) = (\S+)$")


def _load_truth():
    sizes: dict[str, int] = {}
    offsets: dict[str, dict[str, int]] = {}
    values: dict[str, int] = {}
    header = []
    for raw in TRUTH_FILE.read_text(encoding="ascii").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            header.append(line)
            continue
        match = _LINE.match(line)
        assert match, f"unparseable ground-truth line: {line!r}"
        kind, name, value = match.group(1), match.group(2), int(match.group(3), 0)
        if kind == "sizeof":
            sizes[name] = value
        elif kind == "offsetof":
            struct_name, field = name.split(".")
            offsets.setdefault(struct_name, {})[field] = value
        else:
            values[name] = value
    return header, sizes, offsets, values


HEADER, SIZES, OFFSETS, VALUES = _load_truth()

# C structure name → the ctypes class that is handed to DeviceIoControl.
MIRRORS = {
    "ATA_PASS_THROUGH_DIRECT": pt.ATA_PASS_THROUGH_DIRECT,
    "SCSI_PASS_THROUGH_DIRECT": sat.SCSI_PASS_THROUGH_DIRECT,
    "SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER": sat.SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER,
    "SCSI_ADDRESS": transport.SCSI_ADDRESS,
    "STORAGE_PROPERTY_QUERY": transport.STORAGE_PROPERTY_QUERY,
    "STORAGE_PROTOCOL_SPECIFIC_DATA": nvme.STORAGE_PROTOCOL_SPECIFIC_DATA,
    "STORAGE_PROTOCOL_DATA_DESCRIPTOR": nvme.STORAGE_PROTOCOL_DATA_DESCRIPTOR,
    "STORAGE_ADAPTER_DESCRIPTOR": transport.STORAGE_ADAPTER_DESCRIPTOR,
    "STORAGE_DEVICE_DESCRIPTOR": transport.STORAGE_DEVICE_DESCRIPTOR,
    "STORAGE_DEVICE_NUMBER": transport.STORAGE_DEVICE_NUMBER,
    "DISK_EXTENT": device.DISK_EXTENT,
    "VOLUME_DISK_EXTENTS": type(device._volume_disk_extents(1)),
}

# Device data parsed by byte offset rather than through a ctypes mirror.
PARSED_LAYOUTS = {"NVME_IDENTIFY_CONTROLLER_DATA", "NVME_HEALTH_INFO_LOG"}

# SDK constant name → the value the Python code sends.
CONSTANTS = {
    "IOCTL_ATA_PASS_THROUGH_DIRECT": pt.IOCTL_ATA_PASS_THROUGH_DIRECT,
    "IOCTL_SCSI_PASS_THROUGH_DIRECT": sat.IOCTL_SCSI_PASS_THROUGH_DIRECT,
    "IOCTL_SCSI_GET_ADDRESS": transport.IOCTL_SCSI_GET_ADDRESS,
    "IOCTL_STORAGE_QUERY_PROPERTY": transport.IOCTL_STORAGE_QUERY_PROPERTY,
    "IOCTL_STORAGE_GET_DEVICE_NUMBER": transport.IOCTL_STORAGE_GET_DEVICE_NUMBER,
    "IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS": device.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
    "IOCTL_DISK_GET_LENGTH_INFO": device.IOCTL_DISK_GET_LENGTH_INFO,
    "FSCTL_LOCK_VOLUME": device.FSCTL_LOCK_VOLUME,
    "FSCTL_DISMOUNT_VOLUME": device.FSCTL_DISMOUNT_VOLUME,
    "ATA_FLAGS_DRDY_REQUIRED": pt.ATA_FLAGS_DRDY_REQUIRED,
    "ATA_FLAGS_DATA_IN": pt.ATA_FLAGS_DATA_IN,
    "ATA_FLAGS_DATA_OUT": pt.ATA_FLAGS_DATA_OUT,
    "ATA_FLAGS_48BIT_COMMAND": pt.ATA_FLAGS_48BIT_COMMAND,
    "SCSI_IOCTL_DATA_IN": sat.SCSI_IOCTL_DATA_IN,
    "StorageDeviceProperty": transport.StorageDeviceProperty,
    "StorageAdapterProperty": transport.StorageAdapterProperty,
    "StorageAdapterProtocolSpecificProperty": transport.StorageAdapterProtocolSpecificProperty,
    "StorageDeviceProtocolSpecificProperty": transport.StorageDeviceProtocolSpecificProperty,
    "PropertyStandardQuery": transport.PropertyStandardQuery,
    "ProtocolTypeNvme": nvme.ProtocolTypeNvme,
    "NVMeDataTypeIdentify": nvme.NVMeDataTypeIdentify,
    "NVMeDataTypeLogPage": nvme.NVMeDataTypeLogPage,
    "NVME_IDENTIFY_CNS_CONTROLLER": nvme.NVME_IDENTIFY_CNS_CONTROLLER,
    "NVME_LOG_PAGE_HEALTH_INFO": nvme.NVME_LOG_PAGE_HEALTH_INFO,
    "BusTypeUnknown": transport.BusTypeUnknown,
    "BusTypeScsi": transport.BusTypeScsi,
    "BusTypeAta": transport.BusTypeAta,
    "BusTypeUsb": transport.BusTypeUsb,
    "BusTypeRAID": transport.BusTypeRAID,
    "BusTypeSas": transport.BusTypeSas,
    "BusTypeSata": transport.BusTypeSata,
    "BusTypeNvme": transport.BusTypeNvme,
    "FILE_DEVICE_DISK": transport.FILE_DEVICE_DISK,
}

# Printed for reference only (never sent by the Python code).
REFERENCE_ONLY = {
    "SCSI_IOCTL_DATA_OUT",          # SAT here is data-in only
    "SCSI_IOCTL_DATA_UNSPECIFIED",
    "FAST_FAIL_INVALID_BALANCED_TREE",  # bugcheck 0x139 parameter 1 of the crashes
}


def test_ground_truth_was_built_for_x64():
    assert SIZES["void *"] == 8
    assert any("target = x64" in line for line in HEADER)


@pytest.mark.parametrize("name", sorted(MIRRORS))
def test_struct_size_matches_sdk(name):
    assert ctypes.sizeof(MIRRORS[name]) == SIZES[name]


@pytest.mark.parametrize("name", sorted(MIRRORS))
def test_struct_fields_and_offsets_match_sdk(name):
    cls = MIRRORS[name]
    python_fields = [field[0] for field in cls._fields_]
    sdk_fields = sorted(OFFSETS[name], key=OFFSETS[name].get)

    assert python_fields == sdk_fields
    assert {f: getattr(cls, f).offset for f in python_fields} == OFFSETS[name]


def test_every_ground_truth_struct_is_checked():
    structs = (set(SIZES) | set(OFFSETS)) - {"void *"}
    assert structs == set(MIRRORS) | PARSED_LAYOUTS


@pytest.mark.parametrize("name", sorted(CONSTANTS))
def test_constant_matches_sdk(name):
    assert CONSTANTS[name] == VALUES[name]


def test_every_ground_truth_constant_is_checked():
    assert set(VALUES) == set(CONSTANTS) | REFERENCE_ONLY


def test_nvme_identify_offsets_match_nvme_h():
    off = OFFSETS["NVME_IDENTIFY_CONTROLLER_DATA"]

    assert nvme.NVME_IDENTIFY_SIZE == SIZES["NVME_IDENTIFY_CONTROLLER_DATA"]
    assert (nvme._IDC_SN.start, nvme._IDC_SN.stop) == (off["SN"], off["MN"])
    assert (nvme._IDC_MN.start, nvme._IDC_MN.stop) == (off["MN"], off["FR"])
    assert (nvme._IDC_FR.start, nvme._IDC_FR.stop) == (off["FR"], off["FR"] + 8)
    assert (nvme._IDC_SANICAP.start, nvme._IDC_SANICAP.stop) == (off["SANICAP"], off["SANICAP"] + 4)
    assert nvme._IDC_FNA == off["FNA"]


def test_nvme_health_offsets_match_nvme_h():
    off = OFFSETS["NVME_HEALTH_INFO_LOG"]

    assert nvme.NVME_HEALTH_LOG_SIZE == SIZES["NVME_HEALTH_INFO_LOG"]
    assert nvme._HEALTH_PERCENTAGE_USED == off["PercentageUsed"]
    assert (nvme._HEALTH_MEDIA_ERRORS.start, nvme._HEALTH_MEDIA_ERRORS.stop) == (
        off["MediaErrors"], off["MediaErrors"] + 16)


def test_protocol_query_and_answer_share_the_same_header_size():
    # The NVMe request (query header + protocol data + data) is reused as the
    # answer buffer (descriptor header + protocol data + data): both headers
    # must be the same 8 bytes or the data lands outside the buffer.
    assert (OFFSETS["STORAGE_PROPERTY_QUERY"]["AdditionalParameters"]
            == OFFSETS["STORAGE_PROTOCOL_DATA_DESCRIPTOR"]["ProtocolSpecificData"])
    assert nvme.request_size(nvme.NVME_IDENTIFY_SIZE) == (
        OFFSETS["STORAGE_PROPERTY_QUERY"]["AdditionalParameters"]
        + SIZES["STORAGE_PROTOCOL_SPECIFIC_DATA"] + SIZES["NVME_IDENTIFY_CONTROLLER_DATA"])


def test_sense_buffer_lies_inside_the_scsi_request():
    wrapper = sat.SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER
    end = wrapper.ucSenseBuf.offset + sat.SENSE_BUFFER_LEN

    assert wrapper.ucSenseBuf.offset == OFFSETS["SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER"]["ucSenseBuf"]
    assert end <= SIZES["SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER"]
