"""冻结审核清单；指针、快照状态、发布审计在单事务内切换。"""

from sqlalchemy import select

from app.core.security import AppError
from app.db.models import Category
from app.db.review_models import ClaimRevision, Publication, PublicationSwitch, ReviewedRelation
from app.ingestion.text import digest
from app.knowledge.coverage import coverage
from app.knowledge.evidence_registry import canonical, read_evidence
from app.knowledge.quality import context, gate, latest
from app.knowledge.review import lock_snapshot


def validate_revision(db, revision):
    if revision.decision != "accept" or latest(db, revision.claim_id).id != revision.id:
        raise AppError(422, "PUBLICATION_REVIEW_REQUIRED", "只能发布最新人工接受修订")
    result = gate(
        db,
        revision.snapshot_id,
        revision.declaration_type,
        revision.content_json,
        revision.assertions_json,
        revision.gate_json.get("reachability_evidence_ids", []),
    )
    if result["errors"]:
        raise AppError(422, "PUBLICATION_ASSERTION_GATE_FAILED", "发布时断言证据核验未闭合")


def publish(db, sid, body):
    snapshot = lock_snapshot(db, sid)
    category = db.get(Category, snapshot.category_id)
    if category.active_publication_id != body.expected_active_publication_id:
        raise AppError(409, "PUBLICATION_POINTER_CONFLICT", "当前知识已改变，请重新核对")
    rows = [db.get(ClaimRevision, rid) for rid in body.revision_ids]
    if any(not r or r.snapshot_id != sid for r in rows) or len({r.claim_id for r in rows}) != len(
        rows
    ):
        raise AppError(422, "PUBLICATION_SCOPE_INVALID", "发布修订重复或跨快照")
    if len(set(body.revision_ids)) != len(body.revision_ids):
        raise AppError(422, "PUBLICATION_DUPLICATE", "发布修订重复")
    capabilities = []
    for r in rows:
        validate_revision(db, r)
        capabilities.append(
            {
                "id": r.claim_id,
                "revision_id": r.id,
                "declaration_type": r.declaration_type,
                "content": r.content_json,
                "assertions": r.assertions_json,
                "reachability_evidence_ids": r.gate_json.get("reachability_evidence_ids", []),
                "review_status": "human_accepted",
                "reviewer_id": r.reviewer_id,
                "reviewed_at": r.created_at.isoformat(),
                "not_automatic_verification": True,
            }
        )
    relations = []
    for relation_id in body.relation_ids:
        relation = db.get(ReviewedRelation, relation_id)
        if (
            not relation
            or relation.snapshot_id != sid
            or relation.decision != "accept"
            or not {relation.source_revision_id, relation.target_revision_id}.issubset(
                body.revision_ids
            )
        ):
            raise AppError(
                422, "PUBLICATION_RELATION_INVALID", "关系必须已人工审核且双方均在本次清单"
            )
        latest_relation = db.scalar(
            select(ReviewedRelation)
            .where(
                ReviewedRelation.source_revision_id == relation.source_revision_id,
                ReviewedRelation.target_revision_id == relation.target_revision_id,
                ReviewedRelation.relation_type == relation.relation_type,
            )
            .order_by(ReviewedRelation.created_at.desc(), ReviewedRelation.id.desc())
            .limit(1)
        )
        if latest_relation.id != relation.id:
            raise AppError(409, "PUBLICATION_RELATION_STALE", "关系已有后续审核，不可发布旧决策")
        for eid in relation.details_json["evidence_ids"]:
            read_evidence(db, context(db, sid), eid)
        relations.append(
            {
                "id": relation.id,
                "type": relation.relation_type,
                "source_revision_id": relation.source_revision_id,
                "target_revision_id": relation.target_revision_id,
                "details": relation.details_json,
            }
        )
    gaps = coverage(db, sid)
    if not body.acknowledge_coverage_gaps:
        raise AppError(
            422, "COVERAGE_ACKNOWLEDGEMENT_REQUIRED", "须显式确认并保留未调查/未验证缺口"
        )
    payload = {
        "snapshot_id": sid,
        "capabilities": capabilities,
        "relations": relations,
        "coverage": gaps,
        "formal_compliance_decision": None,
    }
    row = Publication(
        category_id=category.id,
        snapshot_id=sid,
        payload_json=payload,
        payload_hash=digest(canonical(payload).encode()),
        published_by="admin",
    )
    db.add(row)
    db.flush()
    previous = category.active_publication_id
    snapshot.status = "published"
    category.active_snapshot_id = sid
    category.active_publication_id = row.id
    db.add(
        PublicationSwitch(
            category_id=category.id,
            publication_id=row.id,
            previous_publication_id=previous,
            action="publish",
            actor="admin",
            comment=body.comment,
        )
    )
    db.flush()
    return row


def rollback(db, category_id, body):
    category = db.scalar(select(Category).where(Category.id == category_id).with_for_update())
    if not category:
        raise AppError(404, "CATEGORY_NOT_FOUND", "品类不存在")
    if category.active_publication_id != body.expected_active_publication_id:
        raise AppError(409, "PUBLICATION_POINTER_CONFLICT", "当前知识已改变")
    row = db.get(Publication, body.publication_id)
    if not row or row.category_id != category.id:
        raise AppError(404, "PUBLICATION_NOT_FOUND", "本品类没有该发布记录")
    lock_snapshot(db, row.snapshot_id)
    if row.payload_hash != digest(canonical(row.payload_json).encode()):
        raise AppError(409, "PUBLICATION_TAMPERED", "不可变清单指纹不一致")
    for c in row.payload_json["capabilities"]:
        for a in c["assertions"]:
            ids = set(a["evidence_ids"]) | set(a.get("implementation_evidence_ids", []))
            for d in a.get("dependencies", []):
                ids.update(d["evidence_ids"])
            for eid in ids:
                read_evidence(db, context(db, row.snapshot_id), eid)
        for eid in c["reachability_evidence_ids"]:
            read_evidence(db, context(db, row.snapshot_id), eid)
    for relation in row.payload_json["relations"]:
        for eid in relation["details"]["evidence_ids"]:
            read_evidence(db, context(db, row.snapshot_id), eid)
    previous = category.active_publication_id
    category.active_publication_id, category.active_snapshot_id = row.id, row.snapshot_id
    db.add(
        PublicationSwitch(
            category_id=category.id,
            publication_id=row.id,
            previous_publication_id=previous,
            action="rollback",
            actor="admin",
            comment=body.comment,
        )
    )
    db.flush()
    return row


def current(db, category_id):
    category = db.get(Category, category_id)
    if not category:
        raise AppError(404, "CATEGORY_NOT_FOUND", "品类不存在")
    row = (
        db.get(Publication, category.active_publication_id)
        if category.active_publication_id
        else None
    )
    if not row:
        raise AppError(409, "KNOWLEDGE_NOT_PUBLISHED", "没有通过P1.4审核的当前生效知识库")
    if row.category_id != category.id or row.snapshot_id != category.active_snapshot_id:
        raise AppError(409, "PUBLICATION_POINTER_INVALID", "生效指针不一致")
    if digest(canonical(row.payload_json).encode()) != row.payload_hash:
        raise AppError(409, "PUBLICATION_TAMPERED", "发布清单已改变")
    return row
