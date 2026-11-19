"""P2.2 评估任务服务：固定知识版本、逐参数检查点、失败隔离与结果读取。"""

import copy
import time
import uuid
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import AppError
from app.db.evaluation_models import (
    AtomicEvaluation,
    EvaluationEvidence,
    EvaluationJob,
    ParameterEvaluation,
)
from app.db.models import now
from app.db.session import session_factory
from app.db.tender_models import (
    TenderAtomicRequirement,
    TenderExtractionJob,
    TenderParameter,
)
from app.evaluation.atomic import AtomicEvaluator, ModelAudit
from app.evaluation.explanation import parameter_rationale
from app.evaluation.logic import SUPPORTED, UNKNOWN, UNSUPPORTED, aggregate_parameter
from app.evaluation.retrieval import PinnedKnowledge
from app.knowledge import publisher


def _owned_job(db: Session, job_id: str, principal_id: str, lock: bool = False):
    statement = select(EvaluationJob).where(
        EvaluationJob.id == job_id, EvaluationJob.principal_id == principal_id
    )
    if lock:
        statement = statement.with_for_update()
    job = db.scalar(statement)
    if not job:
        raise AppError(404, "EVALUATION_JOB_NOT_FOUND", "评估任务不存在")
    return job


def _owned_extraction(db: Session, extraction_job_id: str, principal_id: str):
    job = db.scalar(
        select(TenderExtractionJob).where(
            TenderExtractionJob.id == extraction_job_id,
            TenderExtractionJob.principal_id == principal_id,
        )
    )
    if not job:
        raise AppError(404, "TENDER_JOB_NOT_FOUND", "招标提取任务不存在")
    return job


def _tree_leaves(node: dict) -> list[str]:
    if node.get("kind") == "leaf":
        return [node.get("requirement_id")]
    return [leaf_id for child in node.get("children") or [] for leaf_id in _tree_leaves(child)]


def _remap_tree(node: dict, mapping: dict) -> dict:
    """只替换叶子上的提取期 requirement_id；缺失映射保持原样并按无法确认处理。"""
    result = copy.deepcopy(node)
    if result.get("kind") == "leaf":
        mapped = mapping.get(result.get("requirement_id"))
        if mapped:
            result["requirement_id"] = mapped
        return result
    result["children"] = [_remap_tree(child, mapping) for child in result.get("children") or []]
    return result


