"""双源冲突查看与人工裁决；不提供自动合并或自动发布。"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import admin
from app.core.security import AppError
from app.db.knowledge_models import CapabilityClaim
from app.db.models import AuditEvent, Snapshot, now
from app.db.review_models import ConflictFinding
from app.db.session import get_db
from app.knowledge.conflicts import scan
from app.knowledge.review import lock_category
from app.knowledge.review_schemas import ConflictResolutionRequest

router = APIRouter(prefix="/api/v1", tags=["双源冲突"])


@router.get("/snapshots/{snapshot_id}/conflicts")
def list_conflicts(
    snapshot_id: str,
    status: str | None = None,
    cursor: int = 0,
    limit: int = 50,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    if cursor < 0 or not 1 <= limit <= 100:
        raise AppError(422, "CONFLICT_LIMIT_INVALID", "冲突分页参数不合法")
    query = select(ConflictFinding).where(ConflictFinding.snapshot_id == snapshot_id)
    if status:
        query = query.where(ConflictFinding.status == status)
    rows = db.scalars(query.order_by(ConflictFinding.id).offset(cursor).limit(limit + 1)).all()
    return {
        "snapshot_id": snapshot_id,
        "truncated": len(rows) > limit,
        "next_cursor": cursor + limit if len(rows) > limit else None,
        "results": [
            {
                "id": r.id,
                "type": r.conflict_type,
                "status": r.status,
                "source_claim_id": r.source_claim_id,
                "target_claim_id": r.target_claim_id,
                "details": r.details_json,
                "resolution": r.resolution_json,
                "resolved_by": r.resolved_by,
                "resolved_at": r.resolved_at,
            }
            for r in rows[:limit]
        ],
    }


@router.post("/snapshots/{snapshot_id}/conflicts/scan")
def rescan(snapshot_id: str, request: Request, role=Depends(admin), db: Session = Depends(get_db)):
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    lock_category(db, snapshot.category_id)
    if not db.scalar(
        select(CapabilityClaim.id).where(CapabilityClaim.snapshot_id == snapshot_id).limit(1)
    ):
        raise AppError(422, "CONFLICT_SCAN_NO_CLAIMS", "快照内没有可比较的能力候选")
    created = scan(db, snapshot_id, snapshot.category_id)
    if created:
        db.add(
            AuditEvent(
                principal_id=role,
                action="conflicts.scan",
                resource_id=snapshot_id,
                correlation_id=request.state.correlation_id,
                details_json={"created": len(created)},
            )
        )
    db.commit()
    return {"snapshot_id": snapshot_id, "created": len(created)}


@router.post("/conflicts/{conflict_id}/resolve")
def resolve(
    conflict_id: str,
    body: ConflictResolutionRequest,
    request: Request,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    row = db.get(ConflictFinding, conflict_id)
    if not row:
        raise AppError(404, "CONFLICT_NOT_FOUND", "冲突不存在")
    lock_category(db, row.category_id)
    db.refresh(row)
    if row.status != "unresolved":
        raise AppError(409, "CONFLICT_ALREADY_RESOLVED", "冲突已有裁决")
    if not body.human_confirmed:
        raise AppError(422, "HUMAN_CONFIRMATION_REQUIRED", "必须由管理员显式确认冲突裁决")
    row.status = body.decision
    row.resolution_json = {"explanation": body.explanation, "resolved_by": role}
    row.resolved_by, row.resolved_at = role, now()
    db.add(
        AuditEvent(
            principal_id=role,
            action="conflicts.resolve",
            resource_id=row.id,
            correlation_id=request.state.correlation_id,
            details_json={
                "decision": body.decision,
                "snapshot_id": row.snapshot_id,
                "category_id": row.category_id,
            },
        )
    )
    db.commit()
    return {
        "id": row.id,
        "status": row.status,
        "resolution": row.resolution_json,
        "resolved_at": row.resolved_at,
    }
