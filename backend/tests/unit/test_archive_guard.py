import stat
import zipfile

import pytest

from app.core.config import Settings
from app.core.security import ArchiveRejected
from app.ingestion.archive_guard import extract_checked
from app.ingestion.content_store import extract_snapshot, hash_file
from tests.conftest import make_zip


def run_archive(tmp_path, data, settings):
    archive = tmp_path / "upload.zip"
    archive.write_bytes(data)
    root = tmp_path / "output"
    root.mkdir()
    return extract_checked(archive, root, settings)


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/etc/passwd",
        "a/../../outside",
        "C:/boot",
        "a\\..\\evil",
        "a//b",
        "./evil",
        "a/./b",
        "trailing./x",
        "a/space /x",
        "a/\x01b",
    ],
)
def test_unsafe_paths(tmp_path, environment, name):
    with pytest.raises(ArchiveRejected):
        run_archive(tmp_path, make_zip([(name, "bad")]), environment)
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize(
    "kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK]
)
def test_links_and_special_files(tmp_path, environment, kind):
    info = zipfile.ZipInfo("special")
    info.create_system = 3
    info.external_attr = (kind | 0o777) << 16
    with pytest.raises(ArchiveRejected, match="LINK_OR_SPECIAL_FILE"):
        run_archive(tmp_path, make_zip([(info, "target")]), environment)


@pytest.mark.parametrize(
    "names",
    [
        ["a.py", "A.py"],
        ["a", "a/x"],
        ["a/x", "a"],
        ["A/x", "a/y"],
        ["a.py", "a.py"],
        ["é.txt", "e\u0301.txt"],
    ],
)
def test_collisions(tmp_path, environment, names):
    with pytest.raises(ArchiveRejected, match="PATH_COLLISION"):
        run_archive(tmp_path, make_zip([(name, "x") for name in names]), environment)


@pytest.mark.parametrize(
    "changes, entries, code",
    [
        ({"max_entries": 1}, [("a", "x"), ("b", "x")], "ENTRY_LIMIT"),
        ({"max_file_bytes": 3}, [("a", "1234")], "FILE_SIZE"),
        ({"max_total_bytes": 3}, [("a", "12"), ("b", "34")], "TOTAL_SIZE"),
        ({"max_depth": 1}, [("a/b", "x")], "PATH_LIMIT"),
        ({"max_compression_ratio": 2}, [("a", "x" * 10000)], "COMPRESSION_RATIO"),
    ],
)
def test_limits(tmp_path, environment, changes, entries, code):
    settings = environment.model_copy(update=changes)
    with pytest.raises(ArchiveRejected, match=code):
        run_archive(tmp_path, make_zip(entries), settings)


def test_exclusions_hashes_and_secrets(tmp_path, environment):
    manifest = run_archive(
        tmp_path,
        make_zip(
            [
                ("main.py", "api_key = 'test_fixture_not_a_real_secret'"),
                ("node_modules/dependency.js", "ignored"),
                ("image.bin", b"\x00\xffbinary"),
                ("empty.txt", ""),
            ]
        ),
        environment,
    )
    assert len(manifest["files"]) == 2
    assert len(manifest["excluded"]) == 2
    assert manifest["sensitive_files"] == 1
    assert manifest["index_status"] == "NOT_IMPLEMENTED"
    assert (tmp_path / "output/main.py").stat().st_mode & 0o222 == 0
    assert not (tmp_path / "output/image.bin").exists()
    assert len(manifest["files"][0]["sha256"]) == 64


def test_invalid_archive(tmp_path, environment):
    with pytest.raises(ArchiveRejected, match="INVALID_ZIP"):
        run_archive(tmp_path, b"PK\x03\x04not a zip", environment)


def test_corrupted_crc(tmp_path, environment):
    data = bytearray(make_zip([("a", "unique-content")], compression=zipfile.ZIP_STORED))
    index = data.index(b"unique-content")
    data[index] ^= 1
    with pytest.raises(ArchiveRejected, match="INVALID_ZIP"):
        run_archive(tmp_path, data, environment)


def test_encrypted_flag(tmp_path, environment):
    data = bytearray(make_zip([("a", "x")]))
    central = data.index(b"PK\x01\x02")
    data[6] |= 1
    data[central + 8] |= 1
    with pytest.raises(ArchiveRejected, match="ENCRYPTED_ARCHIVE"):
        run_archive(tmp_path, data, environment)


def test_recovery_and_hash_tampering(environment):
    environment.archive_root.mkdir()
    archive = environment.archive_root / "a.zip"
    archive.write_bytes(make_zip())
    sha = hash_file(archive)
    key, manifest = extract_snapshot("a.zip", sha, "test-id", environment)
    assert key == "test-id/manifest.json"
    assert extract_snapshot("a.zip", sha, "test-id", environment)[1] == manifest
    archive.chmod(0o640)
    archive.write_bytes(b"tampered")
    with pytest.raises(ArchiveRejected, match="ARCHIVE_HASH_MISMATCH"):
        extract_snapshot("a.zip", sha, "other-id", environment)


def test_atomic_failure_cleanup(environment):
    environment.archive_root.mkdir()
    archive = environment.archive_root / "a.zip"
    archive.write_bytes(make_zip([("../escape", "x")]))
    with pytest.raises(ArchiveRejected):
        extract_snapshot("a.zip", hash_file(archive), "failed-id", environment)
    assert list(environment.ingestion_root.iterdir()) == []


def test_settings_reject_bad_credentials():
    with pytest.raises(ValueError):
        Settings(admin_token="short", read_token="also short")
    with pytest.raises(ValueError):
        Settings(admin_token="x" * 48, read_token="x" * 48)
