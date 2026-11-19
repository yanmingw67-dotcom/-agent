"""P2.3 任务首页、结果详情与复核状态聚合。

只读取 P2.2 已持久化的自动结论、原子判断与证据；人工复核来自只追加表。
页面显示的人工应答与系统自动结论始终分字段返回，前端不得合并成一个结论。
"""

from datetime import datetime

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db.evaluation_models import EvaluationJob, ParameterEvaluation
from app.db.models import Category
from app.db.tender_models import TenderExtractionJob, TenderParameter
from app.db.workbench_models import EvaluationReview
from app.evaluation.service import _parameter_payload, job_json
from app.workbench.context import enrich_evidence, pinned_capabilities
from app.workbench.reviews import (
    STATUS_LABELS,
    latest_reviews,
    review_json,
    review_summary,
)

REVIEWABLE = {"succeeded", "partial"}
REVIEW_STATUS_LABELS = {"pending": "待复核", **STATUS_LABELS}


def _category_name(db: Session, category_id: str):
    return db.scalar(select(Category.name).where(Category.id == category_id)) or ""


def _extraction_summary(db: Session, job: EvaluationJob):
    extraction = db.get(TenderExtractionJob, job.extraction_job_id)
    if not extraction:
        return None, 0
    review_required = db.scalar(
        select(func.count(TenderParameter.id)).where(
            TenderParameter.job_id == extraction.id,
            TenderParameter.status != "superseded",
            TenderParameter.review_required.is_(True),
        )
    )
    return (
        {
            "extraction_job_id": extraction.id,
            "status": extraction.status,
            "coverage_status": extraction.coverage_status,
            "extracted_count": extraction.extracted_count,
            "skipped_count": extraction.skipped_count,
            "ambiguous_count": extraction.ambiguous_count,
            "review_required_count": review_required or 0,
            "error_code": extraction.error_code,
        },
        review_required or 0,
    )


def _warnings(job: EvaluationJob, extraction: dict | None, review_required: int, failed: int):
    warnings = []
    if extraction:
        if extraction["coverage_status"] != "complete":
            warnings.append(
                {
                    "code": "EXTRACTION_COVERAGE_INCOMPLETE",
                    "message": "招标文件提取覆盖不完整，可能存在未识别条款，请核对原文。",
                }
            )
        if extraction["ambiguous_count"]:
            warnings.append(
                {
                    "code": "EXTRACTION_AMBIGUOUS",
                    "message": f"有 {extraction['ambiguous_count']} 个区域存在歧义，需人工确认。",
                }
            )
        if review_required:
            warnings.append(
                {
                    "code": "PARAMETER_REVIEW_REQUIRED",
                    "message": f"有 {review_required} 条参数标记为需人工确认。",
                }
            )
    if failed:
        warnings.append(
            {
                "code": "PARAMETER_EVALUATION_FAILED",
                "message": f"有 {failed} 条参数评估失败并按“无法确认”关闭，不能视为支持。",
            }
        )
    if job.error_code:
        warnings.append(
            {
                "code": "EVALUATION_JOB_ERROR",
                "message": f"评估任务存在错误码 {job.error_code}，请查看失败原因。",
            }
        )
    return warnings


def _aggregates(db: Session, job_ids: list[str]):
    stats = {
        job_id: {"支持": 0, "不支持": 0, "无法确认": 0, "failed": 0, "rows": 0}
        for job_id in job_ids
    }
    if not job_ids:
        return stats
    rows = db.execute(
        select(
            ParameterEvaluation.job_id,
            ParameterEvaluation.verdict,
            ParameterEvaluation.error_code,
        ).where(ParameterEvaluation.job_id.in_(job_ids))
    ).all()
    for job_id, verdict, error_code in rows:
        bucket = stats[job_id]
        bucket["rows"] += 1
        if verdict in bucket:
            bucket[verdict] += 1
        if error_code:
            bucket["failed"] += 1
    return stats


