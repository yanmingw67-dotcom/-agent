import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import admin
from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Document, DocumentSegment
from app.db.knowledge_models import DocumentDiscoveryRun
from app.db.models import AuditEvent, KnowledgeJob, Snapshot
from app.db.session import get_db
from app.ingestion.capacity import release, reserve, scan
from app.knowledge.document_discovery import create_run
from app.knowledge.document_staging import (
    derive_staging_snapshot,
    require_derived_staging,
)
from app.knowledge.document_staging import (
    remove_document as remove_staged_document,
)
from app.knowledge.document_staging import (
    reparse_document as reparse_staged_document,
)
from app.knowledge.document_staging import (
    replace_document as replace_staged_document,
)

router = APIRouter(prefix="/api/v1", tags=["索引管理"])

DOCUMENT_SUFFIXES = {".docx", ".pdf", ".xlsx", ".md", ".txt"}
DOCUMENT_MIMES = {
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


def validate_document_name(upload: UploadFile):
    suffix = Path(upload.filename or "").suffix.lower()
    if (
        not upload.filename
        or len(upload.filename.encode()) > 240
        or "/" in upload.filename
        or "\\" in upload.filename
        or any(ord(c) < 32 for c in upload.filename)
        or suffix not in DOCUMENT_SUFFIXES
    ):
        raise AppError(422, "INVALID_DOCUMENT_TYPE", "技术文档文件名或类型不合法")
    if upload.content_type not in DOCUMENT_MIMES[suffix]:
        raise AppError(422, "INVALID_MEDIA_TYPE", "文档 MIME 不受支持")
    return suffix


async def receive_document(upload: UploadFile, suffix: str, settings):
    settings.document_store.mkdir(parents=True, exist_ok=True, mode=0o750)
    key = str(uuid4()) + suffix
    reserve(settings, key, settings.document_max_bytes)
    path = settings.document_store / key
    try:
        checksum = hashlib.sha256()
        size = 0
        with path.open("xb") as output:
            path.chmod(0o640)
            while block := await upload.read(65536):
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
        return key, checksum.hexdigest(), size
    except BaseException:
        path.unlink(missing_ok=True)
        release(settings, key)
        raise


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


@router.post("/categories/{category_id}/document-staging-snapshots", status_code=201)
def derive_document_staging_snapshot(
    category_id: str,
    request: Request,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    """从当前受控源码与文档派生新的待发布快照，不修改已发布快照。"""
    try:
        return derive_staging_snapshot(db, category_id, role, request.state.correlation_id)
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "DERIVATION_CONFLICT", "并发派生快照冲突") from exc


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
    suffix = validate_document_name(file)
    staging(db, snapshot_id, category_id)
    db.rollback()
    key = sha = None
    committed = False
    try:
        key, sha, size = await receive_document(file, suffix, settings)
        staging(db, snapshot_id, category_id)
        document = Document(
            category_id=category_id,
            snapshot_id=snapshot_id,
            filename=file.filename,
            storage_key=key,
            sha256=sha,
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
        if key:
            if not committed:
                (settings.document_store / key).unlink(missing_ok=True)
            release(settings, key)


@router.delete("/snapshots/{snapshot_id}/documents/{document_id}")
def delete_staged_document(
    snapshot_id: str,
    document_id: str,
    request: Request,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    return remove_staged_document(db, snapshot_id, document_id, role, request.state.correlation_id)


@router.post("/snapshots/{snapshot_id}/documents/{document_id}/replace", status_code=202)
async def replace_document(
    snapshot_id: str,
    document_id: str,
    request: Request,
    file: UploadFile = File(...),
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    suffix = validate_document_name(file)
    snapshot = require_derived_staging(db, snapshot_id)
    document = db.get(Document, document_id)
    if not document or document.snapshot_id != snapshot.id:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "暂存快照中没有该文档")
    if Path(document.filename).suffix.lower() != suffix:
        raise AppError(422, "DOCUMENT_REPLACE_TYPE_MISMATCH", "替换文档必须保持原文件类型")
    key = None
    replaced = False
    try:
        key, sha, size = await receive_document(file, suffix, settings)
        result = replace_staged_document(
            db, snapshot_id, document_id, key, sha, size, role, request.state.correlation_id
        )
        replaced = True
        return result
    finally:
        await file.close()
        if key:
            if not replaced:
                (settings.document_store / key).unlink(missing_ok=True)
            release(settings, key)


@router.post("/snapshots/{snapshot_id}/documents/{document_id}/reparse", status_code=202)
def reparse_document(
    snapshot_id: str,
    document_id: str,
    request: Request,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    return reparse_staged_document(db, snapshot_id, document_id, role, request.state.correlation_id)


@router.post("/snapshots/{snapshot_id}/discover-documents", status_code=202)
def discover_documents(
    snapshot_id: str,
    request: Request,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    """文档独立发现：只产生候选与来源证据，不审核、不发布。"""
    snapshot = staging(db, snapshot_id)
    if snapshot.index_status != "indexed":
        raise AppError(409, "SOURCE_INDEX_NOT_READY", "必须先完成源码与文档索引")
    if not db.scalar(
        select(Document.id)
        .where(
            Document.snapshot_id == snapshot.id,
            Document.parse_status.in_(["parsed", "partial", "ocr_required"]),
        )
        .limit(1)
    ):
        raise AppError(422, "DOCUMENT_DISCOVERY_NO_DOCUMENTS", "快照中没有可发现的文档")
    try:
        job = KnowledgeJob(
            snapshot_id=snapshot.id,
            category_id=snapshot.category_id,
            job_type="document_discovery",
            stage="documents_queued",
            metrics_json={"source_channel": "document", "model_mode": "PENDING"},
        )
        db.add(job)
        db.flush()
        run = create_run(db, snapshot, job)
        db.add(
            AuditEvent(
                principal_id=role,
                action="documents.discovery.requested",
                resource_id=run.id,
                correlation_id=request.state.correlation_id,
                details_json={"job_id": job.id, "mode": run.mode},
            )
        )
        db.commit()
        return {
            "snapshot_id": snapshot.id,
            "job_id": job.id,
            "run_id": run.id,
            "mode": run.mode,
        }
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "JOB_CONFLICT", "同品类已有在途任务") from exc


@router.get("/jobs/{job_id}/document-discovery")
def document_discovery(job_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    job = db.get(KnowledgeJob, job_id)
    if not job:
        raise AppError(404, "JOB_NOT_FOUND", "任务不存在")
    run = db.scalar(select(DocumentDiscoveryRun).where(DocumentDiscoveryRun.job_id == job_id))
    if not run:
        raise AppError(404, "DISCOVERY_NOT_FOUND", "该任务没有文档发现运行")
    return {
        "run_id": run.id,
        "snapshot_id": run.snapshot_id,
        "mode": run.mode,
        "status": run.status,
        "error_code": run.error_code,
        "metrics": run.metrics_json,
        "coverage": run.coverage_json,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


@router.get("/admin/storage")
def storage(role=Depends(admin), db: Session = Depends(get_db)):
    return scan(get_settings(), db)


@router.get("/snapshots/{snapshot_id}/documents")
def list_documents(snapshot_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "sha256": d.sha256,
            "size": d.size,
            "parse_status": d.parse_status,
            "error_code": d.error_code,
            "parser_version": d.parser_version,
            "parse_details": d.parse_details,
            "origin": d.origin_json,
            "segment_count": db.scalar(
                select(func.count())
                .select_from(DocumentSegment)
                .where(DocumentSegment.document_id == d.id)
            ),
        }
        for d in db.scalars(select(Document).where(Document.snapshot_id == snapshot_id).limit(200))
    ]
