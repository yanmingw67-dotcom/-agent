import codecs
import errno
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

CACHE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    "__MACOSX",
    ".venv",
    "venv",
    "vendor",
    "dist",
    "build",
}
EXCLUDED_FILENAMES = {".DS_Store", "Thumbs.db"}
DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
SECRET = re.compile(
    rb"(?i)(?:password|passwd|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\";]{8,}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)

EOCD_SIGNATURE = b"PK\x05\x06"
ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
EOCD_SIZE = 22
ZIP64_LOCATOR_SIZE = 20
# ZIP64 EOCD 固定部分：签名(4) + 记录长度(8) + 44 字节记录体。
ZIP64_EOCD_MIN_SIZE = 56
MAX_CENTRAL_DIRECTORY_NAME_BYTES = 1000


def _classic_end_record(tail: bytes, index: int) -> tuple[int, int, int, int, int, int]:
    """解析经典 EOCD，同时做注释长度与整包边界校验。"""
    (
        _,
        disk,
        cd_disk,
        disk_count,
        count,
        cd_size,
        cd_offset,
        comment_size,
    ) = struct.unpack_from("<4s4H2LH", tail, index)
    if index + EOCD_SIZE + comment_size != len(tail):
        raise ArchiveRejected("INVALID_ZIP")
    return disk, cd_disk, disk_count, count, cd_size, cd_offset


def _zip64_end_record(source, locator_position: int) -> tuple[int, int, int, int, int]:
    """读取并校验 ZIP64 EOCD 定位器与记录，拒绝越界、多卷和伪造偏移。"""
    if locator_position < 0:
        raise ArchiveRejected("ZIP64_UNSUPPORTED")
    source.seek(locator_position)
    locator = source.read(ZIP64_LOCATOR_SIZE)
    if len(locator) != ZIP64_LOCATOR_SIZE or locator[:4] != ZIP64_LOCATOR_SIGNATURE:
        raise ArchiveRejected("ZIP64_UNSUPPORTED")
    _, locator_disk, zip64_offset, total_disks = struct.unpack("<4sIQI", locator)
    if locator_disk != 0 or total_disks != 1:
        # 多卷 ZIP64 无法在单文件校验模型中安全处理。
        raise ArchiveRejected("MULTIDISK_UNSUPPORTED")
    if zip64_offset < 0 or zip64_offset + ZIP64_EOCD_MIN_SIZE > locator_position:
        raise ArchiveRejected("INVALID_ZIP")
    source.seek(zip64_offset)
    record = source.read(ZIP64_EOCD_MIN_SIZE)
    if len(record) != ZIP64_EOCD_MIN_SIZE or record[:4] != ZIP64_EOCD_SIGNATURE:
        raise ArchiveRejected("ZIP64_UNSUPPORTED")
    (record_size,) = struct.unpack_from("<Q", record, 4)
    # 记录体至少 44 字节；扩展数据段必须完整落在定位器之前。
    if record_size < 44 or zip64_offset + 12 + record_size > locator_position:
        raise ArchiveRejected("INVALID_ZIP")
    (
        _version_made,
        _version_needed,
        disk,
        cd_disk,
        disk_count,
        count,
        cd_size,
        cd_offset,
    ) = struct.unpack_from("<HHIIQQQQ", record, 12)
    if disk != 0 or cd_disk != 0 or disk_count != count:
        raise ArchiveRejected("MULTIDISK_UNSUPPORTED")
    return count, cd_size, cd_offset, zip64_offset


def preflight_directory(path: Path, settings: Settings):
    # 在 zipfile 分配 ZipInfo 对象前流式检查中央目录，防止海量空条目耗尽内存。
    size = path.stat().st_size
    if size > settings.max_upload_bytes:
        raise ArchiveRejected("UPLOAD_SIZE_EXCEEDED")
    if size < EOCD_SIZE:
        raise ArchiveRejected("INVALID_ZIP")
    with path.open("rb") as source:
        source.seek(max(0, size - 65557))
        tail = source.read()
        index = tail.rfind(EOCD_SIGNATURE)
        if index < 0 or len(tail) - index < EOCD_SIZE:
            raise ArchiveRejected("INVALID_ZIP")
        eocd_position = size - len(tail) + index
        disk, cd_disk, disk_count, count, cd_size, cd_offset = _classic_end_record(tail, index)
        zip64_marked = count == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF
        locator_position = eocd_position - ZIP64_LOCATOR_SIZE
        locator_present = False
        if locator_position >= 0:
            source.seek(locator_position)
            locator_present = source.read(4) == ZIP64_LOCATOR_SIGNATURE
        if zip64_marked and not locator_present:
            # EOCD 字段已饱和却没有合法 ZIP64 定位器，说明归档标记为 ZIP64 但结构不可信。
            raise ArchiveRejected("ZIP64_UNSUPPORTED")
        if locator_present:
            count, cd_size, cd_offset, central_directory_end = _zip64_end_record(
                source, locator_position
            )
        else:
            if disk or cd_disk or disk_count != count:
                raise ArchiveRejected("MULTIDISK_UNSUPPORTED")
            central_directory_end = eocd_position
        if count > settings.max_entries:
            raise ArchiveRejected("ENTRY_LIMIT_EXCEEDED")
        # 中央目录必须完整落在文件内，并且恰好结束于 EOCD（或 ZIP64 EOCD）之前。
        if cd_offset < 0 or cd_size < 0 or cd_offset + cd_size != central_directory_end:
            raise ArchiveRejected("INVALID_ZIP")
        if cd_offset + cd_size > size:
            raise ArchiveRejected("INVALID_ZIP")
        source.seek(cd_offset)
        seen = 0
        while source.tell() < central_directory_end:
            header = source.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise ArchiveRejected("INVALID_ZIP")
            seen += 1
            if seen > settings.max_entries:
                raise ArchiveRejected("ENTRY_LIMIT_EXCEEDED")
            name_length, extra_length, comment_length = struct.unpack_from("<3H", header, 28)
            if name_length > MAX_CENTRAL_DIRECTORY_NAME_BYTES:
                raise ArchiveRejected("PATH_LIMIT_EXCEEDED")
            if source.tell() + name_length + extra_length + comment_length > central_directory_end:
                raise ArchiveRejected("INVALID_ZIP")
            source.seek(name_length + extra_length + comment_length, 1)
        if seen != count or source.tell() != central_directory_end:
            raise ArchiveRejected("INVALID_ZIP")


@dataclass(frozen=True)
class Entry:
    info: zipfile.ZipInfo
    path: str
    directory: bool
    reason: str | None = None


def decoded_name(info: zipfile.ZipInfo) -> str:
    """兼容未设置 UTF-8 标志的 UTF-8 文件名。

    macOS 等工具生成的 ZIP 常把 UTF-8 文件名按 CP437 写入中央目录，且不设置
    0x800 标志。优先尝试 CP437 -> UTF-8；失败时保留 ZIP 原始解码结果。
    """
    raw = info.orig_filename
    if info.flag_bits & 0x800:
        return raw
    try:
        recovered = raw.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw
    return recovered or raw


def normalized_path(info: zipfile.ZipInfo, settings: Settings) -> str:
    raw = decoded_name(info)
    if "\x00" in raw or "\\" in raw or raw.startswith("/"):
        raise ArchiveRejected("UNSAFE_PATH")
    path = unicodedata.normalize("NFC", raw)
    parts = path.rstrip("/").split("/")
    if not parts or any(p in {"", ".", ".."} for p in parts):
        raise ArchiveRejected("UNSAFE_PATH")
    # 冒号在 Linux 文件名中合法（真实源码含 ci:publish 等）；仍拒绝 Windows
    # 盘符/备用数据流形式，以及尾随点、尾随空格和控制字符。
    if any(
        DRIVE_PREFIX.match(p) or p != p.rstrip(" .") or any(ord(c) < 32 for c in p) for p in parts
    ):
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
        parts = path.split("/")
        reason = None
        if kind not in {0, stat.S_IFDIR if directory else stat.S_IFREG}:
            # 符号链接和特殊文件不落盘，只记录为排除项；不跟随、不创建链接。
            reason = "LINK_OR_SPECIAL_FILE"
        elif set(parts[:-1]) & CACHE_DIRS or parts[-1] in EXCLUDED_FILENAMES:
            # 依赖、VCS、构建产物和系统元数据不进入解压预算，也不读取内容。
            reason = "dependency_or_generated"
        if info.flag_bits & 1:
            raise ArchiveRejected("ENCRYPTED_ARCHIVE")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ArchiveRejected("UNSUPPORTED_COMPRESSION")
        if directory and info.file_size:
            raise ArchiveRejected("INVALID_DIRECTORY")
        if reason is None:
            if info.file_size > settings.max_file_bytes:
                raise ArchiveRejected("FILE_SIZE_EXCEEDED")
            total += info.file_size
            if total > settings.max_total_bytes:
                raise ArchiveRejected("TOTAL_SIZE_EXCEEDED")
            if info.file_size / max(info.compress_size, 1) > settings.max_compression_ratio:
                raise ArchiveRejected("COMPRESSION_RATIO_EXCEEDED")
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
        entries.append(Entry(info, path, directory, reason))
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
                if entry.reason:
                    excluded.append(
                        {
                            "path": entry.path,
                            # 排除项不读取、不落盘；空内容哈希只满足 SourceFile 的
                            # 非空字段约束，检索层会按 parse_status=excluded 跳过。
                            "sha256": hashlib.sha256(b"").hexdigest(),
                            "size": entry.info.file_size,
                            "sensitive_detected": False,
                            "binary_detection_status": "not_read_excluded",
                            "content_kind": "excluded",
                            "encoding": None,
                            "reason": entry.reason,
                        }
                    )
                    continue
                target = destination.joinpath(*entry.path.split("/"))
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ArchiveRejected("UNSAFE_PATH")
                reason = None
                digest = hashlib.sha256()
                size = 0
                detected_secret = False
                tail = b""
                output = None
                has_nul = False
                encoding = None
                decoder = None
                invalid_encoding = False
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
                            has_nul |= b"\x00" in block
                            if size == len(block):
                                if block.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
                                    encoding = "utf-32"
                                elif block.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
                                    encoding = "utf-16"
                                if encoding:
                                    decoder = codecs.getincrementaldecoder(encoding)(
                                        errors="strict"
                                    )
                                target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                                output = target.open("xb")
                            if decoder and not invalid_encoding:
                                try:
                                    decoder.decode(block, final=False)
                                except UnicodeDecodeError:
                                    invalid_encoding = True
                            digest.update(block)
                            detected_secret |= bool(SECRET.search(tail + block))
                            tail = block[-512:]
                            if output:
                                output.write(block)
                    if decoder and not invalid_encoding:
                        try:
                            decoder.decode(b"", final=True)
                        except UnicodeDecodeError:
                            invalid_encoding = True
                    if size != entry.info.file_size:
                        raise ArchiveRejected("SIZE_MISMATCH")
                    if size == 0:
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
                        output = target.open("xb")
                finally:
                    if output:
                        output.close()
                content_kind = "text_candidate"
                if encoding and not invalid_encoding:
                    content_kind = "encoded_text_pending"
                elif has_nul or invalid_encoding:
                    content_kind = "binary_or_unknown_encoding"
                    reason = "binary_or_unknown_encoding"
                    target.unlink()
                record = {
                    "path": entry.path,
                    "sha256": digest.hexdigest(),
                    "size": size,
                    "sensitive_detected": detected_secret,
                    "binary_detection_status": "full_stream_nul_and_bom_check",
                    "content_kind": content_kind,
                    "encoding": encoding,
                }
                if reason:
                    excluded.append({**record, "reason": reason})
                else:
                    target.chmod(0o440)
                    files.append(record)
                sensitive_files += int(detected_secret)
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise ArchiveRejected("STORAGE_LOW_SPACE") from exc
        raise
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
