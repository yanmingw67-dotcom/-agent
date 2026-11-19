"""P2.1 招标参数提取任务、执行与用户修正服务。

任务只写入 tender_* 独立表；不创建 CapabilityClaim，不修改 Agent A 的
Snapshot/发布状态。输入正文只在任务记录中保存，不写日志、不响应到日志。
"""

import hashlib
import re
import time
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import AppError
from app.db.models import Category, now
from app.db.session import session_factory
from app.db.tender_models import (
    TenderAtomicRequirement,
    TenderCorrection,
    TenderExtractionJob,
    TenderParameter,
    TenderRegion,
)
from app.tender.rules import build_requirements, extract, normalize
from app.tender.structure import TenderParseError, parse_text, parse_upload

SUFFIXES = {".docx", ".pdf", ".xlsx"}
MIMES = {
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    },
}
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}


def validate_filename(upload: UploadFile, settings: Settings):
    filename = upload.filename or ""
    suffix = Path(filename).suffix.lower()
    if (
        not filename
        or len(filename.encode()) > 240
        or "/" in filename
        or "\\" in filename
        or any(ord(char) < 32 for char in filename)
        or suffix not in SUFFIXES
    ):
        raise AppError(422, "INVALID_TENDER_TYPE", "招标文件文件名或类型不合法")
    if upload.content_type and upload.content_type not in MIMES[suffix]:
        raise AppError(422, "INVALID_TENDER_MEDIA_TYPE", "招标文件 MIME 不受支持")
    return filename, suffix


async def receive_upload(upload: UploadFile, suffix: str, settings: Settings):
    settings.tender_store.mkdir(parents=True, exist_ok=True, mode=0o750)
    key = f"{uuid4()}{suffix}"
    path = settings.tender_store / key
    checksum = hashlib.sha256()
    size = 0
    try:
        with path.open("xb") as output:
            path.chmod(0o640)
            while block := await upload.read(65536):
                if size == 0:
                    if suffix in {".docx", ".xlsx"} and not block.startswith(b"PK\x03\x04"):
                        raise AppError(422, "INVALID_SIGNATURE", "Office 文档内容签名不合法")
                    if suffix == ".pdf" and not block.startswith(b"%PDF-"):
                        raise AppError(422, "INVALID_SIGNATURE", "PDF 内容签名不合法")
                size += len(block)
                if size > settings.tender_max_file_bytes:
                    raise AppError(413, "TENDER_FILE_TOO_LARGE", "招标文件超过上传限额")
                checksum.update(block)
                output.write(block)
        if not size:
            raise AppError(422, "EMPTY_TENDER_FILE", "招标文件为空")
        path.chmod(0o440)
        return key, checksum.hexdigest(), size
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _validate_category(db: Session, category_id: str):
    category = db.get(Category, category_id)
    if not category:
        raise AppError(404, "CATEGORY_NOT_FOUND", "产品品类不存在")
    return category


def _active_count(db: Session, principal_id: str):
    return db.scalar(
        select(func.count())
        .select_from(TenderExtractionJob)
        .where(
            TenderExtractionJob.principal_id == principal_id,
            TenderExtractionJob.status.in_(["queued", "running"]),
        )
    )


def _existing_idempotent(db: Session, principal_id: str, key: str | None):
    if not key:
        return None
    return db.scalar(
        select(TenderExtractionJob).where(
            TenderExtractionJob.principal_id == principal_id,
            TenderExtractionJob.idempotency_key == key,
        )
    )


