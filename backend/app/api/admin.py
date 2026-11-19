"""P3.1-A 管理员工作台只读聚合与文档重解析入口。

所有写操作仍复用既有品类、索引、调查、审核与发布服务；本模块不直接切换
品类生效指针，不修改发布清单，也不提供任何前端可绕过的模型授权开关。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.auth import admin, current_identity
from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Document, DocumentSegment
from app.db.knowledge_models import CapabilityClaim, DocumentDiscoveryRun
from app.db.models import AuditEvent, Category, KnowledgeJob, Snapshot
from app.db.review_models import ClaimRevision, ConflictFinding, Publication
from app.db.session import get_db
from app.knowledge.coverage import coverage
from app.knowledge.quality import gate

router = APIRouter(prefix="/api/v1", tags=["P3.1-A 管理员工作台"])


def _dt(value):
    return value.isoformat() if value else None


def snapshot_view(snapshot):
    return {
        "id": snapshot.id,
        "category_id": snapshot.category_id,
        "status": snapshot.status,
        "index_status": snapshot.index_status,
        "source_sha256": snapshot.source_sha256,
        "derived_from_snapshot_id": snapshot.derived_from_snapshot_id,
        "derivation_type": snapshot.derivation_type,
        "data_classification": snapshot.data_classification,
        "created_at": _dt(snapshot.created_at),
    }


def job_view(job, category_name=None):
    return {
        "id": job.id,
        "category_id": job.category_id,
        "category_name": category_name,
        "snapshot_id": job.snapshot_id,
        "job_type": job.job_type,
        "stage": job.stage,
        "status": job.status,
        "error_code": job.error_code,
        "attempt": job.attempt,
        "metrics": job.metrics_json or {},
        "created_at": _dt(job.created_at),
        "started_at": _dt(job.started_at),
        "finished_at": _dt(job.finished_at),
    }


def document_view(db, doc):
    return {
        "id": doc.id,
        "snapshot_id": doc.snapshot_id,
        "category_id": doc.category_id,
        "filename": doc.filename,
        "sha256": doc.sha256,
        "size": doc.size,
        "parse_status": doc.parse_status,
        "error_code": doc.error_code,
        "parser_version": doc.parser_version,
        "parse_details": doc.parse_details or {},
        "origin": doc.origin_json or {},
        "segment_count": db.scalar(
            select(func.count())
            .select_from(DocumentSegment)
            .where(DocumentSegment.document_id == doc.id)
        ),
    }


def _model_authorization():
    settings = get_settings()
    configured = bool(
        settings.builder_model
        and settings.builder_base_url
        and settings.builder_api_key.get_secret_value()
    )
    return {
        "server_side_authorization_required": True,
        "frontend_can_override": False,
        "builder": {
            "model_configured": configured,
            "allow_synthetic": settings.builder_allow_synthetic,
            "allow_enterprise": settings.builder_allow_enterprise,
            "document_discovery_mode": settings.document_discovery_mode,
            "status": (
                "NOT_INVOKED"
                if configured
                and (settings.builder_allow_synthetic or settings.builder_allow_enterprise)
                else "UNAVAILABLE"
            ),
        },
        "tender": {
            "model_mode": settings.tender_model_mode,
            "queue_enabled": settings.tender_queue_enabled,
        },
        "evaluation": {
            "model_mode": settings.evaluation_model_mode,
            "queue_enabled": settings.evaluation_queue_enabled,
        },
    }


def _latest_revision_map(db, claim_ids):
    if not claim_ids:
        return {}
    newest = (
        select(
            ClaimRevision.claim_id.label("claim_id"),
            func.max(ClaimRevision.number).label("number"),
        )
        .where(ClaimRevision.claim_id.in_(claim_ids))
        .group_by(ClaimRevision.claim_id)
        .subquery()
    )
    rows = db.execute(
        select(ClaimRevision).join(
            newest,
            and_(
                ClaimRevision.claim_id == newest.c.claim_id,
                ClaimRevision.number == newest.c.number,
            ),
        )
    ).scalars()
    return {row.claim_id: row for row in rows}


def _review_summary(db, claims, revisions):
    accepted = sum(
        1
        for claim in claims
        if revisions.get(claim.id) and revisions[claim.id].decision == "accept"
    )
    unreviewed = sum(1 for claim in claims if not revisions.get(claim.id))
    rejected = sum(
        1
        for claim in claims
        if revisions.get(claim.id) and revisions[claim.id].decision == "reject"
    )
    returned = sum(
        1
        for claim in claims
        if revisions.get(claim.id) and revisions[claim.id].decision == "return"
    )
    modified = sum(
        1
        for claim in claims
        if revisions.get(claim.id) and revisions[claim.id].decision == "modify"
    )
    merged = sum(
        1
        for claim in claims
        if revisions.get(claim.id) and revisions[claim.id].decision == "merged"
    )
    return {
        "total": len(claims),
        "accepted": accepted,
        "unreviewed": unreviewed,
        "rejected": rejected,
        "returned": returned,
        "modified": modified,
        "merged": merged,
        "pending": len(claims) - accepted,
    }


def _latest_by(rows, key):
    result = {}
    for row in rows:
        result.setdefault(key(row), row)
    return result


def _category_overviews(db):
    categories = list(db.scalars(select(Category).order_by(Category.code)))
    snapshots = list(
        db.scalars(select(Snapshot).order_by(Snapshot.created_at.desc(), Snapshot.id.desc()))
    )
    jobs = list(
        db.scalars(
            select(KnowledgeJob).order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
        )
    )
    latest_snapshots = _latest_by(snapshots, lambda row: row.category_id)
    latest_jobs = _latest_by(jobs, lambda row: row.category_id)
    document_counts = dict(
        db.execute(select(Document.category_id, func.count()).group_by(Document.category_id)).all()
    )
    publication_rows = list(
        db.scalars(
            select(Publication).order_by(Publication.created_at.desc(), Publication.id.desc())
        )
    )
    latest_publications = _latest_by(publication_rows, lambda row: row.category_id)
    claim_rows = list(
        db.execute(
            select(CapabilityClaim.id, Snapshot.category_id)
            .join(Snapshot, Snapshot.id == CapabilityClaim.snapshot_id)
            .limit(10000)
        ).all()
    )
    claims_by_category = {}
    for claim_id, category_id in claim_rows:
        claims_by_category.setdefault(category_id, []).append(claim_id)
    revisions = _latest_revision_map(db, [claim_id for claim_id, _ in claim_rows])
    result = []
    for category in categories:
        snap = latest_snapshots.get(category.id)
        job = latest_jobs.get(category.id)
        pub = latest_publications.get(category.id)
        pending = sum(
            1
            for claim_id in claims_by_category.get(category.id, [])
            if not revisions.get(claim_id) or revisions[claim_id].decision != "accept"
        )
        result.append(
            {
                "id": category.id,
                "code": category.code,
                "name": category.name,
                "knowledge_ready": category.active_snapshot_id is not None,
                "active_snapshot_id": category.active_snapshot_id,
                "active_publication_id": category.active_publication_id,
                "latest_snapshot": snapshot_view(snap) if snap else None,
                "latest_job": job_view(job) if job else None,
                "document_count": document_counts.get(category.id, 0),
                "pending_review_count": pending,
                "published_capability_count": len(pub.payload_json.get("capabilities", []))
                if pub
                else 0,
                "published_at": _dt(pub.created_at) if pub else None,
                "created_at": _dt(category.created_at),
            }
        )
    return result


@router.get("/auth/me")
def me(identity=Depends(current_identity)):
    return {
        "user_id": identity.user_id,
        "role": identity.role,
        "source": identity.source,
        "is_admin": identity.role == "admin",
        "auth_mode": "static_token",
        "sso_ready": False,
    }


@router.get("/admin/overview")
def overview(role=Depends(admin), db: Session = Depends(get_db)):
    rows = _category_overviews(db)
    failed_jobs = db.scalar(
        select(func.count()).select_from(KnowledgeJob).where(KnowledgeJob.status == "failed")
    )
    unpublished = db.scalar(
        select(func.count()).select_from(Snapshot).where(Snapshot.status == "extracted")
    )
    recent_jobs = list(
        db.scalars(
            select(KnowledgeJob)
            .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
            .limit(12)
        )
    )
    names = {row["id"]: row["name"] for row in rows}
    recent_publications = []
    publications = list(
        db.scalars(
            select(Publication)
            .order_by(Publication.created_at.desc(), Publication.id.desc())
            .limit(10)
        )
    )
    for pub in publications:
        recent_publications.append(
            {
                "publication_id": pub.id,
                "category_id": pub.category_id,
                "category_name": names.get(pub.category_id),
                "snapshot_id": pub.snapshot_id,
                "capability_count": len(pub.payload_json.get("capabilities", [])),
                "published_by": pub.published_by,
                "created_at": _dt(pub.created_at),
            }
        )
    return {
        "summary": {
            "category_count": len(rows),
            "ready_category_count": sum(1 for row in rows if row["knowledge_ready"]),
            "pending_review_count": sum(row["pending_review_count"] for row in rows),
            "failed_job_count": failed_jobs,
            "unpublished_snapshot_count": unpublished,
            "publication_count": db.scalar(select(func.count()).select_from(Publication)),
        },
        "categories": rows,
        "recent_jobs": [job_view(job, names.get(job.category_id)) for job in recent_jobs],
        "recent_publications": recent_publications,
        "model_authorization": _model_authorization(),
        "limits": {
            "document_types": [".docx", ".pdf", ".xlsx"],
            "document_only_build_supported": False,
            "document_only_note": "当前建库必须先有已索引受控源码；系统不会伪造源码快照。",
        },
    }


@router.get("/admin/categories")
def categories(role=Depends(admin), db: Session = Depends(get_db)):
    return {"items": _category_overviews(db)}


@router.get("/admin/jobs")
def jobs(
    category_id: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    query = select(KnowledgeJob)
    count_query = select(func.count()).select_from(KnowledgeJob)
    filters = []
    if category_id:
        filters.append(KnowledgeJob.category_id == category_id)
    if status:
        filters.append(KnowledgeJob.status == status)
    if job_type:
        filters.append(KnowledgeJob.job_type == job_type)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    rows = list(
        db.scalars(
            query.order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    names = dict(db.execute(select(Category.id, Category.name)).all())
    return {
        "total": db.scalar(count_query),
        "limit": limit,
        "offset": offset,
        "items": [job_view(job, names.get(job.category_id)) for job in rows],
    }


@router.get("/admin/categories/{category_id}")
def workspace(
    category_id: str,
    snapshot_id: str | None = None,
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    category = db.get(Category, category_id)
    if not category:
        raise AppError(404, "CATEGORY_NOT_FOUND", "品类不存在")
    snapshots = list(
        db.scalars(
            select(Snapshot)
            .where(Snapshot.category_id == category_id)
            .order_by(Snapshot.created_at.desc(), Snapshot.id.desc())
            .limit(30)
        )
    )
    jobs = list(
        db.scalars(
            select(KnowledgeJob)
            .where(KnowledgeJob.category_id == category_id)
            .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
            .limit(30)
        )
    )
    working = None
    if snapshot_id:
        working = db.scalar(
            select(Snapshot).where(Snapshot.id == snapshot_id, Snapshot.category_id == category_id)
        )
        if not working:
            raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在或不属于该品类")
    else:
        working = (
            db.scalar(
                select(Snapshot)
                .where(
                    Snapshot.category_id == category_id,
                    Snapshot.status == "extracted",
                    Snapshot.derivation_type == "document_update",
                )
                .order_by(Snapshot.created_at.desc(), Snapshot.id.desc())
                .limit(1)
            )
            or (
                db.get(Snapshot, category.active_snapshot_id)
                if category.active_snapshot_id
                else None
            )
            or next((row for row in snapshots if row.status == "extracted"), None)
        )
    docs = (
        list(
            db.scalars(
                select(Document)
                .where(Document.snapshot_id == working.id)
                .order_by(Document.filename, Document.id)
                .limit(200)
            )
        )
        if working
        else []
    )
    claims = (
        list(
            db.scalars(
                select(CapabilityClaim)
                .where(CapabilityClaim.snapshot_id == working.id)
                .order_by(CapabilityClaim.id)
                .limit(1000)
            )
        )
        if working
        else []
    )
    revisions = _latest_revision_map(db, [claim.id for claim in claims])
    active_publication = (
        db.get(Publication, category.active_publication_id)
        if category.active_publication_id
        else None
    )
    has_indexed_source = bool(
        db.scalar(
            select(Snapshot.id)
            .where(
                Snapshot.category_id == category_id,
                Snapshot.status == "extracted",
                Snapshot.index_status == "indexed",
                Snapshot.derivation_type.is_(None),
            )
            .limit(1)
        )
        or (
            category.active_snapshot_id
            and db.scalar(
                select(Snapshot.id).where(
                    Snapshot.id == category.active_snapshot_id,
                    Snapshot.index_status == "indexed",
                )
            )
        )
    )
    discovery = (
        db.scalar(
            select(DocumentDiscoveryRun)
            .where(DocumentDiscoveryRun.snapshot_id == working.id)
            .order_by(DocumentDiscoveryRun.created_at.desc())
            .limit(1)
        )
        if working
        else None
    )
    return {
        "category": {
            "id": category.id,
            "code": category.code,
            "name": category.name,
            "knowledge_ready": category.active_snapshot_id is not None,
            "active_snapshot_id": category.active_snapshot_id,
            "active_publication_id": category.active_publication_id,
        },
        "working_snapshot": snapshot_view(working) if working else None,
        "snapshots": [snapshot_view(row) for row in snapshots],
        "jobs": [job_view(job, category.name) for job in jobs],
        "documents": [document_view(db, doc) for doc in docs],
        "active_publication": {
            "publication_id": active_publication.id,
            "snapshot_id": active_publication.snapshot_id,
            "payload_hash": active_publication.payload_hash,
            "capability_count": len(active_publication.payload_json.get("capabilities", [])),
            "published_at": _dt(active_publication.created_at),
            "published_by": active_publication.published_by,
        }
        if active_publication
        else None,
        "review": _review_summary(db, claims, revisions),
        "document_discovery": {
            "run_id": discovery.id,
            "status": discovery.status,
            "mode": discovery.mode,
            "error_code": discovery.error_code,
            "metrics": discovery.metrics_json,
        }
        if discovery
        else None,
        "model_authorization": _model_authorization(),
        "capabilities": {
            "can_derive_documents": has_indexed_source,
            "source_required": True,
            "derive_blocker": None
            if has_indexed_source
            else "当前品类没有已索引受控源码；不能派生文档暂存快照，也不会伪造源码快照。",
        },
    }


@router.get("/admin/snapshots/{snapshot_id}/review-queue")
def review_queue(
    snapshot_id: str,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    claims = list(
        db.scalars(
            select(CapabilityClaim)
            .where(CapabilityClaim.snapshot_id == snapshot_id)
            .order_by(CapabilityClaim.id)
            .offset(cursor)
            .limit(limit + 1)
        )
    )
    page = claims[:limit]
    revisions = _latest_revision_map(db, [claim.id for claim in page])
    all_claims = list(
        db.scalars(select(CapabilityClaim).where(CapabilityClaim.snapshot_id == snapshot_id))
    )
    all_revisions = _latest_revision_map(db, [claim.id for claim in all_claims])
    items = []
    for claim in page:
        rev = revisions.get(claim.id)
        content = rev.content_json if rev else claim.draft_json
        items.append(
            {
                "claim_id": claim.id,
                "name": content.get("name") or claim.name,
                "status": claim.status,
                "origin": claim.origin_json,
                "draft": claim.draft_json,
                "latest_revision": {
                    "id": rev.id,
                    "number": rev.number,
                    "decision": rev.decision,
                    "declaration_type": rev.declaration_type,
                    "content": rev.content_json,
                    "assertions_count": len(rev.assertions_json),
                    "gate": rev.gate_json,
                    "availability_basis": rev.availability_basis,
                    "product_scope_confirmed": rev.product_scope_confirmed,
                    "proof_kind": rev.proof_kind,
                }
                if rev
                else None,
            }
        )
    accepted_ids = [
        rev.id
        for claim in all_claims
        if (rev := all_revisions.get(claim.id)) and rev.decision == "accept"
    ]
    return {
        "snapshot_id": snapshot_id,
        "snapshot": snapshot_view(snapshot),
        "summary": _review_summary(db, all_claims, all_revisions),
        "accepted_revision_ids": accepted_ids,
        "truncated": len(claims) > limit,
        "next_cursor": cursor + limit if len(claims) > limit else None,
        "items": items,
    }


def _revision_blockers(db, revision):
    errors = []
    try:
        result = gate(
            db,
            revision.snapshot_id,
            revision.declaration_type,
            revision.content_json,
            revision.assertions_json,
            revision.gate_json.get("reachability_evidence_ids", []),
            availability_basis=revision.availability_basis,
            product_scope_confirmed=revision.product_scope_confirmed,
        )
        errors.extend(result.get("errors", []))
    except AppError as exc:
        errors.append(exc.error_code)
    unresolved = db.scalar(
        select(ConflictFinding.id)
        .where(
            ConflictFinding.snapshot_id == revision.snapshot_id,
            ConflictFinding.status == "unresolved",
            (ConflictFinding.source_claim_id == revision.claim_id)
            | (ConflictFinding.target_claim_id == revision.claim_id),
        )
        .limit(1)
    )
    if unresolved:
        errors.append("UNRESOLVED_CONFLICT")
    return sorted(set(errors))


@router.get("/admin/snapshots/{snapshot_id}/release-check")
def release_check(snapshot_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    category = db.get(Category, snapshot.category_id)
    claims = list(
        db.scalars(select(CapabilityClaim).where(CapabilityClaim.snapshot_id == snapshot_id))
    )
    revisions = _latest_revision_map(db, [claim.id for claim in claims])
    published_revision_ids = set()
    if category and category.active_publication_id:
        active = db.get(Publication, category.active_publication_id)
        if active:
            published_revision_ids = {
                item.get("revision_id")
                for item in active.payload_json.get("capabilities", [])
                if item.get("revision_id")
            }
    items, accepted_ids, blocking = [], [], []
    for claim in claims:
        rev = revisions.get(claim.id)
        if not rev:
            continue
        blockers = _revision_blockers(db, rev) if rev.decision == "accept" else []
        if rev.decision == "accept":
            accepted_ids.append(rev.id)
            if blockers:
                blocking.append(
                    {
                        "claim_id": claim.id,
                        "revision_id": rev.id,
                        "name": rev.content_json.get("name"),
                        "errors": blockers,
                    }
                )
        items.append(
            {
                "claim_id": claim.id,
                "revision_id": rev.id,
                "name": rev.content_json.get("name"),
                "decision": rev.decision,
                "declaration_type": rev.declaration_type,
                "gate_errors": rev.gate_json.get("errors", []),
                "blockers": blockers,
                "published": rev.id in published_revision_ids,
            }
        )
    if not accepted_ids:
        blocking.append({"claim_id": None, "revision_id": None, "errors": ["NO_ACCEPTED_REVISION"]})
    unpublished = [
        revision_id for revision_id in accepted_ids if revision_id not in published_revision_ids
    ]
    if accepted_ids and not unpublished:
        blocking.append(
            {
                "claim_id": None,
                "revision_id": None,
                "errors": ["NO_UNPUBLISHED_ACCEPTED_REVISION"],
            }
        )
    return {
        "snapshot_id": snapshot_id,
        "category_id": snapshot.category_id,
        "expected_active_publication_id": category.active_publication_id if category else None,
        "accepted_revision_ids": accepted_ids,
        "items": items,
        "blocking": blocking,
        "publishable": bool(unpublished) and not blocking,
        "coverage": coverage(db, snapshot_id),
        "coverage_acknowledgement_required": True,
        "relations_available": False,
    }


@router.get("/admin/snapshots/{snapshot_id}/documents")
def documents(snapshot_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    rows = list(
        db.scalars(
            select(Document)
            .where(Document.snapshot_id == snapshot_id)
            .order_by(Document.filename, Document.id)
            .limit(200)
        )
    )
    return {
        "snapshot": snapshot_view(snapshot),
        "items": [document_view(db, row) for row in rows],
        "document_only_build_supported": False,
    }


@router.get("/admin/documents/{document_id}")
def document_detail(document_id: str, role=Depends(admin), db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "文档不存在")
    segments = list(
        db.scalars(
            select(DocumentSegment)
            .where(DocumentSegment.document_id == document_id)
            .order_by(DocumentSegment.id)
            .limit(50)
        )
    )
    return {
        **document_view(db, doc),
        "segments": [
            {
                "id": segment.id,
                "locator": segment.locator,
                "text": segment.text,
                "text_hash": segment.text_hash,
                "sensitive": segment.sensitive,
            }
            for segment in segments
        ],
        "segments_truncated": len(segments) == 50,
    }


@router.get("/admin/audit")
def audit_log(
    category_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    role=Depends(admin),
    db: Session = Depends(get_db),
):
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    if category_id:
        snapshot_ids = list(
            db.scalars(select(Snapshot.id).where(Snapshot.category_id == category_id))
        )
        claim_ids = list(
            db.scalars(
                select(CapabilityClaim.id)
                .join(Snapshot, Snapshot.id == CapabilityClaim.snapshot_id)
                .where(Snapshot.category_id == category_id)
            )
        )
        document_ids = list(
            db.scalars(select(Document.id).where(Document.category_id == category_id))
        )
        publication_ids = list(
            db.scalars(select(Publication.id).where(Publication.category_id == category_id))
        )
        resources = [category_id, *snapshot_ids, *claim_ids, *document_ids, *publication_ids]
        query = query.where(AuditEvent.resource_id.in_(resources))
    rows = list(db.scalars(query.limit(limit)))
    return {
        "items": [
            {
                "id": row.id,
                "principal_id": row.principal_id,
                "action": row.action,
                "resource_id": row.resource_id,
                "correlation_id": row.correlation_id,
                "details": row.details_json,
                "created_at": _dt(row.created_at),
            }
            for row in rows
        ]
    }