def _freeze_parameters(db: Session, extraction):
    parameters = list(
        db.scalars(
            select(TenderParameter)
            .where(
                TenderParameter.job_id == extraction.id,
                TenderParameter.status != "superseded",
            )
            .order_by(TenderParameter.order_index)
        )
    )
    if not parameters:
        raise AppError(409, "EVALUATION_NO_PARAMETERS", "招标提取任务没有可评估的技术参数")
    settings = get_settings()
    if len(parameters) > settings.evaluation_max_parameters:
        raise AppError(422, "EVALUATION_PARAMETER_LIMIT", "参数数量超过评估上限")
    snapshot = []
    atomic_total = 0
    for parameter in parameters:
        requirements = list(
            db.scalars(
                select(TenderAtomicRequirement)
                .where(TenderAtomicRequirement.parameter_id == parameter.id)
                .order_by(TenderAtomicRequirement.order_index)
            )
        )
        if not requirements:
            raise AppError(
                409,
                "EVALUATION_REQUIREMENTS_MISSING",
                "参数缺少原子要求，不能生成确定性符合性结论",
            )
        atomic_total += len(requirements)
        if atomic_total > settings.evaluation_max_atomic_requirements:
            raise AppError(422, "EVALUATION_ATOMIC_LIMIT", "原子要求数量超过评估上限")
        # P2.1 的 requirement_id 属于提取时 JSON，数据库行使用自己的主键；
        # 必须按顺序和原文重新绑定，否则 logic_tree 的叶子会全部失配。
        original_items = list(parameter.atomic_requirements or [])
        used = set()
        sources = {}
        for row in requirements:
            match = next(
                (
                    index
                    for index, candidate in enumerate(original_items)
                    if index not in used and candidate.get("original_text") == row.original_text
                ),
                None,
            )
            if match is None:
                position = int(row.order_index or 0) - 1
                if 0 <= position < len(original_items) and position not in used:
                    match = position
            if match is not None:
                used.add(match)
                sources[row.id] = original_items[match]
        items = []
        for requirement in requirements:
            source = sources.get(requirement.id, {})
            items.append(
                {
                    "requirement_id": requirement.id,
                    "order_index": requirement.order_index,
                    "original_text": requirement.original_text,
                    "requirement_type": requirement.requirement_type,
                    "logical_relation": requirement.logical_relation,
                    "logical_group_id": requirement.logical_group_id,
                    "is_mandatory": requirement.is_mandatory,
                    "mandatory_status": source.get("mandatory_status")
                    or ("required" if requirement.is_mandatory else "optional"),
                    "open_enumeration_mode": source.get("open_enumeration_mode"),
                    "example_relation": bool(source.get("example_relation")),
                    "threshold": requirement.threshold,
                    "threshold_operator": source.get("threshold_operator"),
                    "unit": requirement.unit,
                    "conditions": requirement.conditions or [],
                    "ambiguity": requirement.ambiguity,
                    "logic_ambiguity": source.get("logic_ambiguity"),
                    "source_span": requirement.source_span or {},
                }
            )
        tree = parameter.logic_tree or {}
        if not tree:
            tree = {"kind": "node", "relation": "UNKNOWN", "children": []}
        else:
            leaves = _tree_leaves(tree)
            mapping = {
                source.get("requirement_id"): row.id
                for row in requirements
                for source in [sources.get(row.id) or {}]
                if source.get("requirement_id")
            }
            if len(leaves) == len(requirements):
                for leaf_id, row in zip(leaves, requirements, strict=True):
                    mapping.setdefault(leaf_id, row.id)
            tree = _remap_tree(tree, mapping)
        snapshot.append(
            {
                "parameter_id": parameter.id,
                "order_index": parameter.order_index,
                "revision": parameter.revision,
                "parameter_name": parameter.parameter_name,
                "original_text": parameter.original_text,
                "logic_tree": tree,
                "review_required": parameter.review_required,
                "requirements": items,
            }
        )
    return snapshot, atomic_total