def _new_job(
    db: Session,
    *,
    principal_id: str,
    category_id: str,
    input_type: str,
    idempotency_key: str | None,
    metadata: dict,
    filename: str | None = None,
    file_hash: str | None = None,
    storage_key: str | None = None,
    text: str | None = None,
):
    _validate_category(db, category_id)
    existing = _existing_idempotent(db, principal_id, idempotency_key)
    if existing:
        return existing, False
    if _active_count(db, principal_id) >= get_settings().tender_max_active_jobs:
        raise AppError(429, "TENDER_JOB_LIMIT", "招标提取并发任务已达上限")
    job = TenderExtractionJob(
        principal_id=principal_id,
        category_id=category_id,
        input_type=input_type,
        status="queued",
        original_filename=filename,
        original_file_hash=file_hash,
        original_storage_key=storage_key,
        original_text=text,
        input_metadata=metadata,
        idempotency_key=idempotency_key,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _existing_idempotent(db, principal_id, idempotency_key)
        if existing:
            return existing, False
        raise AppError(409, "TENDER_JOB_CONFLICT", "招标提取任务并发冲突") from None
    return job, True


def create_text_job(
    db: Session,
    *,
    principal_id: str,
    category_id: str,
    text: str,
    idempotency_key: str | None = None,
    title: str | None = None,
):
    value = (text or "").strip()
    if not value:
        raise AppError(422, "EMPTY_TENDER_TEXT", "招标文本不能为空")
    if "\x00" in value:
        raise AppError(422, "INVALID_TENDER_TEXT", "招标文本包含非法字符")
    settings = get_settings()
    if len(value) > settings.tender_max_text_chars:
        raise AppError(413, "TENDER_TEXT_TOO_LARGE", "招标文本超过处理限额")
    job, created = _new_job(
        db,
        principal_id=principal_id,
        category_id=category_id,
        input_type="text",
        idempotency_key=idempotency_key,
        metadata={"characters": len(value), "title": (title or "")[:200]},
        text=value,
    )
    return job, created


def create_file_job(
    db: Session,
    *,
    principal_id: str,
    category_id: str,
    key: str,
    file_hash: str,
    size: int,
    filename: str,
    suffix: str,
    idempotency_key: str | None = None,
):
    job, created = _new_job(
        db,
        principal_id=principal_id,
        category_id=category_id,
        input_type=suffix.lstrip("."),
        idempotency_key=idempotency_key,
        metadata={"bytes": size, "suffix": suffix},
        filename=filename,
        file_hash=file_hash,
        storage_key=key,
    )
    if not created:
        (get_settings().tender_store / key).unlink(missing_ok=True)
    return job, created


def _load_for_run(job_id: str, token: str | None):
    with session_factory().begin() as db:
        job = db.scalar(
            select(TenderExtractionJob).where(TenderExtractionJob.id == job_id).with_for_update()
        )
        if not job:
            return None
        if job.status in TERMINAL:
            return None
        if token and job.dispatch_token and token != job.dispatch_token:
            return None
        job.status = "running"
        job.started_at = now()
        job.finished_at = None
        job.error_code = None
        job.dispatch_token = token or job.dispatch_token or str(uuid4())
        return {
            "id": job.id,
            "principal_id": job.principal_id,
            "input_type": job.input_type,
            "text": job.original_text,
            "storage_key": job.original_storage_key,
            "attempt": job.attempt,
        }


def _mark_failed(job_id: str, code: str):
    with session_factory().begin() as db:
        job = db.get(TenderExtractionJob, job_id)
        if not job:
            return
        job.status = "failed"
        job.error_code = code[:64]
        job.coverage_status = "incomplete"
        job.coverage_json = {"coverage_status": "incomplete", "error_code": code[:64]}
        job.finished_at = now()


def _replace_results(db: Session, job: TenderExtractionJob, result, elapsed_ms: int):
    db.execute(delete(TenderAtomicRequirement).where(TenderAtomicRequirement.job_id == job.id))
    db.execute(delete(TenderRegion).where(TenderRegion.job_id == job.id))
    db.execute(delete(TenderParameter).where(TenderParameter.job_id == job.id))
    parameter_ids = {}
    requirement_count = 0
    for order_index, candidate in enumerate(result.candidates, 1):
        parameter = TenderParameter(
            job_id=job.id,
            order_index=order_index,
            original_number=candidate.original_number,
            parameter_name=candidate.parameter_name[:500],
            original_text=candidate.original_text,
            normalized_text=candidate.normalized_text,
            source_locator=candidate.source_locator,
            extraction_method=candidate.extraction_method,
            extraction_confidence=candidate.extraction_confidence,
            review_required=candidate.review_required,
            atomic_requirements=candidate.atomic_requirements,
            logic_tree=candidate.logic_tree,
            original_json={
                "section": candidate.section,
                "extra_locators": candidate.extra_locators,
                "source_locator": candidate.source_locator,
                "model_hints": candidate.model_hints,
            },
            status="extracted",
            revision=1,
        )
        db.add(parameter)
        db.flush()
        parameter_ids[id(candidate)] = parameter.id
        for req_index, requirement in enumerate(candidate.atomic_requirements, 1):
            requirement_count += 1
            db.add(
                TenderAtomicRequirement(
                    job_id=job.id,
                    parameter_id=parameter.id,
                    order_index=req_index,
                    original_text=requirement["original_text"],
                    requirement_type=requirement["requirement_type"][:40],
                    logical_relation=requirement["logical_relation"][:16],
                    logical_group_id=requirement.get("logical_group_id"),
                    is_mandatory=bool(requirement.get("is_mandatory")),
                    threshold=(requirement.get("threshold") or None),
                    unit=(requirement.get("unit") or None),
                    conditions=requirement.get("conditions") or [],
                    ambiguity=requirement.get("ambiguity"),
                    source_span=requirement.get("source_span") or {},
                    parent_requirement_id=requirement.get("parent_requirement_id"),
                    depth=requirement.get("depth") or 0,
                )
            )
    for record in result.coverage:
        db.add(
            TenderRegion(
                job_id=job.id,
                parameter_id=parameter_ids.get(id(record.parameter)) if record.parameter else None,
                disposition=record.disposition,
                region_kind=record.region_kind[:32],
                original_text=record.original_text or "",
                source_locator=record.source_locator or {},
                reason_code=(record.reason_code or None),
                reason=(record.reason or None)[:500] if record.reason else None,
                confidence=record.confidence,
            )
        )
    job.extracted_count = len(result.candidates)
    job.skipped_count = result.skipped_count
    job.ambiguous_count = result.ambiguous_count
    job.coverage_status = result.coverage_status
    job.coverage_json = result.details
    job.metrics_json = {
        "elapsed_ms": elapsed_ms,
        "requirements": requirement_count,
        "regions": result.details.get("regions", 0),
    }
    if result.candidates and result.coverage_status == "complete":
        job.status = "succeeded"
    elif result.coverage_status == "incomplete":
        job.status = "partial"
    else:
        job.status = "failed"
        job.error_code = "NO_TECHNICAL_PARAMETERS"
    job.finished_at = now()


def run_tender_job(job_id: str, token: str | None = None, *, model=None):
    settings = get_settings()
    started = time.monotonic()
    data = _load_for_run(job_id, token)
    if not data:
        return None
    try:
        if data["input_type"] == "text":
            parsed = parse_text(data["text"] or "")
        else:
            key = data["storage_key"] or ""
            path = settings.tender_store / Path(key).name
            if not path.exists():
                raise TenderParseError("FILE_NOT_FOUND")
            parsed = parse_upload(path, settings)
        text_chars = sum(len(region.text) for region in parsed.regions)
        if text_chars > settings.tender_max_text_chars:
            raise TenderParseError("TEXT_LIMIT")
        result = extract(parsed, max_parameters=settings.tender_max_parameters)
        if settings.tender_model_mode != "structural" or model is not None:
            from app.tender.model import refine_tender

            result = refine_tender(
                result,
                parsed,
                job_id,
                data["principal_id"],
                model=model,
            )
        with session_factory().begin() as db:
            job = db.scalar(
                select(TenderExtractionJob)
                .where(TenderExtractionJob.id == job_id)
                .with_for_update()
            )
            if not job or job.status != "running":
                return None
            _replace_results(db, job, result, int((time.monotonic() - started) * 1000))
        return job_id
    except TenderParseError as exc:
        _mark_failed(job_id, exc.code)
        return None
    except AppError as exc:
        _mark_failed(job_id, exc.error_code)
        return None
    except Exception:
        _mark_failed(job_id, "TENDER_INTERNAL_ERROR")
        return None


def _owned_job(db: Session, job_id: str, principal_id: str, lock: bool = False):
    statement = select(TenderExtractionJob).where(
        TenderExtractionJob.id == job_id,
        TenderExtractionJob.principal_id == principal_id,
    )
    if lock:
        statement = statement.with_for_update()
    job = db.scalar(statement)
    if not job:
        raise AppError(404, "TENDER_JOB_NOT_FOUND", "招标提取任务不存在")
    return job


def _parameter(db: Session, job_id: str, parameter_id: str, lock: bool = False):
    statement = select(TenderParameter).where(
        TenderParameter.id == parameter_id,
        TenderParameter.job_id == job_id,
    )
    if lock:
        statement = statement.with_for_update()
    parameter = db.scalar(statement)
    if not parameter:
        raise AppError(404, "TENDER_PARAMETER_NOT_FOUND", "参数不存在")
    return parameter


def parameter_json(parameter: TenderParameter):
    return {
        "id": parameter.id,
        "order_index": parameter.order_index,
        "original_number": parameter.original_number,
        "parameter_name": parameter.parameter_name,
        "original_text": parameter.original_text,
        "normalized_text": parameter.normalized_text,
        "source_locator": parameter.source_locator,
        "extraction_method": parameter.extraction_method,
        "extraction_confidence": parameter.extraction_confidence,
        "review_required": parameter.review_required,
        "atomic_requirements": parameter.atomic_requirements,
        "logic_tree": parameter.logic_tree,
        "model_hints": (parameter.original_json or {}).get("model_hints", []),
        "status": parameter.status,
        "revision": parameter.revision,
        "superseded_by": parameter.superseded_by,
    }


def job_json(db: Session, job: TenderExtractionJob, *, include_parameters: bool = True):
    parameters = []
    if include_parameters:
        rows = db.scalars(
            select(TenderParameter)
            .where(
                TenderParameter.job_id == job.id,
                TenderParameter.status != "superseded",
            )
            .order_by(TenderParameter.order_index)
        ).all()
        parameters = [parameter_json(parameter) for parameter in rows]
    return {
        "job_id": job.id,
        "status": job.status,
        "input_type": job.input_type,
        "category_id": job.category_id,
        "original_filename": job.original_filename,
        "original_file_hash": job.original_file_hash,
        "extracted_count": job.extracted_count,
        "skipped_count": job.skipped_count,
        "ambiguous_count": job.ambiguous_count,
        "coverage_status": job.coverage_status,
        "coverage_incomplete": job.coverage_status != "complete",
        "coverage": job.coverage_json,
        "metrics": job.metrics_json,
        "error_code": job.error_code,
        "review_required": (
            job.coverage_status != "complete"
            or any(parameter["review_required"] for parameter in parameters)
        ),
        "parameters": parameters,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def get_job(db: Session, job_id: str, principal_id: str):
    return _owned_job(db, job_id, principal_id)


def list_jobs(db: Session, principal_id: str, limit: int = 20):
    statement = (
        select(TenderExtractionJob)
        .where(TenderExtractionJob.principal_id == principal_id)
        .order_by(TenderExtractionJob.created_at.desc())
    )
    return db.scalars(statement.limit(min(max(limit, 1), 100))).all()


def coverage_json(
    db: Session, job_id: str, disposition: str | None = None, limit: int = 100, offset: int = 0
):
    statement = select(TenderRegion).where(TenderRegion.job_id == job_id)
    if disposition:
        statement = statement.where(TenderRegion.disposition == disposition)
    rows = db.scalars(
        statement.order_by(TenderRegion.created_at, TenderRegion.id)
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 500))
    ).all()
    return [
        {
            "id": row.id,
            "parameter_id": row.parameter_id,
            "disposition": row.disposition,
            "region_kind": row.region_kind,
            "original_text": row.original_text,
            "source_locator": row.source_locator,
            "reason_code": row.reason_code,
            "reason": row.reason,
            "confidence": row.confidence,
        }
        for row in rows
    ]


