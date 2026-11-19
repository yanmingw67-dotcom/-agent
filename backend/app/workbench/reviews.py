"""P2.3 人工复核：只追加审计记录，绝不改自动结论或证据。

系统自动结论始终来自 ParameterEvaluation.verdict。人工复核可以记录是否认可、
人工应答结论与理由，但导出与页面都必须把两者分开呈现。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import AppError
from app.db.evaluation_models import EvaluationJob, ParameterEvaluation
from app.db.models import AuditEvent
from app.db.workbench_models import EvaluationReview
from app.workbench.schemas import ReviewCreate

STATUS_LABELS = {
    "accepted": "已复核",
    "adjusted": "已复核（人工调整）",
    "rejected": "已复核（不认可自动结论）",
    "reassessment_required": "需重新评估",
}
ACTION_LABELS = {
    "accept": "认可自动结论",
    "adjust": "人工调整最终应答",
    "reject": "不认可自动结论",
    "reassess": "需要重新评估",
}
REVIEWABLE = {"succeeded", "partial"}
EDITABLE_VERDICTS = {"支持", "不支持", "无法确认"}


def owned_job(db: Session, job_id: str, principal_id: str, lock: bool = False):
    statement = select(EvaluationJob).where(
        EvaluationJob.id == job_id, EvaluationJob.principal_id == principal_id
    )
    if lock:
        statement = statement.with_for_update()
    job = db.scalar(statement)
    if not job:
        raise AppError(404, "EVALUATION_JOB_NOT_FOUND", "评估任务不存在")
    return job


def parameter_evaluation(db: Session, job_id: str, parameter_id: str):
    row = db.scalar(
        select(ParameterEvaluation).where(
            ParameterEvaluation.job_id == job_id,
            ParameterEvaluation.parameter_id == parameter_id,
        )
    )
    if not row:
        raise AppError(404, "EVALUATION_PARAMETER_NOT_FOUND", "评估结果中没有该参数")
    return row


def _clean(value: str | None, *, label: str, required: bool = False, limit: int = 2000) -> str:
    text = (value or "").strip()
    if required and not text:
        raise AppError(422, "REVIEW_NOTE_REQUIRED", f"{label}不能为空")
    if "\x00" in text:
        raise AppError(422, "REVIEW_TEXT_INVALID", f"{label}包含非法字符")
    return text[:limit]


def review_json(row: EvaluationReview):
    return {
        "review_id": row.id,
        "job_id": row.job_id,
        "parameter_id": row.parameter_id,
        "reviewer_id": row.reviewer_id,
        "action": row.action,
        "action_label": ACTION_LABELS.get(row.action, row.action),
        "review_status": row.review_status,
        "review_status_label": STATUS_LABELS.get(row.review_status, row.review_status),
        "auto_verdict": row.auto_verdict,
        "final_verdict": row.final_verdict,
        "recognized": row.recognized,
        "requires_reevaluation": row.requires_reevaluation,
        "comment": row.comment,
        "adjust_reason": row.adjust_reason,
        "created_at": row.created_at,
    }


def latest_reviews(db: Session, job_id: str, parameter_ids: list[str] | None = None):
    """返回每个参数最新一条复核记录；历史记录仍可通过 history 查询。"""
    query = select(EvaluationReview).where(EvaluationReview.job_id == job_id)
    if parameter_ids:
        query = query.where(EvaluationReview.parameter_id.in_(parameter_ids))
    rows = db.scalars(
        query.order_by(
            EvaluationReview.created_at.asc(),
            EvaluationReview.id.asc(),
        )
    ).all()
    result = {}
    for row in rows:
        result[row.parameter_id] = row
    return result


def review_summary(db: Session, job: EvaluationJob):
    rows = latest_reviews(db, job.id)
    counts = {
        "accepted": 0,
        "adjusted": 0,
        "rejected": 0,
        "reassessment_required": 0,
    }
    human_final = {"支持": 0, "不支持": 0, "无法确认": 0}
    requires = 0
    for row in rows.values():
        counts[row.review_status] = counts.get(row.review_status, 0) + 1
        verdict = row.final_verdict or row.auto_verdict
        if verdict in human_final:
            human_final[verdict] += 1
        if row.requires_reevaluation:
            requires += 1
    reviewed = len(rows)
    return {
        "parameter_count": job.parameter_count,
        "reviewed": reviewed,
        "pending": max(0, job.parameter_count - reviewed),
        "accepted": counts["accepted"],
        "adjusted": counts["adjusted"],
        "rejected": counts["rejected"],
        "reassessment_required": counts["reassessment_required"],
        "requires_reevaluation": requires,
        "human_final_counts": human_final,
    }


def record_review(
    db: Session,
    *,
    job: EvaluationJob,
    parameter_id: str,
    principal_id: str,
    payload: ReviewCreate,
    correlation_id: str | None = None,
):
    if job.status not in REVIEWABLE:
        raise AppError(
            409,
            "REVIEW_JOB_NOT_READY",
            "只有评估成功或部分成功的任务才能人工复核",
        )
    parameter = parameter_evaluation(db, job.id, parameter_id)
    auto = parameter.verdict
    comment = _clean(payload.comment, label="复核意见")
    reason = _clean(payload.adjust_reason, label="人工调整理由")
    action = payload.action
    final = payload.final_verdict
    requires = bool(payload.requires_reevaluation)

    if action == "accept":
        if final is not None and final != auto:
            raise AppError(
                422,
                "REVIEW_VERDICT_INVALID",
                "认可自动结论时不能另外填写不同的人工结论",
            )
        if requires:
            raise AppError(
                422,
                "REVIEW_REEVALUATION_CONFLICT",
                "认可自动结论时不能同时要求重新评估",
            )
        status = "accepted"
        recognized = True
        final = auto
    elif action in {"adjust", "reject"}:
        if final not in EDITABLE_VERDICTS:
            raise AppError(422, "REVIEW_VERDICT_REQUIRED", "人工调整必须选择最终应答结论")
        if final == auto:
            raise AppError(
                422,
                "REVIEW_VERDICT_UNCHANGED",
                "人工调整结论必须与系统自动结论不同；认可自动结论请选择认可",
            )
        if not reason:
            raise AppError(422, "REVIEW_REASON_REQUIRED", "人工调整必须填写调整理由")
        status = "adjusted" if action == "adjust" else "rejected"
        recognized = False
    else:
        if final is not None:
            raise AppError(422, "REVIEW_VERDICT_INVALID", "需要重新评估时不能提交最终应答结论")
        if not comment and not reason:
            raise AppError(422, "REVIEW_NOTE_REQUIRED", "需要重新评估时必须填写复核意见")
        status = "reassessment_required"
        recognized = False
        final = None
        requires = True

    row = EvaluationReview(
        job_id=job.id,
        parameter_evaluation_id=parameter.id,
        parameter_id=parameter.parameter_id,
        reviewer_id=(principal_id or "unknown")[:64],
        action=action,
        review_status=status,
        auto_verdict=auto,
        final_verdict=final,
        recognized=recognized,
        requires_reevaluation=requires,
        comment=comment,
        adjust_reason=reason,
        correlation_id=(correlation_id or "")[:36] or None,
    )
    db.add(row)
    db.flush()
    db.add(
        AuditEvent(
            principal_id=(principal_id or "unknown")[:32],
            action="evaluation.review",
            resource_id=job.id,
            correlation_id=(correlation_id or row.id)[:36],
            details_json={
                "review_id": row.id,
                "parameter_id": parameter.parameter_id,
                "action": action,
                "review_status": status,
                "auto_verdict": auto,
                "final_verdict": final,
                "requires_reevaluation": requires,
            },
        )
    )
    db.commit()
    return row


def history(db: Session, job: EvaluationJob, parameter_id: str):
    parameter_evaluation(db, job.id, parameter_id)
    rows = db.scalars(
        select(EvaluationReview)
        .where(
            EvaluationReview.job_id == job.id,
            EvaluationReview.parameter_id == parameter_id,
        )
        .order_by(EvaluationReview.created_at.desc(), EvaluationReview.id.desc())
    ).all()
    return [review_json(row) for row in rows]
