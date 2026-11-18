"""只有显式管理员审核可形成可发布修订；普通用户仅见当前冻结清单。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import admin, principal
from app.core.security import AppError
from app.db.knowledge_models import CapabilityClaim
from app.db.review_models import PublicationSwitch, ReviewEvent
from app.db.session import get_db
from app.knowledge import review as service
from app.knowledge.coverage import coverage, coverage_summary
from app.knowledge.evidence_registry import register
from app.knowledge.publisher import current, publish, rollback
from app.knowledge.quality import classify, context, gate, latest
from app.knowledge.review_schemas import (
    MergeRequest,
    PublishRequest,
    RelationRequest,
    ReviewRequest,
    RollbackRequest,
)
from app.knowledge.schemas import EvidenceRequest

router = APIRouter(prefix="/api/v1", tags=["能力审核与发布"])


def revision_view(row):
    return {
        "id": row.id,
        "claim_id": row.claim_id,
        "number": row.number,
        "declaration_type": row.declaration_type,
        "decision": row.decision,
        "content": row.content_json,
        "assertions": row.assertions_json,
        "gate": row.gate_json,
        "merged_into": row.merged_into,
    }


@router.get("/admin/capabilities/{claim_id}")
def draft(claim_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    claim = db.get(CapabilityClaim, claim_id)
    if not claim:
        raise AppError(404, "CLAIM_NOT_FOUND", "候选不存在")
    row = latest(db, claim.id)
    return {
        "id": claim.id,
        "snapshot_id": claim.snapshot_id,
        "original_model_output": claim.draft_json,
        "machine_declaration_type": classify(claim.draft_json),
        "latest_revision": revision_view(row) if row else None,
        "review_events": [
            {"id": e.id, "action": e.action, "comment": e.comment, "details": e.details_json}
            for e in db.scalars(
                select(ReviewEvent)
                .where(ReviewEvent.claim_id == claim.id)
                .order_by(ReviewEvent.created_at, ReviewEvent.id)
            )
        ],
    }


@router.post("/capabilities/{claim_id}/review")
def review(claim_id: str, body: ReviewRequest, role=Depends(admin), db: Session = Depends(get_db)):
    row = service.review(db, claim_id, body)
    db.commit()
    return revision_view(row)


@router.post("/capabilities/{claim_id}/validate")
def validate(
    claim_id: str, body: ReviewRequest, role=Depends(admin), db: Session = Depends(get_db)
):
    claim = db.get(CapabilityClaim, claim_id)
    if not claim or not body.content:
        raise AppError(422, "REVIEW_CONTENT_REQUIRED", "需真实候选和完整描述")
    return gate(
        db,
        claim.snapshot_id,
        body.declaration_type,
        body.content.model_dump(),
        [a.model_dump() for a in body.assertions],
        body.reachability_evidence_ids,
        availability_basis=body.availability_basis,
        product_scope_confirmed=body.product_scope_confirmed,
    )


@router.post("/snapshots/{snapshot_id}/review-evidence")
def review_evidence(
    snapshot_id: str, body: EvidenceRequest, role=Depends(admin), db: Session = Depends(get_db)
):
    service.lock_snapshot(db, snapshot_id)
    receipt = register(db, context(db, snapshot_id), body)
    service.event(
        db,
        snapshot_id,
        None,
        "read_review_evidence",
        "管理员调查实际实现并登记精确来源",
        {"evidence_id": receipt.evidence_id},
    )
    db.commit()
    return receipt


@router.get("/snapshots/{snapshot_id}/coverage")
def get_coverage(snapshot_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    context(db, snapshot_id)
    return coverage(db, snapshot_id)


@router.get("/snapshots/{snapshot_id}/duplicate-candidates")
def duplicate_candidates(snapshot_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    context(db, snapshot_id)
    claims = list(
        db.scalars(
            select(CapabilityClaim)
            .where(CapabilityClaim.snapshot_id == snapshot_id)
            .order_by(CapabilityClaim.id)
            .limit(201)
        )
    )
    groups = {}
    for c in claims[:200]:
        r = latest(db, c.id)
        data = r.content_json if r else c.draft_json
        key = (data.get("action", "").strip().casefold(), data.get("target", "").strip().casefold())
        groups.setdefault(key, []).append(c.id)
    return {
        "groups": [v for v in groups.values() if len(v) > 1],
        "truncated": len(claims) > 200,
        "decision": "suggestions_only",
        "automatic_merge": False,
    }


@router.post("/capabilities/merge")
def merge(body: MergeRequest, role=Depends(admin), db: Session = Depends(get_db)):
    row = service.merge(db, body)
    db.commit()
    return revision_view(row)


@router.post("/capability-relations/review")
def relation(body: RelationRequest, role=Depends(admin), db: Session = Depends(get_db)):
    row = service.relation(db, body)
    db.commit()
    return {
        "id": row.id,
        "decision": row.decision,
        "type": row.relation_type,
        "details": row.details_json,
    }


@router.post("/snapshots/{snapshot_id}/publish")
def publish_snapshot(
    snapshot_id: str, body: PublishRequest, role=Depends(admin), db: Session = Depends(get_db)
):
    row = publish(db, snapshot_id, body)
    db.commit()
    return {
        "publication_id": row.id,
        "snapshot_id": row.snapshot_id,
        "payload_hash": row.payload_hash,
    }


@router.post("/categories/{category_id}/rollback")
def rollback_publication(
    category_id: str, body: RollbackRequest, role=Depends(admin), db: Session = Depends(get_db)
):
    row = rollback(db, category_id, body)
    db.commit()
    return {"publication_id": row.id, "snapshot_id": row.snapshot_id}


@router.get("/categories/{category_id}/publications")
def publications(category_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    return [
        {
            "publication_id": e.publication_id,
            "previous_publication_id": e.previous_publication_id,
            "action": e.action,
            "actor": e.actor,
            "comment": e.comment,
        }
        for e in db.scalars(
            select(PublicationSwitch)
            .where(PublicationSwitch.category_id == category_id)
            .order_by(PublicationSwitch.created_at, PublicationSwitch.id)
        )
    ]


@router.get("/capabilities")
def capabilities(
    category_id: str | None = None, role=Depends(principal), db: Session = Depends(get_db)
):
    if not category_id:
        raise AppError(404, "CATEGORY_REQUIRED", "请选择产品品类")
    row = current(db, category_id)
    published_coverage = row.payload_json["coverage"]
    return {
        "snapshot_id": row.snapshot_id,
        "capabilities": row.payload_json["capabilities"],
        "relations": row.payload_json["relations"],
        "coverage": published_coverage if role == "admin" else coverage_summary(published_coverage),
        "formal_compliance_decision": None,
    }


@router.get("/capabilities/{claim_id}")
def capability(
    claim_id: str, category_id: str, role=Depends(principal), db: Session = Depends(get_db)
):
    row = current(db, category_id)
    item = next((c for c in row.payload_json["capabilities"] if c["id"] == claim_id), None)
    if not item:
        raise AppError(404, "CAPABILITY_NOT_PUBLISHED", "当前生效清单不含该能力")
    return {**item, "snapshot_id": row.snapshot_id}
