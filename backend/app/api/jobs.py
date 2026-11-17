from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import JobView
from app.core.auth import admin
from app.core.security import AppError
from app.db.models import AuditEvent, Category, KnowledgeJob, Snapshot
from app.db.session import get_db

router = APIRouter(prefix="/api/v1/jobs", tags=["后台任务"])


@router.get("/{job_id}", response_model=JobView)
def get_job(job_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    job = db.get(KnowledgeJob, job_id)
    if not job:
        raise AppError(404, "JOB_NOT_FOUND", "任务不存在")
    return job


@router.post("/{job_id}/retry", response_model=JobView, status_code=202)
def retry_job(job_id: str, request: Request, role=Depends(admin), db: Session = Depends(get_db)):
    original = db.get(KnowledgeJob, job_id)
    if not original:
        raise AppError(404, "JOB_NOT_FOUND", "任务不存在")
    category_id = original.category_id
    db.rollback()
    try:
        with db.begin():
            # 与上传保持同一锁顺序，串行化同品类的重试/导入。
            db.scalar(select(Category).where(Category.id == category_id).with_for_update())
            job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
            if job.status != "failed":
                raise AppError(409, "RETRY_CONFLICT", "仅失败任务可重试")
            if db.scalar(
                select(KnowledgeJob.id).where(
                    KnowledgeJob.category_id == category_id,
                    KnowledgeJob.status.in_(["queued", "running"]),
                )
            ):
                raise AppError(409, "IMPORT_CONFLICT", "此品类已有导入任务")
            job.status, job.stage, job.error_code = "queued", "uploaded", None
            job.dispatch_token = job.dispatched_at = job.started_at = job.finished_at = None
            job.dispatch_enqueued_at = None
            job.attempt += 1
            job.metrics_json = {"model_mode": "NOT_USED"}
            db.get(Snapshot, job.snapshot_id).status = "uploaded"
            db.add(
                AuditEvent(
                    principal_id=role,
                    action="job.retry",
                    resource_id=job.id,
                    correlation_id=request.state.correlation_id,
                    details_json={"attempt": job.attempt},
                )
            )
        return job
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "IMPORT_CONFLICT", "导入任务冲突") from exc
