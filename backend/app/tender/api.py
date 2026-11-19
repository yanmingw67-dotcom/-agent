"""P2.1 招标参数提取 API：任务、进度、结果、覆盖记录和用户修正。"""

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import tender_principal
from app.core.config import get_settings
from app.core.security import AppError
from app.db.session import get_db
from app.tender import service
from app.tender.schemas import (
    AddRequest,
    MergeRequest,
    ParameterEdit,
    SplitRequest,
    TextCreate,
)

router = APIRouter(prefix="/api/v1/tender/extractions", tags=["招标参数提取"])


def _correlation(request: Request):
    return getattr(request.state, "correlation_id", None)


def _schedule(job_id: str, background_tasks: BackgroundTasks):
    settings = get_settings()
    if settings.tender_queue_enabled:
        try:
            from rq import Queue

            from app.jobs.queue import connection

            Queue(settings.tender_queue_name, connection=connection()).enqueue(
                service.run_tender_job,
                job_id,
                job_timeout=settings.tender_job_timeout_seconds,
                result_ttl=3600,
                failure_ttl=86400,
            )
            return
        except Exception:
            # 队列不可用时退回进程内后台任务；任务仍可由重试接口恢复。
            pass
    background_tasks.add_task(service.run_tender_job, job_id)


def _created(job, created: bool):
    return {
        "job_id": job.id,
        "status": job.status,
        "created": created,
        "input_type": job.input_type,
        "coverage_status": job.coverage_status,
    }


@router.post("", status_code=202)
def create_text(
    payload: TextCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job, created = service.create_text_job(
        db,
        principal_id=principal_id,
        category_id=payload.category_id,
        text=payload.text,
        idempotency_key=payload.idempotency_key,
        title=payload.title,
    )
    if created:
        _schedule(job.id, background_tasks)
    return _created(job, created)


@router.post("/upload", status_code=202)
async def create_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    category_id: str = Form(...),
    file: UploadFile = File(...),
    idempotency_key: str | None = Form(default=None),
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    service._validate_category(db, category_id)
    filename, suffix = service.validate_filename(file, settings)
    key = None
    try:
        key, sha, size = await service.receive_upload(file, suffix, settings)
        job, created = service.create_file_job(
            db,
            principal_id=principal_id,
            category_id=category_id,
            key=key,
            file_hash=sha,
            size=size,
            filename=filename,
            suffix=suffix,
            idempotency_key=idempotency_key,
        )
        if created:
            _schedule(job.id, background_tasks)
        return _created(job, created)
    except AppError:
        if key:
            (settings.tender_store / key).unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("")
def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    jobs = service.list_jobs(db, principal_id, limit=limit)
    return {"jobs": [service.job_json(db, job, include_parameters=False) for job in jobs]}


@router.get("/{job_id}")
def get_job(job_id: str, principal_id=Depends(tender_principal), db: Session = Depends(get_db)):
    job = service.get_job(db, job_id, principal_id)
    return service.job_json(db, job)


@router.get("/{job_id}/result")
def get_result(job_id: str, principal_id=Depends(tender_principal), db: Session = Depends(get_db)):
    job = service.get_job(db, job_id, principal_id)
    return service.job_json(db, job)


@router.get("/{job_id}/coverage")
def get_coverage(
    job_id: str,
    disposition: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    service.get_job(db, job_id, principal_id)
    records = service.coverage_json(db, job_id, disposition=disposition, limit=limit, offset=offset)
    return {"job_id": job_id, "records": records, "regions": records}


@router.post("/{job_id}/retry", status_code=202)
def retry_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job = service.retry_job(db, job_id, principal_id)
    _schedule(job.id, background_tasks)
    return _created(job, True)


@router.post("/{job_id}/reextract", status_code=202)
def reextract_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    job, created = service.reextract_job(db, job_id, principal_id)
    if created:
        _schedule(job.id, background_tasks)
    body = _created(job, created)
    body["source_job_id"] = job_id
    return body


@router.patch("/{job_id}/parameters/{parameter_id}")
def edit_parameter(
    job_id: str,
    parameter_id: str,
    payload: ParameterEdit,
    request: Request,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    if payload.parameter_name is None and payload.original_text is None:
        raise AppError(422, "EMPTY_EDIT", "至少需要修改一个字段")
    parameter = service.edit_parameter(
        db,
        job_id,
        parameter_id,
        principal_id,
        parameter_name=payload.parameter_name,
        original_text=payload.original_text,
        correlation_id=_correlation(request),
    )
    return service.parameter_json(parameter)


@router.post("/{job_id}/parameters/{parameter_id}/confirm")
def confirm_parameter(
    job_id: str,
    parameter_id: str,
    request: Request,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    parameter = service.confirm_parameter(
        db, job_id, parameter_id, principal_id, correlation_id=_correlation(request)
    )
    return service.parameter_json(parameter)


@router.post("/{job_id}/parameters/{parameter_id}/split")
def split_parameter(
    job_id: str,
    parameter_id: str,
    payload: SplitRequest,
    request: Request,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    parameters = service.split_parameter(
        db,
        job_id,
        parameter_id,
        principal_id,
        [part.model_dump() for part in payload.parts],
        correlation_id=_correlation(request),
    )
    return {"job_id": job_id, "parameters": [service.parameter_json(p) for p in parameters]}


@router.post("/{job_id}/merge")
def merge_parameters(
    job_id: str,
    payload: MergeRequest,
    request: Request,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    parameter = service.merge_parameters(
        db,
        job_id,
        principal_id,
        payload.parameter_ids,
        parameter_name=payload.parameter_name,
        correlation_id=_correlation(request),
    )
    return service.parameter_json(parameter)


@router.post("/{job_id}/parameters", status_code=201)
def add_parameter(
    job_id: str,
    payload: AddRequest,
    request: Request,
    principal_id=Depends(tender_principal),
    db: Session = Depends(get_db),
):
    parameter = service.add_parameter(
        db,
        job_id,
        principal_id,
        parameter_name=payload.parameter_name,
        original_text=payload.original_text,
        correlation_id=_correlation(request),
    )
    return service.parameter_json(parameter)
