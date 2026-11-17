import codecs
import hashlib
from pathlib import Path

from app.core.security import AppError
from app.ingestion.archive_guard import SECRET


def decode_source(data: bytes):
    for bom, encoding in [
        (codecs.BOM_UTF32_LE, "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ]:
        if data.startswith(bom):
            return data.decode(encoding), encoding
    return data.decode("utf-8-sig"), "utf-8-sig"


def source_path(root: Path, snapshot_id: str, path: str):
    base = root / snapshot_id / "source"
    candidate = base / path
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(base.resolve()):
        raise AppError(422, "UNSAFE_PATH", "来源路径不合法")
    return candidate


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def sensitive_text(text: str):
    return bool(SECRET.search(text.encode("utf-8"))) or "PRIVATE KEY-----" in text
