import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import Settings
from app.core.security import AppError, ArchiveRejected
from app.ingestion.archive_guard import extract_checked
from app.ingestion.capacity import release, reserve

ALLOWED_MIMES = {"application/zip", "application/x-zip-compressed", "application/octet-stream"}


async def receive_archive(upload: UploadFile, settings: Settings) -> tuple[str, str, int]:
    filename = upload.filename or ""
    if (
        not filename.lower().endswith(".zip")
        or "/" in filename
        or "\\" in filename
        or any(ord(c) < 32 for c in filename)
    ):
        raise AppError(422, "INVALID_FILENAME", "只接受文件名合法的 ZIP 归档")
    if upload.content_type not in ALLOWED_MIMES:
        raise AppError(422, "INVALID_MEDIA_TYPE", "文件 MIME 不受支持")
    settings.archive_root.mkdir(parents=True, exist_ok=True, mode=0o750)
    key = f"{uuid4()}.zip"
    path = settings.archive_root / key
    reserve(
        settings,
        key,
        settings.max_upload_bytes + settings.max_total_bytes + settings.max_entries * 2048,
    )
    digest = hashlib.sha256()
    count = 0
    try:
        with path.open("xb") as output:
            os.chmod(path, 0o640)
            while block := await upload.read(64 * 1024):
                if count == 0 and not block.startswith(b"PK\x03\x04"):
                    raise AppError(422, "INVALID_ZIP_SIGNATURE", "ZIP 内容签名不合法或归档为空")
                count += len(block)
                if count > settings.max_upload_bytes:
                    raise AppError(413, "UPLOAD_TOO_LARGE", "上传文件超过限额")
                digest.update(block)
                output.write(block)
            if count == 0:
                raise AppError(422, "EMPTY_UPLOAD", "文件为空")
        path.chmod(0o440)
    except BaseException:
        path.unlink(missing_ok=True)
        release(settings, key)
        raise
    return key, digest.hexdigest(), count


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(64 * 1024):
            digest.update(block)
    return digest.hexdigest()


def remove_tree(path: Path):
    # 仅清理本服务创建的 UUID 临时目录；先恢复目录写权限。
    for root, dirs, _ in os.walk(path):
        os.chmod(root, 0o750)
        for name in dirs:
            os.chmod(Path(root) / name, 0o750)
    shutil.rmtree(path)


def extract_snapshot(
    archive_key: str, sha256: str, snapshot_id: str, settings: Settings
) -> tuple[str, dict]:
    archive = settings.archive_root / archive_key
    if hash_file(archive) != sha256:
        raise ArchiveRejected("ARCHIVE_HASH_MISMATCH")
    settings.ingestion_root.mkdir(parents=True, exist_ok=True, mode=0o750)
    destination = settings.ingestion_root / snapshot_id
    if destination.exists():
        # 处理文件系统提交成功、数据库提交前崩溃后的幂等恢复。
        manifest = json.loads((destination / "manifest.json").read_text())
        if manifest["source_sha256"] != sha256:
            raise ArchiveRejected("SNAPSHOT_HASH_MISMATCH")
        for record in manifest["files"]:
            if hash_file(destination / "source" / record["path"]) != record["sha256"]:
                raise ArchiveRejected("EXTRACTED_HASH_MISMATCH")
        return f"{snapshot_id}/manifest.json", manifest
    temp = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}-", dir=settings.ingestion_root))
    try:
        source = temp / "source"
        source.mkdir(mode=0o750)
        manifest = extract_checked(archive, source, settings)
        manifest.update({"snapshot_id": snapshot_id, "source_sha256": sha256})
        manifest_path = temp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        manifest_path.chmod(0o440)
        for root, _, _ in os.walk(temp, topdown=False):
            if Path(root) != temp:
                Path(root).chmod(0o550)
        temp.rename(destination)
        destination.chmod(0o550)
        return f"{snapshot_id}/manifest.json", manifest
    finally:
        if temp.exists():
            remove_tree(temp)