def _display_name(text: str):
    value = re.sub(r"^\s*(?:\d{1,3}\s*[、.．)]\s*)", "", (text or "").strip())
    value = re.split(r"[。；;\n]", value, maxsplit=1)[0].strip()
    return value[:120] or "用户修正参数"


def _before(parameter: TenderParameter):
    return {
        "parameter_id": parameter.id,
        "parameter_name": parameter.parameter_name,
        "original_text": parameter.original_text,
        "normalized_text": parameter.normalized_text,
        "status": parameter.status,
        "atomic_requirements": parameter.atomic_requirements,
    }


def _after(parameter: TenderParameter):
    return {
        "parameter_id": parameter.id,
        "parameter_name": parameter.parameter_name,
        "original_text": parameter.original_text,
        "normalized_text": parameter.normalized_text,
        "status": parameter.status,
        "atomic_requirements": parameter.atomic_requirements,
    }


def _write_requirements(db: Session, job_id: str, parameter: TenderParameter, text: str):
    requirements, tree = build_requirements(text)
    parameter.atomic_requirements = requirements
    parameter.logic_tree = tree
    db.execute(
        delete(TenderAtomicRequirement).where(TenderAtomicRequirement.parameter_id == parameter.id)
    )
    for index, requirement in enumerate(requirements, 1):
        db.add(
            TenderAtomicRequirement(
                job_id=job_id,
                parameter_id=parameter.id,
                order_index=index,
                original_text=requirement["original_text"],
                requirement_type=requirement["requirement_type"][:40],
                logical_relation=requirement["logical_relation"][:16],
                logical_group_id=requirement.get("logical_group_id"),
                is_mandatory=bool(requirement.get("is_mandatory")),
                threshold=requirement.get("threshold"),
                unit=requirement.get("unit"),
                conditions=requirement.get("conditions") or [],
                ambiguity=requirement.get("ambiguity"),
                source_span=requirement.get("source_span") or {},
                parent_requirement_id=requirement.get("parent_requirement_id"),
                depth=requirement.get("depth") or 0,
            )
        )