def _review_counts(db: Session, job_ids: list[str]):
    if not job_ids:
        return {}
    rows = db.execute(
        select(EvaluationReview.job_id, func.count(distinct(EvaluationReview.parameter_id)))
        .where(EvaluationReview.job_id.in_(job_ids))
        .group_by(EvaluationReview.job_id)
    ).all()
    return {job_id: count for job_id, count in rows}


def list_tasks(
    db: Session,
    principal_id: str,
    *,
    status: str | None = None,
    category_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
):
    query = (
        select(EvaluationJob, Category.name)
        .join(Category, Category.id == EvaluationJob.category_id)
        .where(EvaluationJob.principal_id == principal_id)
    )
    if status:
        query = query.where(EvaluationJob.status == status)
    if category_id:
        query = query.where(EvaluationJob.category_id == category_id)
    if created_from:
        query = query.where(EvaluationJob.created_at >= created_from)
    if created_to:
        query = query.where(EvaluationJob.created_at <= created_to)
    rows = db.execute(
        query.order_by(EvaluationJob.created_at.desc(), EvaluationJob.id.desc())
        .offset(max(0, offset))
        .limit(max(1, min(200, limit)))
    ).all()
    job_ids = [job.id for job, _ in rows]
    stats = _aggregates(db, job_ids)
    reviewed = _review_counts(db, job_ids)
    items = []
    for job, category_name in rows:
        bucket = stats[job.id]
        reviewed_count = reviewed.get(job.id, 0)
        item = job_json(job)
        item.update(
            {
                "category_name": category_name,
                "counts": {
                    "支持": bucket["支持"],
                    "不支持": bucket["不支持"],
                    "无法确认": bucket["无法确认"],
                },
                "failed_count": bucket["failed"],
                "reviewed_count": reviewed_count,
                "pending_review_count": max(0, job.parameter_count - reviewed_count),
                "exportable": job.status in REVIEWABLE and bucket["rows"] > 0,
            }
        )
        items.append(item)
    return {"total": len(items), "limit": limit, "offset": offset, "items": items}


def task_detail(db: Session, job: EvaluationJob):
    bucket = _aggregates(db, [job.id])[job.id]
    extraction, review_required = _extraction_summary(db, job)
    summary = review_summary(db, job)
    detail = job_json(job)
    detail.update(
        {
            "category_id": job.category_id,
            "category_name": _category_name(db, job.category_id),
            "counts": {
                "支持": bucket["支持"],
                "不支持": bucket["不支持"],
                "无法确认": bucket["无法确认"],
            },
            "failed_count": bucket["failed"],
            "result_count": bucket["rows"],
            "review_summary": summary,
            "reviewed_count": summary["reviewed"],
            "pending_review_count": summary["pending"],
            "extraction": extraction,
            "warnings": _warnings(job, extraction, review_required, bucket["failed"]),
            "exportable": job.status in REVIEWABLE and bucket["rows"] > 0,
        }
    )
    return detail


def _requirement_map(job: EvaluationJob):
    result = {}
    for parameter in job.parameter_snapshot or []:
        for item in parameter.get("requirements") or []:
            result[item.get("requirement_id")] = item
    return result


def _atomic_payload(atom: dict, requirements: dict):
    requirement = requirements.get(atom.get("requirement_id")) or {}
    enriched = dict(atom)
    enriched.update(
        {
            "logical_relation": requirement.get("logical_relation") or "AND",
            "mandatory_status": requirement.get("mandatory_status")
            or ("required" if atom.get("necessary") else "optional"),
            "is_mandatory": requirement.get("is_mandatory", bool(atom.get("necessary"))),
            "threshold": requirement.get("threshold"),
            "threshold_operator": requirement.get("threshold_operator"),
            "unit": requirement.get("unit"),
            "conditions": requirement.get("conditions") or [],
            "conditions_text": "；".join(
                str(item) for item in (requirement.get("conditions") or []) if item
            ),
            "ambiguity": requirement.get("ambiguity"),
        }
    )
    return enriched


