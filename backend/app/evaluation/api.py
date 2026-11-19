"""P2.2 参数符合性评估 API；所有权来自认证用户，不接受客户端用户 ID。"""

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import tender_principal
from app.core.config import get_settings
from app.db.session import get_db
from app.evaluation import service
from app.evaluation.schemas import EvaluationCreate

router = APIRouter(prefix="/api/v1/tender/evaluations", tags=["参数符合性评估"])


def _schedule(job_id: str, token: str | None, background_tasks: BackgroundTasks):
    settings = get_settings()
    if settings.evaluation_queue_enabled:
        try:
            from rq import Queue

            from app.jobs.queue import connection

            Queue(settings.evaluation_queue_name, connection=connection()).enqueue(
                service.run_evaluation_job,
                job_id,
                token,
                job_timeout=settings.evaluation_job_timeout_seconds,
                result_ttl=3600,
                failure_ttl=86400,
            )
            return
        except Exception:
            # 队列不可用时退回进程内后台任务；任务仍可通过重试接口恢复。
            pass
    background_tasks.add_task(service.run_evaluation_job, job_id, token)


@router.post("", status_code=202)
def create(
    payload: EvaluationCreate,
    background_tasks: BackgroundTasks,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job, created = service.create_job(
        db,
        principal_id=principal_id,
        extraction_job_id=payload.extraction_job_id,
        idempotency_key=payload.idempotency_key,
    )
    if created:
        db.commit()
        _schedule(job.id, job.dispatch_token, background_tasks)
    return {
        "job_id": job.id,
        "status": job.status,
        "created": created,
        "publication_id": job.publication_id,
        "parameter_count": job.parameter_count,
    }


@router.get("")
def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    return {"jobs": service.list_jobs(db, principal_id, limit)}


@router.get("/{job_id}")
def job_detail(
    job_id: str,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = service._owned_job(db, job_id, principal_id)
    return service.job_json(job)


@router.get("/{job_id}/results")
def job_results(
    job_id: str,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = service._owned_job(db, job_id, principal_id)
    return service.results_json(db, job)


@router.get("/{job_id}/parameters/{parameter_id}")
def parameter_result(
    job_id: str,
    parameter_id: str,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = service._owned_job(db, job_id, principal_id)
    return service.parameter_json(db, job, parameter_id)


@router.get("/{job_id}/evidence")
def evidence(
    job_id: str,
    parameter_id: str | None = Query(default=None),
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = service._owned_job(db, job_id, principal_id)
    return service.evidence_json(db, job, parameter_id)


@router.post("/{job_id}/retry", status_code=202)
def retry(
    job_id: str,
    background_tasks: BackgroundTasks,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = service.retry_job(db, job_id, principal_id)
    db.commit()
    _schedule(job.id, job.dispatch_token, background_tasks)
    return {"job_id": job.id, "status": job.status, "retried": True}
