import struct
import zipfile

import pytest

from app.core.security import ArchiveRejected
from tests.conftest import make_zip
from tests.unit.test_archive_guard import run_archive


def test_forged_directory_entry_count_is_rejected_before_zipfile(tmp_path, environment):
    data = bytearray(make_zip([("a", "x"), ("b", "x")]))
    eocd = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, eocd + 8, 1, 1)
    with pytest.raises(ArchiveRejected, match="INVALID_ZIP"):
        run_archive(tmp_path, data, environment)


def test_truncated_and_zip64_metadata(tmp_path, environment):
    data = bytearray(make_zip())
    eocd = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, eocd + 8, 65535, 65535)
    with pytest.raises(ArchiveRejected, match="ZIP64_UNSUPPORTED"):
        run_archive(tmp_path, data, environment)


def test_unsupported_compression(tmp_path, environment):
    with pytest.raises(ArchiveRejected, match="UNSUPPORTED_COMPRESSION"):
        run_archive(tmp_path, make_zip([("a", "hello")], zipfile.ZIP_BZIP2), environment)