def _dedupe_evidence(atoms: list[dict], capabilities: dict, context_error: str | None):
    result = {}
    for atom in atoms:
        label = f"子要求 {atom.get('order_index')}（{atom.get('verdict')}）"
        for raw in atom.get("evidence") or []:
            enriched = enrich_evidence(raw, capabilities, context_error)
            key = enriched.get("evidence_id")
            if key not in result:
                enriched["used_for"] = [label]
                result[key] = enriched
            else:
                result[key]["used_for"].append(label)
    return list(result.values())


def _review_payload(row: EvaluationReview | None):
    if not row:
        return None
    payload = review_json(row)
    payload["source"] = "human"
    return payload


def results_payload(db: Session, job: EvaluationJob):
    capabilities, context_error = pinned_capabilities(db, job)
    rows = list(db.scalars(select(ParameterEvaluation).where(ParameterEvaluation.job_id == job.id)))
    order = {item["parameter_id"]: index for index, item in enumerate(job.parameter_snapshot or [])}
    rows.sort(key=lambda item: order.get(item.parameter_id, 999999))
    reviews = latest_reviews(db, job.id)
    requirements = _requirement_map(job)
    parameters = []
    evidence_ids = set()
    atomic_total = 0
    failed = 0
    counts = {"支持": 0, "不支持": 0, "无法确认": 0}
    for position, row in enumerate(rows, 1):
        payload = _parameter_payload(db, row)
        atoms = [
            _atomic_payload(atom, requirements) for atom in payload.get("atomic_requirements") or []
        ]
        evidence = _dedupe_evidence(atoms, capabilities, context_error)
        for atom in atoms:
            atom["evidence"] = [
                enrich_evidence(item, capabilities, context_error)
                for item in atom.get("evidence") or []
            ]
        atomic_total += len(atoms)
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
        if row.error_code:
            failed += 1
        evidence_ids.update(item.get("evidence_id") for item in evidence)
        review = reviews.get(row.parameter_id)
        review_payload = _review_payload(review)
        payload.update(
            {
                "order_index": position,
                "logic": row.logic_json or {},
                "logic_reason": (row.logic_json or {}).get("reason")
                or (row.details_json or {}).get("aggregate_reason")
                or "",
                "logic_tree": (row.logic_json or {}).get("tree"),
                "atomic_requirements": atoms,
                "evidence": evidence,
                "review": review_payload,
                "review_status": review.review_status if review else "pending",
                "review_status_label": REVIEW_STATUS_LABELS.get(
                    review.review_status if review else "pending", "待复核"
                ),
                "human_final_verdict": review.final_verdict if review else None,
                "human_final_verdict_label": (
                    review.final_verdict if review and review.final_verdict else "未填写"
                ),
                "human_comment": review.comment if review else "",
                "human_adjust_reason": review.adjust_reason if review else "",
                "recognized": review.recognized if review else None,
                "requires_reevaluation": (review.requires_reevaluation if review else False),
            }
        )
        parameters.append(payload)

    summary = review_summary(db, job)
    detail = task_detail(db, job)
    detail.update(
        {
            "counts": counts,
            "result_count": len(rows),
            "atomic_count": atomic_total,
            "failed_count": failed,
            "evidence_count": len(evidence_ids),
            "review_summary": summary,
            "reviewed_count": summary["reviewed"],
            "pending_review_count": summary["pending"],
            "publication_context_error": context_error,
            "evidence_context_available": not context_error,
            "evidence_context_note": (
                "证据展示上下文来自任务固定的知识发布版本。"
                if not context_error
                else "固定发布版本校验失败，证据来源不可用；系统不会替换为当前生效版本。"
            ),
        }
    )
    return {"job": detail, "parameters": parameters}
