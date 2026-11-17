from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import ImportAccepted
from app.core.auth import admin
from app.core.config import get_settings
from app.core.security import AppError
from app.db.models import AuditEvent, Category, KnowledgeJob, Snapshot
from app.db.session import get_db
from app.ingestion.capacity import release
from app.ingestion.content_store import receive_archive

router = APIRouter(prefix="/api/v1/categories", tags=["安全导入"])


@router.post("/{category_id}/source", response_model=ImportAccepted, status_code=202)
async def upload_source(
    category_id: str,
    request: Request,
    file: UploadFile = File(...),
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    key = None
    committed = False
    try:
        if db.get(Category, category_id) is None:
            raise AppError(404, "CATEGORY_NOT_FOUND", "产品品类不存在")
        db.rollback()
        key, sha256, size = await receive_archive(file, settings)
        with db.begin():
            db.scalar(select(Category).where(Category.id == category_id).with_for_update())
            active = db.scalar(
                select(KnowledgeJob.id).where(
                    KnowledgeJob.category_id == category_id,
                    KnowledgeJob.status.in_(["queued", "running"]),
                )
            )
            if active:
                raise AppError(
                    409, "IMPORT_CONFLICT", "此品类已有导入任务，请等待完成或处理失败任务"
                )
            snapshot = Snapshot(category_id=category_id, source_sha256=sha256, archive_key=key)
            db.add(snapshot)
            db.flush()
            job = KnowledgeJob(
                snapshot_id=snapshot.id,
                category_id=category_id,
                metrics_json={"upload_bytes": size, "model_mode": "NOT_USED"},
            )
            db.add(job)
            db.flush()
            db.add(
                AuditEvent(
                    principal_id=role,
                    action="source.upload",
                    resource_id=snapshot.id,
                    correlation_id=request.state.correlation_id,
                    details_json={"sha256": sha256, "job_id": job.id, "upload_bytes": size},
                )
            )
        committed = True
        return ImportAccepted(snapshot_id=snapshot.id, job_id=job.id)
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "IMPORT_CONFLICT", "导入任务冲突") from exc
    finally:
        await file.close()
        if key and not committed:
            (settings.archive_root / key).unlink(missing_ok=True)
            release(settings, key)