def create_job(
    db: Session,
    *,
    principal_id: str,
    extraction_job_id: str,
    idempotency_key: str | None = None,
):
    if idempotency_key:
        existing = db.scalar(
            select(EvaluationJob).where(
                EvaluationJob.principal_id == principal_id,
                EvaluationJob.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.extraction_job_id != extraction_job_id:
                raise AppError(409, "EVALUATION_IDEMPOTENCY_CONFLICT", "幂等键已用于其他招标任务")
            return existing, False
    extraction = _owned_extraction(db, extraction_job_id, principal_id)
    if extraction.status in {"queued", "running"}:
        raise AppError(409, "EVALUATION_EXTRACTION_NOT_READY", "招标提取任务尚未完成")
    if extraction.status == "failed":
        raise AppError(409, "EVALUATION_EXTRACTION_FAILED", "失败的提取任务不能评估")
    settings = get_settings()
    active = (
        db.scalar(
            select(func.count())
            .select_from(EvaluationJob)
            .where(
                EvaluationJob.principal_id == principal_id,
                EvaluationJob.status.in_(["queued", "running"]),
            )
        )
        or 0
    )
    if active >= settings.evaluation_max_active_jobs:
        raise AppError(429, "EVALUATION_ACTIVE_LIMIT", "同时运行的评估任务已达上限")
    snapshot, atomic_total = _freeze_parameters(db, extraction)
    publication = publisher.current(db, extraction.category_id)
    job = EvaluationJob(
        principal_id=principal_id,
        category_id=extraction.category_id,
        extraction_job_id=extraction.id,
        published_snapshot_id=publication.snapshot_id,
        publication_id=publication.id,
        publication_hash=publication.payload_hash,
        parameter_snapshot=snapshot,
        status="queued",
        parameter_count=len(snapshot),
        atomic_count=atomic_total,
        metrics_json={
            "extraction_status": extraction.status,
            "extraction_coverage_status": extraction.coverage_status,
            "publication_pinned_at": now().isoformat(),
            "model": {"status": "NOT_INVOKED", "mode": settings.evaluation_model_mode},
        },
        checkpoint_json={"completed_parameter_ids": [], "completed": 0, "total": len(snapshot)},
        idempotency_key=idempotency_key,
        attempt=1,
        dispatch_token=uuid.uuid4().hex,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if idempotency_key:
            existing = db.scalar(
                select(EvaluationJob).where(
                    EvaluationJob.principal_id == principal_id,
                    EvaluationJob.idempotency_key == idempotency_key,
                )
            )
            if existing:
                return existing, False
        raise AppError(409, "EVALUATION_CREATE_CONFLICT", "评估任务创建冲突") from exc
    return job, True


def _completed_ids(job):
    checkpoint = job.checkpoint_json or {}
    return set(checkpoint.get("completed_parameter_ids") or [])


def _write_parameter(job_id, parameter, atomic_results, aggregate, rationale, details):
    with session_factory().begin() as db:
        job = db.scalar(select(EvaluationJob).where(EvaluationJob.id == job_id).with_for_update())
        if not job:
            return
        existing = db.scalar(
            select(ParameterEvaluation).where(
                ParameterEvaluation.job_id == job_id,
                ParameterEvaluation.parameter_id == parameter["parameter_id"],
            )
        )
        if existing:
            return
        counts = aggregate["counts"]
        row = ParameterEvaluation(
            job_id=job_id,
            parameter_id=parameter["parameter_id"],
            parameter_revision=parameter["revision"],
            parameter_name=parameter["parameter_name"],
            original_text=parameter["original_text"],
            verdict=aggregate["verdict"],
            rationale=rationale,
            atomic_count=len(atomic_results),
            supported_count=counts[SUPPORTED],
            unsupported_count=counts[UNSUPPORTED],
            unknown_count=counts[UNKNOWN],
            evidence_ids=sorted(
                {eid for item in atomic_results for eid in item.get("evidence_ids", [])}
            )[:200],
            logic_json={"root_reason": aggregate.get("reason"), "tree": aggregate.get("tree")},
            details_json=details,
            error_code=details.get("error_code"),
        )
        db.add(row)
        db.flush()
        for result in atomic_results:
            atom = AtomicEvaluation(
                job_id=job_id,
                parameter_evaluation_id=row.id,
                requirement_id=result["requirement_id"],
                order_index=result.get("order_index", 0),
                requirement_text=result["requirement_text"],
                necessary=result.get("necessary", True),
                verdict=result["verdict"],
                reason=result["reason"],
                evidence_ids=result.get("evidence_ids", []),
                assessment_method=result.get("assessment_method", "deterministic"),
                details_json=result.get("details_json", {}),
            )
            db.add(atom)
            db.flush()
            for citation in result.get("citations", [])[:12]:
                db.add(
                    EvaluationEvidence(
                        job_id=job_id,
                        parameter_evaluation_id=row.id,
                        atomic_evaluation_id=atom.id,
                        evidence_id=citation["evidence_id"],
                        capability_id=citation["capability_id"],
                        revision_id=citation["revision_id"],
                        kind=citation["kind"][:16],
                        source_ref=citation.get("source_ref") or {},
                        locator=citation.get("locator") or {},
                        source_hash=citation["source_hash"],
                        quote_hash=citation["quote_hash"],
                        quote=citation.get("quote", "")[:1000],
                        verification_state=citation["verification_state"],
                        verification_json={"verification": "pinned_snapshot_readback"},
                    )
                )
        completed = sorted(_completed_ids(job) | {parameter["parameter_id"]})
        counts_all = Counter()
        rows = db.scalars(
            select(ParameterEvaluation).where(ParameterEvaluation.job_id == job_id)
        ).all()
        for item in rows:
            counts_all[item.verdict] += 1
        job.checkpoint_json = {
            "completed_parameter_ids": completed,
            "completed": len(completed),
            "total": job.parameter_count,
        }
        job.supported_count = counts_all[SUPPORTED]
        job.unsupported_count = counts_all[UNSUPPORTED]
        job.unknown_count = counts_all[UNKNOWN]
        job.gap_count = sum(
            len((result.get("details_json") or {}).get("retrieval_gaps") or [])
            + len((result.get("details_json") or {}).get("model_gaps") or [])
            for result in atomic_results
        ) + int(job.gap_count or 0)


def _write_failed_parameter(job_id, parameter, error_code):
    results = []
    for requirement in parameter.get("requirements", []):
        results.append(
            {
                "requirement_id": requirement["requirement_id"],
                "order_index": requirement.get("order_index", 0),
                "requirement_text": requirement.get("original_text", ""),
                "necessary": bool(requirement.get("is_mandatory", True)),
                "mandatory_status": requirement.get("mandatory_status"),
                "verdict": UNKNOWN,
                "reason": f"单参数评估异常，按无法确认处理（{error_code}）。",
                "assessment_method": "failed_closed",
                "evidence_ids": [],
                "citations": [],
                "details_json": {"error_code": error_code},
            }
        )
    if not results:
        results.append(
            {
                "requirement_id": parameter["requirements"][0]["requirement_id"]
                if parameter.get("requirements")
                else "missing",
                "order_index": 0,
                "requirement_text": parameter["original_text"],
                "necessary": True,
                "mandatory_status": "required",
                "verdict": UNKNOWN,
                "reason": "参数缺少原子要求，按无法确认处理。",
                "assessment_method": "failed_closed",
                "evidence_ids": [],
                "citations": [],
                "details_json": {"error_code": "REQUIREMENTS_MISSING"},
            }
        )
    # 失败隔离仍需一条参数结论；不伪造任何支持。
    with session_factory().begin() as db:
        job = db.get(EvaluationJob, job_id)
        if not job:
            return
        if db.scalar(
            select(ParameterEvaluation.id).where(
                ParameterEvaluation.job_id == job_id,
                ParameterEvaluation.parameter_id == parameter["parameter_id"],
            )
        ):
            return
        row = ParameterEvaluation(
            job_id=job_id,
            parameter_id=parameter["parameter_id"],
            parameter_revision=parameter["revision"],
            parameter_name=parameter["parameter_name"],
            original_text=parameter["original_text"],
            verdict=UNKNOWN,
            rationale=f"参数“{parameter['parameter_name']}”评估过程异常，按无法确认处理；未生成任何支持结论。",
            atomic_count=len(results),
            supported_count=0,
            unsupported_count=0,
            unknown_count=len(results),
            evidence_ids=[],
            logic_json={"root_reason": "parameter_failed_closed", "tree": {}},
            details_json={"error_code": error_code, "failed_closed": True},
            error_code=error_code,
        )
        db.add(row)
        db.flush()
        for result in results:
            atom = AtomicEvaluation(
                job_id=job_id,
                parameter_evaluation_id=row.id,
                requirement_id=result["requirement_id"],
                order_index=result.get("order_index", 0),
                requirement_text=result["requirement_text"],
                necessary=result["necessary"],
                verdict=UNKNOWN,
                reason=result["reason"],
                evidence_ids=[],
                assessment_method="failed_closed",
                details_json=result["details_json"],
            )
            db.add(atom)
        completed = sorted(_completed_ids(job) | {parameter["parameter_id"]})
        job.checkpoint_json = {
            "completed_parameter_ids": completed,
            "completed": len(completed),
            "total": job.parameter_count,
        }
        job.unknown_count = (job.unknown_count or 0) + 1


def _finish(job_id, *, error_code=None):
    with session_factory().begin() as db:
        job = db.scalar(select(EvaluationJob).where(EvaluationJob.id == job_id).with_for_update())
        if not job:
            return
        rows = list(
            db.scalars(select(ParameterEvaluation).where(ParameterEvaluation.job_id == job_id))
        )
        failed = [row for row in rows if row.error_code]
        completed = len(rows)
        job.finished_at = now()
        if error_code:
            job.status = "failed"
            job.error_code = error_code
            return
        if completed < job.parameter_count:
            job.status = "failed"
            job.error_code = "EVALUATION_INCOMPLETE"
            return
        metrics = dict(job.metrics_json or {})
        extraction_partial = metrics.get("extraction_coverage_status") == "incomplete"
        job.status = "partial" if failed or extraction_partial else "succeeded"
        if failed:
            metrics["failed_parameters"] = len(failed)
        job.metrics_json = metrics


def run_evaluation_job(job_id: str, token: str | None = None, *, model=None):
    started = time.monotonic()
    db = session_factory()()
    try:
        job = db.get(EvaluationJob, job_id)
        if not job:
            return None
        if token and job.dispatch_token and token != job.dispatch_token:
            return None
        if job.status not in {"queued", "running"}:
            return None
        job.status = "running"
        job.started_at = job.started_at or now()
        job.attempt = max(1, job.attempt or 1)
        db.commit()
        try:
            knowledge = PinnedKnowledge(db, job)
        except AppError as exc:
            _finish(job_id, error_code=exc.error_code)
            return None
        audit = ModelAudit(job_id, job.principal_id)
        evaluator = AtomicEvaluator(
            knowledge,
            job_id=job_id,
            principal_id=job.principal_id,
            model=model,
            audit=audit,
        )
        completed = _completed_ids(job)
        for parameter in job.parameter_snapshot or []:
            if parameter["parameter_id"] in completed:
                continue
            try:
                atomic_results = evaluator.evaluate(
                    parameter.get("requirements") or [], parameter["parameter_name"]
                )
                aggregate = aggregate_parameter(parameter.get("logic_tree"), atomic_results)
                for index, result in enumerate(atomic_results, 1):
                    result["order_index"] = index
                rationale = parameter_rationale(
                    parameter_name=parameter["parameter_name"],
                    verdict=aggregate["verdict"],
                    aggregate=aggregate,
                    atom_results=atomic_results,
                    publication_id=job.publication_id,
                )
                details = {
                    "aggregate_reason": aggregate.get("reason"),
                    "publication_id": job.publication_id,
                    "publication_hash": job.publication_hash,
                    "published_snapshot_id": job.published_snapshot_id,
                    "parameter_revision": parameter.get("revision"),
                    "model_status": audit.status,
                    "model_mode": audit.mode,
                    "atom_methods": dict(
                        Counter(item.get("assessment_method") for item in atomic_results)
                    ),
                }
                _write_parameter(job_id, parameter, atomic_results, aggregate, rationale, details)
            except Exception:  # noqa: BLE001 - 单参数失败隔离，绝不伪造成支持
                _write_failed_parameter(job_id, parameter, "EVALUATION_PARAMETER_ERROR")
        metrics = dict(job.metrics_json or {})
        metrics.update(
            {
                "model": audit.details(),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "publication_id": job.publication_id,
                "published_snapshot_id": job.published_snapshot_id,
            }
        )
        with session_factory().begin() as write:
            row = write.get(EvaluationJob, job_id)
            if row:
                row.metrics_json = metrics
        _finish(job_id)
        return job_id
    except AppError as exc:
        _finish(job_id, error_code=getattr(exc, "error_code", "EVALUATION_ERROR"))
        return None
    except Exception:  # noqa: BLE001 - 任务错误须显式失败关闭
        _finish(job_id, error_code="EVALUATION_INTERNAL_ERROR")
        return None
    finally:
        db.close()


def retry_job(db: Session, job_id: str, principal_id: str):
    job = _owned_job(db, job_id, principal_id, lock=True)
    if job.status != "failed":
        raise AppError(409, "EVALUATION_JOB_NOT_RETRYABLE", "只有失败任务可以重试")
    completed = _completed_ids(job)
    job.status = "queued"
    job.error_code = None
    job.dispatch_token = uuid.uuid4().hex
    job.attempt = (job.attempt or 1) + 1
    job.checkpoint_json = {
        "completed_parameter_ids": sorted(completed),
        "completed": len(completed),
        "total": job.parameter_count,
    }
    return job


def job_json(job):
    checkpoint = job.checkpoint_json or {}
    metrics = job.metrics_json or {}
    return {
        "job_id": job.id,
        "status": job.status,
        "extraction_job_id": job.extraction_job_id,
        "publication_id": job.publication_id,
        "published_snapshot_id": job.published_snapshot_id,
        "publication_hash": job.publication_hash,
        "parameter_count": job.parameter_count,
        "atomic_count": job.atomic_count,
        "counts": {
            "支持": job.supported_count,
            "不支持": job.unsupported_count,
            "无法确认": job.unknown_count,
        },
        "gap_count": job.gap_count,
        "error_code": job.error_code,
        "checkpoint": {
            "completed": checkpoint.get("completed", 0),
            "total": checkpoint.get("total", job.parameter_count),
        },
        "model": metrics.get("model") or {},
        "metrics": {
            key: value
            for key, value in metrics.items()
            if key not in {"publication_id", "published_snapshot_id"}
        },
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def list_jobs(db: Session, principal_id: str, limit: int):
    rows = db.scalars(
        select(EvaluationJob)
        .where(EvaluationJob.principal_id == principal_id)
        .order_by(EvaluationJob.created_at.desc(), EvaluationJob.id.desc())
        .limit(limit)
    ).all()
    return [job_json(item) for item in rows]


def _parameter_payload(db: Session, row: ParameterEvaluation):
    atoms = list(
        db.scalars(
            select(AtomicEvaluation)
            .where(AtomicEvaluation.parameter_evaluation_id == row.id)
            .order_by(AtomicEvaluation.order_index)
        )
    )
    evidence = list(
        db.scalars(
            select(EvaluationEvidence).where(EvaluationEvidence.parameter_evaluation_id == row.id)
        )
    )
    by_atom = {}
    for item in evidence:
        by_atom.setdefault(item.atomic_evaluation_id, []).append(
            {
                "evidence_id": item.evidence_id,
                "capability_id": item.capability_id,
                "revision_id": item.revision_id,
                "kind": item.kind,
                "source_ref": item.source_ref,
                "locator": item.locator,
                "source_hash": item.source_hash,
                "quote_hash": item.quote_hash,
                "quote": item.quote,
                "verification_state": item.verification_state,
            }
        )
    return {
        "parameter_id": row.parameter_id,
        "parameter_revision": row.parameter_revision,
        "parameter_name": row.parameter_name,
        "original_text": row.original_text,
        "verdict": row.verdict,
        "rationale": row.rationale,
        "counts": {
            "支持": row.supported_count,
            "不支持": row.unsupported_count,
            "无法确认": row.unknown_count,
        },
        "evidence_ids": row.evidence_ids or [],
        "error_code": row.error_code,
        "details": row.details_json or {},
        "atomic_requirements": [
            {
                "requirement_id": item.requirement_id,
                "order_index": item.order_index,
                "requirement_text": item.requirement_text,
                "necessary": item.necessary,
                "verdict": item.verdict,
                "reason": item.reason,
                "evidence_ids": item.evidence_ids or [],
                "assessment_method": item.assessment_method,
                "details": item.details_json or {},
                "evidence": by_atom.get(item.id, []),
            }
            for item in atoms
        ],
    }


def results_json(db: Session, job):
    rows = list(db.scalars(select(ParameterEvaluation).where(ParameterEvaluation.job_id == job.id)))
    order = {item["parameter_id"]: index for index, item in enumerate(job.parameter_snapshot or [])}
    rows.sort(key=lambda item: order.get(item.parameter_id, 999999))
    return {
        "job": job_json(job),
        "parameters": [_parameter_payload(db, row) for row in rows],
    }


def parameter_json(db: Session, job, parameter_id: str):
    row = db.scalar(
        select(ParameterEvaluation).where(
            ParameterEvaluation.job_id == job.id,
            ParameterEvaluation.parameter_id == parameter_id,
        )
    )
    if not row:
        raise AppError(404, "EVALUATION_PARAMETER_NOT_FOUND", "评估结果中没有该参数")
    return _parameter_payload(db, row)


def evidence_json(db: Session, job, parameter_id: str | None = None):
    query = select(EvaluationEvidence).where(EvaluationEvidence.job_id == job.id)
    if parameter_id:
        row = db.scalar(
            select(ParameterEvaluation).where(
                ParameterEvaluation.job_id == job.id,
                ParameterEvaluation.parameter_id == parameter_id,
            )
        )
        if not row:
            raise AppError(404, "EVALUATION_PARAMETER_NOT_FOUND", "评估结果中没有该参数")
        query = query.where(EvaluationEvidence.parameter_evaluation_id == row.id)
    rows = list(db.scalars(query.limit(2000)))
    return {
        "job_id": job.id,
        "publication_id": job.publication_id,
        "published_snapshot_id": job.published_snapshot_id,
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "atomic_evaluation_id": item.atomic_evaluation_id,
                "capability_id": item.capability_id,
                "revision_id": item.revision_id,
                "kind": item.kind,
                "source_ref": item.source_ref,
                "locator": item.locator,
                "source_hash": item.source_hash,
                "quote_hash": item.quote_hash,
                "quote": item.quote,
                "verification_state": item.verification_state,
            }
            for item in rows
        ],
    }
