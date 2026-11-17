from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import admin
from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Document
from app.db.models import AuditEvent, KnowledgeJob, Snapshot
from app.db.session import get_db
from app.ingestion.capacity import release, reserve, scan

router = APIRouter(prefix="/api/v1", tags=["索引管理"])


def staging(db, snapshot_id, category_id=None):
    snapshot = db.scalar(select(Snapshot).where(Snapshot.id == snapshot_id).with_for_update())
    if not snapshot or (category_id and snapshot.category_id != category_id):
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在或不属于指定品类")
    if snapshot.status not in {"extracted"}:
        raise AppError(409, "SNAPSHOT_NOT_STAGING", "只允许构建已解压的暂存快照")
    if db.scalar(
        select(KnowledgeJob.id).where(
            KnowledgeJob.category_id == snapshot.category_id,
            KnowledgeJob.status.in_(["queued", "running"]),
        )
    ):
        raise AppError(409, "JOB_CONFLICT", "品类已有在途任务")
    return snapshot


@router.post("/snapshots/{snapshot_id}/build", status_code=202)
def build(snapshot_id: str, request: Request, role=Depends(admin), db: Session = Depends(get_db)):
    try:
        snapshot = staging(db, snapshot_id)
        job = KnowledgeJob(
            snapshot_id=snapshot.id,
            category_id=snapshot.category_id,
            job_type="source_index",
            stage="extracted",
        )
        snapshot.index_status = "queued"
        db.add(job)
        db.flush()
        db.add(
            AuditEvent(
                principal_id=role,
                action="index.build",
                resource_id=snapshot.id,
                correlation_id=request.state.correlation_id,
                details_json={"job_id": job.id},
            )
        )
        db.commit()
        return {"snapshot_id": snapshot.id, "job_id": job.id}
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "JOB_CONFLICT", "并发索引任务冲突") from exc


@router.post("/categories/{category_id}/documents", status_code=202)
async def upload_document(
    category_id: str,
    request: Request,
    snapshot_id: str = Form(...),
    file: UploadFile = File(...),
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    suffix = Path(file.filename or "").suffix.lower()
    if (
        not file.filename
        or len(file.filename.encode()) > 240
        or "/" in file.filename
        or "\\" in file.filename
        or any(ord(c) < 32 for c in file.filename)
        or suffix not in {".docx", ".pdf", ".xlsx", ".md", ".txt"}
    ):
        raise AppError(422, "INVALID_DOCUMENT_TYPE", "技术文档文件名或类型不合法")
    allowed_mimes = {
        ".docx": {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/octet-stream",
        },
        ".xlsx": {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/octet-stream",
        },
        ".pdf": {"application/pdf", "application/octet-stream"},
        ".md": {"text/markdown", "text/plain", "application/octet-stream"},
        ".txt": {"text/plain", "application/octet-stream"},
    }
    if file.content_type not in allowed_mimes[suffix]:
        raise AppError(422, "INVALID_MEDIA_TYPE", "文档 MIME 不受支持")
    staging(db, snapshot_id, category_id)
    db.rollback()
    settings.document_store.mkdir(parents=True, exist_ok=True, mode=0o750)
    key = str(uuid4()) + suffix
    reserve(settings, key, settings.document_max_bytes)
    path = settings.document_store / key
    committed = False
    try:
        import hashlib

        checksum = hashlib.sha256()
        size = 0
        with path.open("xb") as output:
            path.chmod(0o640)
            while block := await file.read(65536):
                if size == 0:
                    if suffix in {".docx", ".xlsx"} and not block.startswith(b"PK\x03\x04"):
                        raise AppError(422, "INVALID_SIGNATURE", "Office 文档内容签名不合法")
                    if suffix == ".pdf" and not block.startswith(b"%PDF-"):
                        raise AppError(422, "INVALID_SIGNATURE", "PDF 内容签名不合法")
                size += len(block)
                if size > settings.document_max_bytes:
                    raise AppError(413, "DOCUMENT_TOO_LARGE", "文档超过上传限额")
                checksum.update(block)
                output.write(block)
        if not size:
            raise AppError(422, "EMPTY_DOCUMENT", "文档为空")
        path.chmod(0o440)
        staging(db, snapshot_id, category_id)
        document = Document(
            category_id=category_id,
            snapshot_id=snapshot_id,
            filename=file.filename,
            storage_key=key,
            sha256=checksum.hexdigest(),
            size=size,
        )
        db.add(document)
        job = KnowledgeJob(
            category_id=category_id,
            snapshot_id=snapshot_id,
            job_type="document_index",
            stage="extracted",
        )
        db.add(job)
        db.flush()
        db.add(
            AuditEvent(
                principal_id=role,
                action="document.upload",
                resource_id=document.id,
                correlation_id=request.state.correlation_id,
                details_json={"job_id": job.id},
            )
        )
        db.commit()
        committed = True
        return {"document_id": document.id, "job_id": job.id, "snapshot_id": snapshot_id}
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "JOB_CONFLICT", "并发文档任务冲突") from exc
    finally:
        await file.close()
        if not committed:
            path.unlink(missing_ok=True)
        release(settings, key)


@router.get("/admin/storage")
def storage(role=Depends(admin), db: Session = Depends(get_db)):
    return scan(get_settings(), db)


@router.get("/snapshots/{snapshot_id}/documents")
def list_documents(snapshot_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "parse_status": d.parse_status,
            "error_code": d.error_code,
            "parse_details": d.parse_details,
        }
        for d in db.scalars(select(Document).where(Document.snapshot_id == snapshot_id).limit(200))
    ]