def _shift_orders(db: Session, job_id: str):
    current_max = (
        db.scalar(
            select(func.max(TenderParameter.order_index)).where(TenderParameter.job_id == job_id)
        )
        or 0
    )
    offset = max(int(current_max), 0) + 1_000_000
    db.execute(
        update(TenderParameter)
        .where(TenderParameter.job_id == job_id)
        .values(order_index=TenderParameter.order_index + offset)
    )


def _renumber(db: Session, job_id: str, ordered_ids: list[str]):
    _shift_orders(db, job_id)
    for index, parameter_id in enumerate(ordered_ids, 1):
        db.execute(
            update(TenderParameter)
            .where(
                TenderParameter.id == parameter_id,
                TenderParameter.job_id == job_id,
            )
            .values(order_index=index)
        )


def _audit(
    db: Session,
    job_id: str,
    parameter_id: str | None,
    actor: str,
    action: str,
    correlation_id: str | None,
    before: dict,
    after: dict,
):
    db.add(
        TenderCorrection(
            job_id=job_id,
            parameter_id=parameter_id,
            actor=actor,
            action=action,
            correlation_id=correlation_id,
            before_json=before,
            after_json=after,
        )
    )


def _require_editable(db: Session, job_id: str, principal_id: str):
    job = _owned_job(db, job_id, principal_id, lock=True)
    if job.status in {"queued", "running"}:
        raise AppError(409, "TENDER_JOB_RUNNING", "任务尚未完成，不能修改参数")
    return job


