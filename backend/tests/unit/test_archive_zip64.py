import struct

import pytest

from app.core.security import ArchiveRejected
from tests.conftest import make_zip
from tests.unit.test_archive_guard import run_archive


def zip64_archive(entries=None, *, count=None, cd_size=None, cd_offset=None, locator_disk=0):
    """在真实中央目录后追加合法 ZIP64 EOCD 与定位器，供安全预检回归。"""
    data = make_zip(entries)
    eocd = data.rfind(b"PK\x05\x06")
    (
        _,
        _disk,
        _cd_disk,
        _disk_count,
        real_count,
        real_cd_size,
        real_cd_offset,
        _comment_size,
    ) = struct.unpack_from("<4s4H2LH", data, eocd)
    record_body = struct.pack(
        "<HHIIQQQQ",
        45,
        45,
        0,
        0,
        real_count if count is None else count,
        real_count if count is None else count,
        real_cd_size if cd_size is None else cd_size,
        real_cd_offset if cd_offset is None else cd_offset,
    )
    record = b"PK\x06\x06" + struct.pack("<Q", len(record_body)) + record_body
    locator = b"PK\x06\x07" + struct.pack("<IQI", locator_disk, eocd, 1)
    end = bytearray(data[eocd:])
    # 经典 EOCD 字段全部饱和，强制解析器走 ZIP64 路径。
    struct.pack_into("<4H", end, 4, 0, 0, 0xFFFF, 0xFFFF)
    struct.pack_into("<2L", end, 12, 0xFFFFFFFF, 0xFFFFFFFF)
    return data[:eocd] + record + locator + bytes(end)


def test_valid_zip64_archive_is_extracted(tmp_path, environment):
    manifest = run_archive(tmp_path, zip64_archive([("main.py", "print('zip64')\n")]), environment)
    assert [record["path"] for record in manifest["files"]] == ["main.py"]
    assert (tmp_path / "output" / "main.py").read_text() == "print('zip64')\n"


def test_zip64_entry_limit_is_enforced(tmp_path, environment):
    settings = environment.model_copy(update={"max_entries": 1})
    with pytest.raises(ArchiveRejected, match="ENTRY_LIMIT_EXCEEDED"):
        run_archive(tmp_path, zip64_archive([("a", "x")], count=2), settings)


def test_zip64_locator_out_of_bounds_is_rejected(tmp_path, environment):
    data = bytearray(zip64_archive([("a", "x")]))
    locator = data.rfind(b"PK\x06\x07")
    struct.pack_into("<Q", data, locator + 8, len(data) + 4096)
    with pytest.raises(ArchiveRejected, match="INVALID_ZIP"):
        run_archive(tmp_path, bytes(data), environment)


def test_zip64_multidisk_is_rejected(tmp_path, environment):
    with pytest.raises(ArchiveRejected, match="MULTIDISK_UNSUPPORTED"):
        run_archive(tmp_path, zip64_archive([("a", "x")], locator_disk=1), environment)


def test_zip64_record_size_cannot_overrun_locator(tmp_path, environment):
    data = bytearray(zip64_archive([("a", "x")]))
    record = data.rfind(b"PK\x06\x06")
    struct.pack_into("<Q", data, record + 4, 4096)
    with pytest.raises(ArchiveRejected, match="INVALID_ZIP"):
        run_archive(tmp_path, bytes(data), environment)


def test_zip64_directory_size_mismatch_is_rejected(tmp_path, environment):
    with pytest.raises(ArchiveRejected, match="INVALID_ZIP"):
        run_archive(tmp_path, zip64_archive([("a", "x")], cd_size=999), environment)


def test_zip64_marked_without_locator_is_rejected(tmp_path, environment):
    data = bytearray(make_zip([("a", "x")]))
    eocd = data.rfind(b"PK\x05\x06")
    struct.pack_into("<4H", data, eocd + 4, 0, 0, 0xFFFF, 0xFFFF)
    struct.pack_into("<2L", data, eocd + 12, 0xFFFFFFFF, 0xFFFFFFFF)
    with pytest.raises(ArchiveRejected, match="ZIP64_UNSUPPORTED"):
        run_archive(tmp_path, bytes(data), environment)
