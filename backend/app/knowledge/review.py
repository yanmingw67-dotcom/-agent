"""人工决策与不可变修订；任何模型只能创建原始候选。"""

from sqlalchemy import select

from app.core.security import AppError
from app.db.index_models import CodeEdge, CodeSymbol
from app.db.knowledge_models import CapabilityClaim
from app.db.models import Category, KnowledgeJob, Snapshot
from app.db.review_models import ClaimRevision, ConflictFinding, ReviewedRelation, ReviewEvent
from app.ingestion.text import sensitive_text
from app.knowledge.evidence_registry import read_evidence
from app.knowledge.quality import context, gate, latest
from app.knowledge.review_schemas import ReviewRequest


def lock_category(db, category_id):
    # FOR UPDATE 本身不刷新 identity map；也不应先 autoflush 陈旧缓存。
    with db.no_autoflush:
        category = db.scalar(
            select(Category)
            .where(Category.id == category_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if not category:
        raise AppError(404, "CATEGORY_NOT_FOUND", "品类不存在")
    return category


def lock_snapshot(db, sid):
    with db.no_autoflush:
        category_id = db.scalar(select(Snapshot.category_id).where(Snapshot.id == sid))
    if category_id is None:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    lock_category(db, category_id)
    with db.no_autoflush:
        snapshot = db.scalar(
            select(Snapshot)
            .where(Snapshot.id == sid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if not snapshot:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    if snapshot.status not in {"extracted", "published"} or snapshot.index_status != "indexed":
        raise AppError(409, "REVIEW_SOURCE_NOT_READY", "源码尚未就绪")
    if db.scalar(
        select(KnowledgeJob.id)
        .where(
            KnowledgeJob.category_id == snapshot.category_id,
            KnowledgeJob.status.in_(["running", "queued"]),
        )
        .limit(1)
    ):
        raise AppError(409, "REVIEW_JOB_CONFLICT", "存在在途任务")
    return snapshot


def event(db, sid, claim_id, action, comment, details):
    db.add(
        ReviewEvent(
            snapshot_id=sid,
            claim_id=claim_id,
            reviewer_id="admin",
            action=action,
            comment=comment,
            details_json=details,
        )
    )


def review(db, claim_id, body: ReviewRequest):
    claim = db.get(CapabilityClaim, claim_id)
    if not claim:
        raise AppError(404, "CLAIM_NOT_FOUND", "候选不存在")
    lock_snapshot(db, claim.snapshot_id)
    prior = latest(db, claim_id)
    number = prior.number if prior else 0
    if number != body.expected_revision:
        raise AppError(409, "REVIEW_REVISION_CONFLICT", "审核修订已变化，请重新读取")
    if body.action in {"modify", "accept"} and not body.content:
        raise AppError(422, "REVIEW_CONTENT_REQUIRED", "必须显式提交核对后的完整描述")
    content = (
        body.content.model_dump()
        if body.content
        else (
            prior.content_json
            if prior
            else {
                k: claim.draft_json.get(k, [])
                if k in {"supported_scope", "conditions", "limitations"}
                else claim.draft_json.get(k, "")
                for k in [
                    "name",
                    "action",
                    "target",
                    "business_goal",
                    "implementation_mechanism",
                    "supported_scope",
                    "conditions",
                    "limitations",
                ]
            }
        )
    )
    if sensitive_text(str(content)):
        raise AppError(422, "SENSITIVE_OUTPUT_RESTRICTED", "修订含疑似敏感内容")
    assertions = [a.model_dump() for a in body.assertions]
    checks = (
        gate(
            db,
            claim.snapshot_id,
            body.declaration_type,
            content,
            assertions,
            body.reachability_evidence_ids,
            availability_basis=body.availability_basis,
            product_scope_confirmed=body.product_scope_confirmed,
        )
        if body.content
        else {"errors": ["NOT_REVIEWED"]}
    )
    conflicts = list(
        db.scalars(
            select(ConflictFinding).where(
                ConflictFinding.snapshot_id == claim.snapshot_id,
                (ConflictFinding.source_claim_id == claim.id)
                | (ConflictFinding.target_claim_id == claim.id),
            )
        )
    )
    if body.action == "accept":
        if not body.human_confirmed:
            raise AppError(422, "HUMAN_CONFIRMATION_REQUIRED", "必须由管理员显式确认人工语义审核")
        if any(item.status == "unresolved" for item in conflicts):
            raise AppError(422, "UNRESOLVED_CONFLICT", "存在未解决的双源冲突，不能接受该能力")
        if checks["errors"]:
            raise AppError(422, "REVIEW_GATE_FAILED", "断言、类型或依赖证据门槛未闭合")
    proof_kind = None
    if body.content:
        has_document = any(a.get("proof_basis") in {"document", "mixed"} for a in assertions)
        has_source = any(a.get("proof_basis") in {"source", "mixed"} for a in assertions)
        proof_kind = (
            "mixed" if has_document and has_source else ("document" if has_document else "source")
        )
    row = ClaimRevision(
        claim_id=claim_id,
        snapshot_id=claim.snapshot_id,
        number=number + 1,
        declaration_type=body.declaration_type,
        decision=body.action,
        content_json=content,
        assertions_json=assertions,
        gate_json=checks,
        availability_basis=body.availability_basis,
        product_scope_confirmed=body.product_scope_confirmed,
        proof_kind=proof_kind,
        conflict_ids=[item.id for item in conflicts],
        reviewer_id="admin",
    )
    db.add(row)
    db.flush()
    event(
        db,
        claim.snapshot_id,
        claim.id,
        body.action,
        body.comment,
        {"revision_id": row.id, "number": row.number, "gate": checks},
    )
    return row


def relation(db, body):
    a, b = (
        db.get(ClaimRevision, body.source_revision_id),
        db.get(ClaimRevision, body.target_revision_id),
    )
    if not a or not b or a.snapshot_id != b.snapshot_id or a.claim_id == b.claim_id:
        raise AppError(422, "RELATION_SCOPE_INVALID", "双方必须为同快照不同候选")
    lock_snapshot(db, a.snapshot_id)
    if latest(db, a.claim_id).id != a.id or latest(db, b.claim_id).id != b.id:
        raise AppError(409, "RELATION_REVISION_STALE", "必须引用最新修订")
    evidence = [read_evidence(db, context(db, a.snapshot_id), e) for e in body.evidence_ids]
    if body.decision == "accept":
        if not body.human_confirmed or a.decision != "accept" or b.decision != "accept":
            raise AppError(422, "RELATION_REVIEW_REQUIRED", "关系和双方能力须人工确认")
        if len({e["source_ref"].get("file_id") for e in evidence if e["kind"] == "source"}) < 2:
            raise AppError(422, "RELATION_TWO_SIDED_EVIDENCE_REQUIRED", "需两侧实现源码证据")
        supported_a = {e for item in a.assertions_json for e in item["evidence_ids"]}
        supported_b = {e for item in b.assertions_json for e in item["evidence_ids"]}
        if not supported_a.intersection(body.evidence_ids) or not supported_b.intersection(
            body.evidence_ids
        ):
            raise AppError(422, "RELATION_EVIDENCE_MISMATCH", "关系证据须覆盖双方断言")
    if body.decision == "accept":
        left = {k: v for k, v in a.content_json.items() if k != "name"}
        right = {k: v for k, v in b.content_json.items() if k != "name"}
        if body.relation_type == "same_as" and (
            left != right or a.declaration_type != b.declaration_type
        ):
            raise AppError(422, "RELATION_BOUNDARY_MISMATCH", "same_as要求所有业务边界一致")
        if body.relation_type == "implements" and not (
            a.declaration_type == "technical_mechanism" and b.declaration_type == "product_function"
        ):
            raise AppError(422, "RELATION_DIRECTION_INVALID", "implements应由技术机制指向产品功能")
        if body.relation_type == "subset_of" and not (
            a.content_json["action"] == b.content_json["action"]
            and a.content_json["target"] == b.content_json["target"]
            and set(a.content_json["supported_scope"]).issubset(b.content_json["supported_scope"])
            and set(b.content_json["conditions"]).issubset(a.content_json["conditions"])
            and set(b.content_json["limitations"]).issubset(a.content_json["limitations"])
        ):
            raise AppError(422, "RELATION_SUBSET_UNPROVEN", "范围包含与条件限制未能保守证明")
        if body.relation_type in {"depends_on", "implements", "composes_with"}:
            source_ids = {e for item in a.assertions_json for e in item["evidence_ids"]}
            target_ids = {e for item in b.assertions_json for e in item["evidence_ids"]}
            source_files = {
                e["source_ref"]["file_id"]
                for e in evidence
                if e["id"] in source_ids and e["kind"] == "source"
            }
            target_files = {
                e["source_ref"]["file_id"]
                for e in evidence
                if e["id"] in target_ids and e["kind"] == "source"
            }
            target_names = {
                s.qualified_name.split(".")[-1]
                for s in db.scalars(
                    select(CodeSymbol).where(
                        CodeSymbol.snapshot_id == a.snapshot_id,
                        CodeSymbol.file_id.in_(target_files),
                    )
                )
            }
            edges = list(
                db.scalars(
                    select(CodeEdge).where(
                        CodeEdge.snapshot_id == a.snapshot_id, CodeEdge.file_id.in_(source_files)
                    )
                )
            )
            if not any(edge.target_name.split(".")[-1] in target_names for edge in edges):
                raise AppError(
                    422,
                    "RELATION_CALL_EVIDENCE_REQUIRED",
                    "缺少从源到目标的引用线索；名称匹配仍仅线索，需人工契约核对",
                )
    row = ReviewedRelation(
        snapshot_id=a.snapshot_id,
        source_revision_id=a.id,
        target_revision_id=b.id,
        relation_type=body.relation_type,
        decision=body.decision,
        details_json={
            **body.model_dump(),
            "status": "hypothesis"
            if body.relation_type == "possible_equivalent"
            else "human_reviewed",
        },
        reviewer_id="admin",
    )
    db.add(row)
    db.flush()
    event(
        db,
        a.snapshot_id,
        a.claim_id,
        "relation_" + body.decision,
        body.rationale,
        {"relation_id": row.id, "type": body.relation_type, "target_claim_id": b.claim_id},
    )
    return row


def merge(db, body):
    # 只读取不可变作用域定位，不在取得品类/快照锁前读取审核版本。
    with db.no_autoflush:
        scopes = dict(
            db.execute(
                select(CapabilityClaim.id, CapabilityClaim.snapshot_id).where(
                    CapabilityClaim.id.in_([body.source_claim_id, body.target_claim_id])
                )
            ).all()
        )
    if len(scopes) != 2 or len(set(scopes.values())) != 1:
        raise AppError(422, "MERGE_SCOPE_INVALID", "合并双方必须同快照不同候选")
    lock_snapshot(db, scopes[body.source_claim_id])
    source, target = latest(db, body.source_claim_id), latest(db, body.target_claim_id)
    if not source or not target:
        raise AppError(422, "MERGE_SCOPE_INVALID", "合并双方必须具有审核修订")
    if source.number != body.source_revision or target.number != body.target_revision:
        raise AppError(409, "MERGE_REVISION_CONFLICT", "修订已改变")
    if not body.human_confirmed or source.decision != "accept" or target.decision != "accept":
        raise AppError(422, "MERGE_REVIEW_REQUIRED", "只允许人工确认的同义能力合并")
    # 不以文本相似生成范围并集；所有业务边界必须严格一致（名字允许别名）。
    left = {k: v for k, v in source.content_json.items() if k != "name"}
    right = {k: v for k, v in target.content_json.items() if k != "name"}
    if source.declaration_type != target.declaration_type or left != right:
        raise AppError(
            422, "MERGE_BOUNDARY_MISMATCH", "动作、目标、机制、范围、条件及限制不一致，禁止扩大合并"
        )
    row = ClaimRevision(
        claim_id=source.claim_id,
        snapshot_id=source.snapshot_id,
        number=source.number + 1,
        declaration_type=source.declaration_type,
        decision="merged",
        content_json=source.content_json,
        assertions_json=source.assertions_json,
        gate_json=source.gate_json,
        merged_into=target.claim_id,
        reviewer_id="admin",
    )
    db.add(row)
    db.flush()
    event(
        db,
        source.snapshot_id,
        source.claim_id,
        "merge_same_as",
        body.comment,
        {
            "revision_id": row.id,
            "target_revision_id": target.id,
            "source_revision_id": source.id,
            "scope_union_performed": False,
        },
    )
    return row