def edit_parameter(
    db: Session,
    job_id: str,
    parameter_id: str,
    principal_id: str,
    *,
    parameter_name: str | None,
    original_text: str | None,
    correlation_id: str | None = None,
):
    job = _require_editable(db, job_id, principal_id)
    parameter = _parameter(db, job.id, parameter_id, lock=True)
    if parameter.status == "superseded":
        raise AppError(409, "TENDER_PARAMETER_SUPERSEDED", "已合并或拆分的参数不能直接修改")
    before = _before(parameter)
    if parameter_name is not None:
        value = parameter_name.strip()
        if not value:
            raise AppError(422, "EMPTY_PARAMETER_NAME", "参数名称不能为空")
        parameter.parameter_name = value[:500]
    if original_text is not None:
        value = original_text.strip()
        if not value:
            raise AppError(422, "EMPTY_PARAMETER_TEXT", "参数正文不能为空")
        parameter.original_text = value
        parameter.normalized_text = normalize(value)
        _write_requirements(db, job.id, parameter, value)
    parameter.status = "corrected"
    parameter.revision += 1
    db.flush()
    _audit(
        db, job.id, parameter.id, principal_id, "edit", correlation_id, before, _after(parameter)
    )
    db.commit()
    return parameter


def confirm_parameter(
    db: Session,
    job_id: str,
    parameter_id: str,
    principal_id: str,
    correlation_id: str | None = None,
):
    job = _require_editable(db, job_id, principal_id)
    parameter = _parameter(db, job.id, parameter_id, lock=True)
    if parameter.status == "superseded":
        raise AppError(409, "TENDER_PARAMETER_SUPERSEDED", "已合并或拆分的参数不能确认")
    before = _before(parameter)
    parameter.status = "confirmed"
    db.flush()
    _audit(
        db, job.id, parameter.id, principal_id, "confirm", correlation_id, before, _after(parameter)
    )
    db.commit()
    return parameter


