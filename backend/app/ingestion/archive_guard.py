import hashlib
import re
import stat
import struct
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.security import ArchiveRejected

CACHE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "vendor", "dist", "build"}
SECRET = re.compile(
    rb"(?i)(?:password|passwd|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\";]{8,}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def preflight_directory(path: Path, settings: Settings):
    # 在 zipfile 分配 ZipInfo 对象前流式检查中央目录，防止海量空条目耗尽内存。
    size = path.stat().st_size
    if size > settings.max_upload_bytes:
        raise ArchiveRejected("UPLOAD_SIZE_EXCEEDED")
    with path.open("rb") as source:
        source.seek(max(0, size - 65557))
        tail = source.read()
        index = tail.rfind(b"PK\x05\x06")
        if index < 0 or len(tail) - index < 22:
            raise ArchiveRejected("INVALID_ZIP")
        fields = struct.unpack_from("<4s4H2LH", tail, index)
        _, disk, cd_disk, disk_count, count, cd_size, cd_offset, comment_size = fields
        if disk or cd_disk or disk_count != count:
            raise ArchiveRejected("MULTIDISK_UNSUPPORTED")
        if count == 65535 or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
            raise ArchiveRejected("ZIP64_UNSUPPORTED")
        if count > settings.max_entries:
            raise ArchiveRejected("ENTRY_LIMIT_EXCEEDED")
        eocd_position = size - len(tail) + index
        if index + 22 + comment_size != len(tail) or cd_offset + cd_size != eocd_position:
            raise ArchiveRejected("INVALID_ZIP")
        source.seek(cd_offset)
        seen = 0
        while source.tell() < eocd_position:
            header = source.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise ArchiveRejected("INVALID_ZIP")
            seen += 1
            if seen > settings.max_entries:
                raise ArchiveRejected("ENTRY_LIMIT_EXCEEDED")
            name_length, extra_length, comment_length = struct.unpack_from("<3H", header, 28)
            if name_length > 1000:
                raise ArchiveRejected("PATH_LIMIT_EXCEEDED")
            source.seek(name_length + extra_length + comment_length, 1)
        if seen != count or source.tell() != eocd_position:
            raise ArchiveRejected("INVALID_ZIP")


@dataclass(frozen=True)
class Entry:
    info: zipfile.ZipInfo
    path: str
    directory: bool


def normalized_path(info: zipfile.ZipInfo, settings: Settings) -> str:
    raw = info.orig_filename
    if raw != info.filename or "\x00" in raw or "\\" in raw or raw.startswith("/"):
        raise ArchiveRejected("UNSAFE_PATH")
    path = unicodedata.normalize("NFC", raw)
    parts = path.rstrip("/").split("/")
    if not parts or any(p in {"", ".", ".."} for p in parts):
        raise ArchiveRejected("UNSAFE_PATH")
    if any(":" in p or p != p.rstrip(" .") or any(ord(c) < 32 for c in p) for p in parts):
        raise ArchiveRejected("UNSAFE_PATH")
    if (
        len(parts) > settings.max_depth
        or len(path.encode()) > 1000
        or any(len(p.encode()) > 240 for p in parts)
    ):
        raise ArchiveRejected("PATH_LIMIT_EXCEEDED")
    return "/".join(parts)


def inspect_entries(archive: zipfile.ZipFile, settings: Settings) -> list[Entry]:
    infos = archive.infolist()
    if not infos:
        raise ArchiveRejected("EMPTY_ARCHIVE")
    if len(infos) > settings.max_entries:
        raise ArchiveRejected("ENTRY_LIMIT_EXCEEDED")
    entries = []
    nodes = {}
    explicit = set()
    total = 0
    for info in infos:
        path = normalized_path(info, settings)
        kind = stat.S_IFMT(info.external_attr >> 16)
        directory = info.is_dir()
        if kind not in {0, stat.S_IFDIR if directory else stat.S_IFREG}:
            raise ArchiveRejected("LINK_OR_SPECIAL_FILE")
        if info.flag_bits & 1:
            raise ArchiveRejected("ENCRYPTED_ARCHIVE")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ArchiveRejected("UNSUPPORTED_COMPRESSION")
        if info.file_size > settings.max_file_bytes:
            raise ArchiveRejected("FILE_SIZE_EXCEEDED")
        total += info.file_size
        if total > settings.max_total_bytes:
            raise ArchiveRejected("TOTAL_SIZE_EXCEEDED")
        if info.file_size / max(info.compress_size, 1) > settings.max_compression_ratio:
            raise ArchiveRejected("COMPRESSION_RATIO_EXCEEDED")
        if directory and info.file_size:
            raise ArchiveRejected("INVALID_DIRECTORY")
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            spelling = "/".join(parts[:i])
            key = spelling.casefold()
            expected_dir = i < len(parts) or directory
            previous = nodes.get(key)
            if previous and previous != (spelling, expected_dir):
                raise ArchiveRejected("PATH_COLLISION")
            nodes[key] = (spelling, expected_dir)
        key = path.casefold()
        if key in explicit:
            raise ArchiveRejected("PATH_COLLISION")
        explicit.add(key)
        entries.append(Entry(info, path, directory))
    if not any(not entry.directory for entry in entries):
        raise ArchiveRejected("EMPTY_ARCHIVE")
    return entries


def extract_checked(archive_path: Path, destination: Path, settings: Settings) -> dict:
    # destination 必须由服务端新建；上传包无法控制根目录或文件权限。
    files = []
    excluded = []
    actual_total = 0
    sensitive_files = 0
    try:
        preflight_directory(archive_path, settings)
        with zipfile.ZipFile(archive_path) as archive:
            entries = inspect_entries(archive, settings)
            for entry in entries:
                if entry.directory:
                    continue
                target = destination.joinpath(*entry.path.split("/"))
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ArchiveRejected("UNSAFE_PATH")
                reason = (
                    "dependency_or_generated"
                    if CACHE_DIRS.intersection(entry.path.split("/")[:-1])
                    else None
                )
                digest = hashlib.sha256()
                size = 0
                detected_secret = False
                tail = b""
                output = None
                try:
                    with archive.open(entry.info) as source:
                        while block := source.read(64 * 1024):
                            size += len(block)
                            actual_total += len(block)
                            if (
                                size > settings.max_file_bytes
                                or actual_total > settings.max_total_bytes
                            ):
                                raise ArchiveRejected("ACTUAL_SIZE_EXCEEDED")
                            if size == len(block):
                                if b"\x00" in block:
                                    reason = reason or "binary"
                                if reason is None:
                                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                                    output = target.open("xb")
                            digest.update(block)
                            detected_secret |= bool(SECRET.search(tail + block))
                            tail = block[-512:]
                            if output:
                                output.write(block)
                    if size != entry.info.file_size:
                        raise ArchiveRejected("SIZE_MISMATCH")
                    if size == 0 and reason is None:
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                        output = target.open("xb")
                finally:
                    if output:
                        output.close()
                record = {
                    "path": entry.path,
                    "sha256": digest.hexdigest(),
                    "size": size,
                    "sensitive_detected": detected_secret,
                }
                if reason:
                    excluded.append({**record, "reason": reason})
                else:
                    target.chmod(0o440)
                    files.append(record)
                sensitive_files += int(detected_secret)
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError, ValueError) as exc:
        raise ArchiveRejected("INVALID_ZIP") from exc
    return {
        "files": files,
        "excluded": excluded,
        "total_bytes": actual_total,
        "entry_count": len(entries),
        "sensitive_files": sensitive_files,
        "security_policy": "sensitive files require filtering before any future model tool",
        "index_status": "NOT_IMPLEMENTED",
    }
