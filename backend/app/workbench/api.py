"""P2.3 评估工作台 API：任务查询、结果详情、人工复核与受控导出。"""

import secrets
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import tender_principal
from app.core.config import get_settings
from app.core.security import AppError
from app.db.models import AuditEvent
from app.db.session import get_db
from app.db.workbench_models import EvaluationExportJob
from app.workbench import results, reviews
from app.workbench.exporter import (
    create_export,
    export_json,
    schedule_export,
)
from app.workbench.schemas import ExportCreate, ReviewCreate

router = APIRouter(prefix="/api/v1/tender/workbench", tags=["P2.3 评估工作台"])

TaskStatus = Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
EXPORT_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.get("/tasks")
def list_tasks(
    status: TaskStatus | None = None,
    category_id: str | None = Query(default=None, max_length=36),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    return results.list_tasks(
        db,
        principal_id,
        status=status,
        category_id=category_id,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )


@router.get("/tasks/{job_id}")
def task_detail(
    job_id: str,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = reviews.owned_job(db, job_id, principal_id)
    return results.task_detail(db, job)


@router.get("/tasks/{job_id}/results")
def task_results(
    job_id: str,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = reviews.owned_job(db, job_id, principal_id)
    return results.results_payload(db, job)


@router.get("/tasks/{job_id}/parameters/{parameter_id}/reviews")
def review_history(
    job_id: str,
    parameter_id: str,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = reviews.owned_job(db, job_id, principal_id)
    return {
        "job_id": job.id,
        "parameter_id": parameter_id,
        "items": reviews.history(db, job, parameter_id),
    }


@router.post("/tasks/{job_id}/parameters/{parameter_id}/review", status_code=201)
def submit_review(
    job_id: str,
    parameter_id: str,
    payload: ReviewCreate,
    request: Request,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = reviews.owned_job(db, job_id, principal_id, lock=True)
    row = reviews.record_review(
        db,
        job=job,
        parameter_id=parameter_id,
        principal_id=principal_id,
        payload=payload,
        correlation_id=request.state.correlation_id,
    )
    return {
        "review": reviews.review_json(row),
        "auto_verdict": row.auto_verdict,
        "review_summary": reviews.review_summary(db, job),
        "review_history": reviews.history(db, job, parameter_id),
    }


@router.get("/tasks/{job_id}/exports")
def list_exports(
    job_id: str,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = reviews.owned_job(db, job_id, principal_id)
    rows = db.scalars(
        select(EvaluationExportJob)
        .where(
            EvaluationExportJob.job_id == job.id,
            EvaluationExportJob.principal_id == principal_id,
        )
        .order_by(EvaluationExportJob.created_at.desc(), EvaluationExportJob.id.desc())
        .limit(100)
    ).all()
    return {"job_id": job.id, "items": [export_json(row) for row in rows]}


@router.post("/tasks/{job_id}/exports", status_code=202)
def start_export(
    job_id: str,
    payload: ExportCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = reviews.owned_job(db, job_id, principal_id)
    export = create_export(db, job, principal_id, payload.format)
    schedule_export(export.id, export.dispatch_token, background_tasks)
    return export_json(export)


def _owned_export(db: Session, export_id: str, principal_id: str):
    row = db.scalar(
        select(EvaluationExportJob).where(
            EvaluationExportJob.id == export_id,
            EvaluationExportJob.principal_id == principal_id,
        )
    )
    if not row:
        raise AppError(404, "EXPORT_NOT_FOUND", "导出任务不存在")
    return row


def _export_path(row: EvaluationExportJob):
    settings = get_settings()
    if not row.storage_key:
        raise AppError(409, "EXPORT_NOT_READY", "导出文件尚未生成")
    store = Path(settings.export_store).resolve()
    path = (store / row.storage_key).resolve()
    if store not in path.parents:
        raise AppError(409, "EXPORT_PATH_INVALID", "导出文件路径无效")
    if not path.is_file():
        raise AppError(409, "EXPORT_FILE_MISSING", "导出文件不可用，请重新生成")
    if path.stat().st_size > settings.export_max_file_bytes:
        raise AppError(409, "EXPORT_FILE_INVALID", "导出文件超过允许大小")
    return path


@router.get("/exports/{export_id}")
def export_detail(
    export_id: str,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    row = _owned_export(db, export_id, principal_id)
    payload = export_json(row)
    payload["file_available"] = bool(
        row.status == "succeeded" and row.storage_key and _path_exists(row)
    )
    return payload


def _path_exists(row: EvaluationExportJob):
    try:
        return _export_path(row).is_file()
    except AppError:
        return False


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: str,
    request: Request,
    token: str = Query(min_length=20, max_length=128),
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    row = _owned_export(db, export_id, principal_id)
    if not secrets.compare_digest(token, row.download_token):
        raise AppError(403, "EXPORT_TOKEN_INVALID", "下载令牌无效")
    if row.status != "succeeded":
        raise AppError(409, "EXPORT_NOT_READY", "导出文件尚未生成")
    path = _export_path(row)
    settings = get_settings()
    if path.stat().st_size > settings.export_max_file_bytes:
        raise AppError(409, "EXPORT_FILE_INVALID", "导出文件超过允许大小")
    db.add(
        AuditEvent(
            principal_id=(principal_id or "unknown")[:32],
            action="evaluation.export.download",
            resource_id=row.job_id,
            correlation_id=(getattr(request.state, "correlation_id", None) or row.id)[:36],
            details_json={"export_id": row.id, "format": row.format},
        )
    )
    db.commit()
    return FileResponse(
        path,
        media_type=EXPORT_MEDIA.get(row.format, "application/octet-stream"),
        filename=row.filename or f"evaluation-report.{row.format}",
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Download-Options": "noopen",
        },
    )