def add_parameter(
    db: Session,
    job_id: str,
    principal_id: str,
    *,
    parameter_name: str | None,
    original_text: str,
    correlation_id: str | None = None,
):
    job = _require_editable(db, job_id, principal_id)
    value = (original_text or "").strip()
    if not value:
        raise AppError(422, "EMPTY_PARAMETER_TEXT", "参数正文不能为空")
    current_max = (
        db.scalar(
            select(func.max(TenderParameter.order_index)).where(TenderParameter.job_id == job.id)
        )
        or 0
    )
    parameter = TenderParameter(
        job_id=job.id,
        order_index=current_max + 1,
        parameter_name=(parameter_name or _display_name(value))[:500],
        original_text=value,
        normalized_text=normalize(value),
        source_locator={"kind": "user", "section": "用户新增"},
        extraction_method="user_added",
        extraction_confidence="user",
        review_required=False,
        status="corrected",
        revision=1,
    )
    db.add(parameter)
    db.flush()
    _write_requirements(db, job.id, parameter, value)
    _audit(db, job.id, parameter.id, principal_id, "add", correlation_id, {}, _after(parameter))
    db.commit()
    return parameter


def split_parameter(
    db: Session,
    job_id: str,
    parameter_id: str,
    principal_id: str,
    parts: list[dict],
    correlation_id: str | None = None,
):
    job = _require_editable(db, job_id, principal_id)
    parameter = _parameter(db, job.id, parameter_id, lock=True)
    if parameter.status == "superseded":
        raise AppError(409, "TENDER_PARAMETER_SUPERSEDED", "已合并或拆分的参数不能再次拆分")
    if len(parts) < 2 or len(parts) > 50:
        raise AppError(422, "INVALID_SPLIT", "拆分至少需要两个部分")
    values = []
    for part in parts:
        text = str(part.get("original_text") or "").strip()
        if not text:
            raise AppError(422, "INVALID_SPLIT", "拆分部分不能为空")
        values.append((str(part.get("parameter_name") or _display_name(text))[:500], text))
    all_parameters = db.scalars(
        select(TenderParameter)
        .where(TenderParameter.job_id == job.id)
        .order_by(TenderParameter.order_index)
    ).all()
    original_order = parameter.order_index
    before = _before(parameter)
    # 先把既有顺序整体后移，避免拆分新增行与原下一行发生唯一约束冲突。
    _shift_orders(db, job.id)
    parameter.status = "superseded"
    new_parameters = []
    for index, (name, text) in enumerate(values, 1):
        new_parameter = TenderParameter(
            job_id=job.id,
            order_index=original_order + index,
            parameter_name=name,
            original_text=text,
            normalized_text=normalize(text),
            source_locator={**parameter.source_locator, "split_from": parameter.id},
            extraction_method="user_split",
            extraction_confidence="user",
            review_required=False,
            status="corrected",
            revision=1,
        )
        db.add(new_parameter)
        db.flush()
        _write_requirements(db, job.id, new_parameter, text)
        new_parameters.append(new_parameter)
    parameter.superseded_by = new_parameters[0].id
    ordered = [p.id for p in all_parameters if p.id != parameter.id]
    insert_at = min(max(original_order - 1, 0), len(ordered))
    ordered[insert_at:insert_at] = [p.id for p in new_parameters]
    ordered.append(parameter.id)
    _renumber(db, job.id, ordered)
    _audit(
        db,
        job.id,
        parameter.id,
        principal_id,
        "split",
        correlation_id,
        before,
        {"parameter_ids": [p.id for p in new_parameters]},
    )
    db.commit()
    return new_parameters


