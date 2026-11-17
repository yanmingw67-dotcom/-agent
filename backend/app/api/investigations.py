"""管理员暂存调查入口；不改变P1.2 build语义，不提供审核或发布。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.indexing import staging
from app.core.auth import admin, principal
from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Module
from app.db.knowledge_models import (
    CapabilityClaim,
    CapabilityRelation,
    Investigation,
    InvestigationTrace,
)
from app.db.models import AuditEvent, KnowledgeJob
from app.db.session import get_db
from app.knowledge.classification import approve_fixture, classification
from app.knowledge.evidence_registry import read_evidence
from app.knowledge.schemas import InvestigationRequest
from app.retrieval.tools import bind_context

router = APIRouter(prefix="/api/v1", tags=["知识构建候选"])


@router.post("/snapshots/{snapshot_id}/investigate", status_code=202)
def investigate(
    snapshot_id: str,
    body: InvestigationRequest,
    request: Request,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    s = get_settings()
    snapshot = staging(db, snapshot_id)
    if snapshot.index_status != "indexed":
        raise AppError(409, "SOURCE_INDEX_NOT_READY", "必须先完成源码索引")
    query = select(Module).where(Module.snapshot_id == snapshot_id)
    if body.module_ids:
        query = query.where(Module.id.in_(body.module_ids))
    modules = db.scalars(query.order_by(Module.id).limit(s.builder_max_modules + 1)).all()
    if body.module_ids and {m.id for m in modules} != set(body.module_ids):
        raise AppError(404, "MODULE_NOT_FOUND", "指定模块不属于该快照")
    if not modules or len(modules) > s.builder_max_modules:
        raise AppError(422, "MODULE_SELECTION_REQUIRED", "模块为空或超出单任务预算，请分批指定模块")
    trusted_classification = classification(db, snapshot_id)
    try:
        job = KnowledgeJob(
            snapshot_id=snapshot_id,
            category_id=snapshot.category_id,
            job_type="knowledge_build",
            stage="module_planning",
            metrics_json={
                "data_classification": trusted_classification,
                "requested_classification": body.data_classification,
                "model_mode": "NOT_STARTED",
                "investigation_mode": body.investigation_mode,
                "capability_limit": body.capability_limit,
                "include_equivalence": body.include_equivalence,
                "exploration_max_fragments": body.exploration_max_fragments,
            },
        )
        db.add(job)
        db.flush()
        for module in modules:
            db.add(Investigation(job_id=job.id, snapshot_id=snapshot_id, module_id=module.id))
        db.add(
            AuditEvent(
                principal_id=role,
                action="knowledge.investigate",
                resource_id=snapshot_id,
                correlation_id=request.state.correlation_id,
                details_json={
                    "job_id": job.id,
                    "modules": len(modules),
                    "data_classification": trusted_classification,
                    "requested_classification": body.data_classification,
                },
            )
        )
        db.commit()
        return {"snapshot_id": snapshot_id, "job_id": job.id, "module_count": len(modules)}
    except IntegrityError as exc:
        db.rollback()
        raise AppError(409, "JOB_CONFLICT", "同品类已有在途任务") from exc


@router.post("/snapshots/{snapshot_id}/approve-public-fixture")
def approve_public_fixture(
    snapshot_id: str, request: Request, role=Depends(admin), db: Session = Depends(get_db)
):
    snapshot = staging(db, snapshot_id)
    previous = snapshot.data_classification
    approve_fixture(db, snapshot)
    db.add(
        AuditEvent(
            principal_id=role,
            action="source.approve_public_fixture",
            resource_id=snapshot_id,
            correlation_id=request.state.correlation_id,
            details_json={
                "classification": "synthetic_public",
                "previous_classification": previous,
                "source_sha256": snapshot.source_sha256,
                "policy": "exact_bundled_fixture_v1",
            },
        )
    )
    db.commit()
    return {"snapshot_id": snapshot_id, "data_classification": "synthetic_public"}


@router.get("/jobs/{job_id}/investigations")
def investigations(job_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    job = db.get(KnowledgeJob, job_id)
    if not job:
        raise AppError(404, "JOB_NOT_FOUND", "任务不存在")
    return [
        {
            "id": r.id,
            "module_id": r.module_id,
            "status": r.status,
            "attempt": r.attempt,
            "error_code": r.error_code,
            "metrics": r.metrics_json,
            "result": r.output_json,
        }
        for r in db.scalars(
            select(Investigation)
            .where(Investigation.job_id == job_id)
            .order_by(Investigation.module_id)
        )
    ]


@router.get("/jobs/{job_id}/investigation-traces")
def traces(
    job_id: str,
    cursor: int = 0,
    limit: int = 100,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    if cursor < 0 or not 1 <= limit <= 100:
        raise AppError(422, "TRACE_LIMIT_INVALID", "轨迹分页参数不合法")
    rows = db.scalars(
        select(InvestigationTrace)
        .join(Investigation, Investigation.id == InvestigationTrace.investigation_id)
        .where(Investigation.job_id == job_id)
        .order_by(InvestigationTrace.created_at, InvestigationTrace.id)
        .offset(cursor)
        .limit(limit + 1)
    ).all()
    return {
        "truncated": len(rows) > limit,
        "next_cursor": cursor + limit if len(rows) > limit else None,
        "results": [
            {
                "investigation_id": r.investigation_id,
                "attempt": r.attempt,
                "sequence": r.sequence,
                "kind": r.kind,
                "details": r.details_json,
            }
            for r in rows[:limit]
        ],
    }


@router.get("/snapshots/{snapshot_id}/candidates")
def candidates(
    snapshot_id: str,
    category_id: str,
    cursor: int = 0,
    limit: int = 30,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    bind_context(db, category_id, role, snapshot_id)
    if cursor < 0 or not 1 <= limit <= 100:
        raise AppError(422, "CANDIDATE_LIMIT_INVALID", "候选分页参数不合法")
    rows = db.scalars(
        select(CapabilityClaim)
        .where(CapabilityClaim.snapshot_id == snapshot_id)
        .order_by(CapabilityClaim.id)
        .offset(cursor)
        .limit(limit + 1)
    ).all()
    # 关系按调查单元分页，不静默将关系标成已证明。
    run_ids = {r.investigation_id for r in rows[:limit]}
    relations = db.scalars(
        select(CapabilityRelation)
        .where(
            CapabilityRelation.investigation_id.in_(run_ids),
            CapabilityRelation.snapshot_id == snapshot_id,
        )
        .order_by(CapabilityRelation.id)
        .limit(301)
    ).all()
    return {
        "snapshot_id": snapshot_id,
        "truncated": len(rows) > limit,
        "next_cursor": cursor + limit if len(rows) > limit else None,
        "semantic_verification": "unverified",
        "formal_compliance_decision": None,
        "results": [{"id": r.id, "status": r.status, "draft": r.draft_json} for r in rows[:limit]],
        "relations_truncated": len(relations) > 300,
        "relations": [
            {
                "id": r.id,
                "source_claim_id": r.source_claim_id,
                "target_claim_id": r.target_claim_id,
                "relation_type": r.relation_type,
                "status": r.status,
                "details": r.details_json,
            }
            for r in relations[:300]
        ],
    }


@router.get("/evidence/{evidence_id}")
def evidence(
    evidence_id: str,
    category_id: str,
    snapshot_id: str | None = None,
    role=Depends(principal),
    db: Session = Depends(get_db),
):
    context = bind_context(db, category_id, role, snapshot_id)
    if snapshot_id is None:
        from app.knowledge.publisher import current

        publication = current(db, category_id)
        ids = set()
        for capability in publication.payload_json["capabilities"]:
            ids.update(capability["reachability_evidence_ids"])
            for assertion in capability["assertions"]:
                ids.update(assertion["evidence_ids"])
                ids.update(assertion.get("implementation_evidence_ids", []))
                for dependency in assertion.get("dependencies", []):
                    ids.update(dependency["evidence_ids"])
        for relation in publication.payload_json["relations"]:
            ids.update(relation["details"]["evidence_ids"])
        if evidence_id not in ids:
            raise AppError(404, "EVIDENCE_NOT_PUBLISHED", "证据不在当前审核发布清单")
    return read_evidence(db, context, evidence_id)
