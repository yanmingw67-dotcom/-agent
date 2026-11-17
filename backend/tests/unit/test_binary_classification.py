import codecs
import zipfile

import pytest

from tests.conftest import make_zip
from tests.unit.test_archive_guard import run_archive


def test_nul_after_first_64k_is_classified_and_partial_file_removed(tmp_path, environment):
    content = b"a" * (64 * 1024 + 10) + b"\x00tail"
    manifest = run_archive(
        tmp_path, make_zip([("late.bin", content)], zipfile.ZIP_STORED), environment
    )
    assert manifest["files"] == []
    item = manifest["excluded"][0]
    assert item["reason"] == "binary_or_unknown_encoding"
    assert item["binary_detection_status"] == "full_stream_nul_and_bom_check"
    assert not (tmp_path / "output/late.bin").exists()


@pytest.mark.parametrize(
    "encoding,bom",
    [
        ("utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16-be", codecs.BOM_UTF16_BE),
        ("utf-32-le", codecs.BOM_UTF32_LE),
        ("utf-32-be", codecs.BOM_UTF32_BE),
    ],
)
def test_bom_encoded_text_is_preserved_pending_parser(tmp_path, environment, encoding, bom):
    content = bom + ("源码配置示例 " * 10000).encode(encoding)
    manifest = run_archive(
        tmp_path, make_zip([("encoded.txt", content)], zipfile.ZIP_STORED), environment
    )
    assert manifest["excluded"] == []
    item = manifest["files"][0]
    assert item["content_kind"] == "encoded_text_pending"
    assert item["encoding"] in {"utf-16", "utf-32"}
    assert (tmp_path / "output/encoded.txt").read_bytes() == content


def test_truncated_bom_encoding_is_not_silently_text(tmp_path, environment):
    manifest = run_archive(
        tmp_path, make_zip([("bad.txt", codecs.BOM_UTF16_LE + b"x")]), environment
    )
    assert manifest["files"] == []
    assert manifest["excluded"][0]["content_kind"] == "binary_or_unknown_encoding"


def test_large_text_and_empty_file_classification(tmp_path, environment):
    manifest = run_archive(
        tmp_path,
        make_zip([("text.txt", b"x" * 70000), ("empty", b"")], zipfile.ZIP_STORED),
        environment,
    )
    assert len(manifest["files"]) == 2
    assert all(item["content_kind"] == "text_candidate" for item in manifest["files"])