def merge_parameters(
    db: Session,
    job_id: str,
    principal_id: str,
    parameter_ids: list[str],
    *,
    parameter_name: str | None = None,
    correlation_id: str | None = None,
):
    job = _require_editable(db, job_id, principal_id)
    unique_ids = list(dict.fromkeys(parameter_ids))
    if len(unique_ids) < 2 or len(unique_ids) > 50:
        raise AppError(422, "INVALID_MERGE", "合并至少需要两个参数")
    parameters = [_parameter(db, job.id, parameter_id, lock=True) for parameter_id in unique_ids]
    if any(parameter.status == "superseded" for parameter in parameters):
        raise AppError(409, "TENDER_PARAMETER_SUPERSEDED", "不能合并已失效参数")
    target = min(parameters, key=lambda item: item.order_index)
    before = {"parameter_ids": [p.id for p in parameters], "target": _before(target)}
    merged_text = "\n".join(parameter.original_text for parameter in parameters)
    target.original_text = merged_text
    target.normalized_text = normalize(merged_text)
    target.parameter_name = (parameter_name or target.parameter_name)[:500]
    target.status = "corrected"
    target.revision += 1
    _write_requirements(db, job.id, target, merged_text)
    for parameter in parameters:
        if parameter.id != target.id:
            parameter.status = "superseded"
            parameter.superseded_by = target.id
    active = db.scalars(
        select(TenderParameter)
        .where(TenderParameter.job_id == job.id, TenderParameter.status != "superseded")
        .order_by(TenderParameter.order_index)
    ).all()
    ordered = [p.id for p in active]
    ordered.extend(
        p.id for p in sorted(parameters, key=lambda item: item.order_index) if p.id not in ordered
    )
    _renumber(db, job.id, ordered)
    _audit(
        db,
        job.id,
        target.id,
        principal_id,
        "merge",
        correlation_id,
        before,
        {"parameter_id": target.id, "merged_ids": unique_ids},
    )
    db.commit()
    return target


def _manual_change_count(db: Session, job_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(TenderParameter)
            .where(
                TenderParameter.job_id == job_id,
                TenderParameter.status.in_(["corrected", "confirmed", "superseded"]),
            )
        )
        or 0
    )


def retry_job(db: Session, job_id: str, principal_id: str):
    """失败重试：只在没有人工结果时恢复同一任务。

    已成功/部分成功、或存在修正/确认/拆分结果时，普通重试不会覆盖任何参数；
    用户应调用 reextract_job 创建新任务，旧任务及其修正审计保持可查。
    """
    job = _owned_job(db, job_id, principal_id, lock=True)
    if job.status != "failed":
        raise AppError(
            409,
            "TENDER_JOB_USE_REEXTRACT",
            "当前任务不是失败任务；主动重新提取请创建新的重新提取任务",
        )
    if _manual_change_count(db, job.id):
        raise AppError(
            409,
            "TENDER_JOB_HAS_MANUAL_CHANGES",
            "任务包含人工修正或确认结果，普通重试不会覆盖；请创建新的重新提取任务",
        )
    if (
        db.scalar(
            select(func.count())
            .select_from(TenderParameter)
            .where(TenderParameter.job_id == job.id)
        )
        or 0
    ):
        raise AppError(
            409,
            "TENDER_JOB_HAS_RESULTS_USE_REEXTRACT",
            "失败任务已有结果记录，普通重试不会重建主键；请创建新的重新提取任务",
        )
    job.status = "queued"
    job.attempt += 1
    job.started_at = None
    job.finished_at = None
    job.error_code = None
    db.commit()
    return job


def reextract_job(db: Session, job_id: str, principal_id: str):
    """用户主动重新提取：新建任务，旧任务、参数 ID 和修正审计原样保留。"""
    original = _owned_job(db, job_id, principal_id)
    if original.status in {"queued", "running"}:
        raise AppError(409, "TENDER_JOB_RUNNING", "任务尚未完成，不能重新提取")
    metadata = dict(original.input_metadata or {})
    metadata.pop("title", None)
    metadata["reextract_of"] = original.id
    job, created = _new_job(
        db,
        principal_id=principal_id,
        category_id=original.category_id,
        input_type=original.input_type,
        idempotency_key=None,
        metadata=metadata,
        filename=original.original_filename,
        file_hash=original.original_file_hash,
        storage_key=original.original_storage_key,
        text=original.original_text,
    )
    return job, created
