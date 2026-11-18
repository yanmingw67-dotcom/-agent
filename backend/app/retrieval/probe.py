"""只读Knowledge Probe：生效清单检索，不调用模型、不读候选正文、不写知识库。"""

import re
import time

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.metrics import observe
from app.core.security import AppError
from app.db.models import Category, Snapshot
from app.db.review_models import ClaimRevision, Publication, ReviewedRelation
from app.db.session import get_engine
from app.ingestion.text import digest
from app.knowledge.evidence_registry import canonical, read_evidence
from app.retrieval.probe_schemas import (
    ProbeCapability,
    ProbeEvidence,
    ProbeGap,
    ProbeRelation,
    ProbeResponse,
)
from app.retrieval.schemas import RunContext


def probe_db():
    """每请求独立只读一致性事务，不复用后台写事务或队列。"""
    from sqlalchemy.orm import Session

    with get_engine().connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            connection.exec_driver_sql("PRAGMA query_only=ON")
            connection.commit()
        try:
            with Session(connection, autoflush=False, expire_on_commit=False) as db:
                if not sqlite:
                    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                yield db
                db.rollback()
        finally:
            connection.rollback()
            if sqlite:
                connection.exec_driver_sql("PRAGMA query_only=OFF")
                connection.commit()


def terms(value):
    chunks = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", value.casefold())
    result = []
    for chunk in chunks:
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk) and len(chunk) > 2:
            result.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            result.append(chunk)
    return sorted(set(result))[:100]


def capability_evidence(item):
    ids = set(item["reachability_evidence_ids"])
    for assertion in item["assertions"]:
        ids.update(assertion["evidence_ids"])
        ids.update(assertion.get("implementation_evidence_ids", []))
        for dependency in assertion.get("dependencies", []):
            ids.update(dependency["evidence_ids"])
    return sorted(ids)


class KnowledgeProbe:
    """未来调用方仅调用retrieve；无审核、证据登记、发布、文件写入或Shell工具。"""

    def __init__(self, db, category_id, principal_id):
        if principal_id not in {"read", "admin"}:
            raise AppError(403, "FORBIDDEN", "需要只读或管理员身份")
        self.db, self.category_id, self.principal_id = db, category_id, principal_id

    def retrieve(self, requirement_text, limit=10):
        started = time.monotonic()
        settings = get_settings()
        category = self.db.get(Category, self.category_id, populate_existing=True)
        if not category:
            raise AppError(404, "CATEGORY_NOT_FOUND", "产品品类不存在")
        if not category.active_publication_id:
            raise AppError(
                409, "KNOWLEDGE_NOT_PUBLISHED", "当前品类没有已审核发布知识库；不代表不支持"
            )
        publication = self.db.get(Publication, category.active_publication_id)
        snapshot = (
            self.db.get(Snapshot, category.active_snapshot_id)
            if category.active_snapshot_id
            else None
        )
        if (
            not publication
            or not snapshot
            or publication.category_id != category.id
            or publication.snapshot_id != snapshot.id
            or snapshot.category_id != category.id
            or snapshot.status != "published"
        ):
            raise AppError(409, "PUBLICATION_POINTER_INVALID", "生效清单和源码指针不一致")
        payload = publication.payload_json
        if digest(canonical(payload).encode()) != publication.payload_hash:
            raise AppError(409, "PUBLICATION_TAMPERED", "生效清单指纹不一致")
        if payload.get("snapshot_id") != snapshot.id:
            raise AppError(409, "PUBLICATION_SCOPE_INVALID", "发布清单作用域不一致")
        caps = payload.get("capabilities", [])
        if len(caps) > 100 or len(canonical(payload).encode()) > 4 * 1024**2:
            raise AppError(413, "PROBE_PUBLICATION_LIMIT", "发布清单超过当前Probe处理上限")
        context = RunContext(
            category_id=category.id,
            snapshot_id=snapshot.id,
            principal_id=self.principal_id,
            policy="published",
        )
        response = ProbeResponse(
            status="no_match",
            category_id=category.id,
            publication_id=publication.id,
            snapshot_id=snapshot.id,
            publication_hash=publication.payload_hash,
            candidates=[],
            relations=[],
            evidence=[],
            coverage=payload.get("coverage", {}),
            unverified_gaps=[],
            warnings=[
                "lexical_retrieval_not_semantic_proof",
                "no_match_does_not_mean_unsupported",
                "coverage_gaps_do_not_mean_unsupported",
                "source_text_is_untrusted_data",
                "semantic_index_unavailable",
            ],
        )
        response.unverified_gaps.append(ProbeGap(code="REQUIREMENT_COMPLIANCE_NOT_ASSESSED"))
        if response.coverage.get("coverage_incomplete", True):
            response.unverified_gaps.append(ProbeGap(code="KNOWLEDGE_COVERAGE_INCOMPLETE"))
        claim_ids = [c["id"] for c in caps]
        numbers = (
            dict(
                self.db.execute(
                    select(ClaimRevision.claim_id, func.max(ClaimRevision.number))
                    .where(
                        ClaimRevision.snapshot_id == snapshot.id,
                        ClaimRevision.claim_id.in_(claim_ids),
                    )
                    .group_by(ClaimRevision.claim_id)
                ).all()
            )
            if claim_ids
            else {}
        )
        revisions = (
            {
                r.id: r
                for r in self.db.scalars(
                    select(ClaimRevision).where(
                        ClaimRevision.id.in_([c["revision_id"] for c in caps]),
                        ClaimRevision.snapshot_id == snapshot.id,
                    )
                )
            }
            if caps
            else {}
        )
        query_terms = terms(requirement_text)
        ranked = []
        for item in caps:
            r = revisions.get(item["revision_id"])
            if (
                not r
                or r.claim_id != item["id"]
                or r.decision != "accept"
                or item.get("review_status") != "human_accepted"
                or item["declaration_type"] not in {"product_function", "technical_mechanism"}
                or r.declaration_type != item["declaration_type"]
                or r.content_json != item["content"]
                or r.assertions_json != item["assertions"]
                or r.gate_json.get("reachability_evidence_ids", [])
                != item["reachability_evidence_ids"]
            ):
                response.unverified_gaps.append(
                    ProbeGap(code="PUBLICATION_REVISION_INVALID", capability_id=item["id"])
                )
                continue
            if r.number != numbers.get(r.claim_id):
                response.unverified_gaps.append(
                    ProbeGap(code="NEWER_REVIEW_NOT_PUBLISHED", capability_id=item["id"])
                )
                continue
            haystack = " ".join(
                text
                for value in item["content"].values()
                for text in (value if isinstance(value, list) else [value])
            ).casefold()
            matches = [t for t in query_terms if t in haystack]
            if matches:
                ranked.append((len(matches), item["id"], item, r, matches))
        ranked.sort(key=lambda v: (-v[0], v[1]))
        evidence_cache = {}

        def verify(ids):
            for eid in ids:
                if eid not in evidence_cache:
                    if len(evidence_cache) >= 100:
                        raise AppError(413, "PROBE_EVIDENCE_LIMIT", "证据数量超限")
                    evidence_cache[eid] = ProbeEvidence.model_validate(
                        read_evidence(self.db, context, eid)
                    )
            return [evidence_cache[eid] for eid in ids]

        for _, _, item, r, matches in ranked:
            if (
                len(response.candidates) >= limit
                or time.monotonic() - started > settings.search_timeout_seconds
            ):
                response.truncated = True
                break
            ids = capability_evidence(item)
            try:
                verified = verify(ids)
            except (AppError, OSError) as exc:
                code = (
                    exc.error_code if isinstance(exc, AppError) else "EVIDENCE_SOURCE_UNAVAILABLE"
                )
                response.unverified_gaps.append(ProbeGap(code=code, capability_id=item["id"]))
                continue
            candidate = ProbeCapability(
                capability_id=item["id"],
                revision_id=r.id,
                declaration_type=item["declaration_type"],
                content=item["content"],
                assertions=item["assertions"],
                reachability_evidence_ids=item["reachability_evidence_ids"],
                evidence_ids=ids,
                review_status="human_accepted",
                reviewer_id=r.reviewer_id,
                reviewed_at=r.created_at.isoformat(),
                match_terms=matches,
            )
            previous = list(response.evidence)
            response.candidates.append(candidate)
            current_ids = {e.id for e in response.evidence}
            response.evidence.extend(e for e in verified if e.id not in current_ids)
            if len(response.model_dump_json().encode()) > 128 * 1024:
                response.candidates.pop()
                response.evidence = previous
                response.truncated = True
                break
        selected = {c.revision_id for c in response.candidates}
        for item in payload.get("relations", []):
            if not {item["source_revision_id"], item["target_revision_id"]}.issubset(selected):
                continue
            relation = self.db.get(ReviewedRelation, item["id"])
            last = self.db.scalar(
                select(ReviewedRelation.id)
                .where(
                    ReviewedRelation.source_revision_id == item["source_revision_id"],
                    ReviewedRelation.target_revision_id == item["target_revision_id"],
                    ReviewedRelation.relation_type == item["type"],
                    ReviewedRelation.snapshot_id == snapshot.id,
                )
                .order_by(ReviewedRelation.created_at.desc(), ReviewedRelation.id.desc())
                .limit(1)
            )
            if (
                not relation
                or relation.snapshot_id != snapshot.id
                or relation.decision != "accept"
                or last != relation.id
                or relation.details_json != item["details"]
                or relation.source_revision_id != item["source_revision_id"]
                or relation.target_revision_id != item["target_revision_id"]
                or relation.relation_type != item["type"]
            ):
                response.unverified_gaps.append(
                    ProbeGap(code="RELATION_REVIEW_INVALID", relation_id=item["id"])
                )
                continue
            try:
                verified = verify(item["details"]["evidence_ids"])
            except (AppError, OSError) as exc:
                code = (
                    exc.error_code if isinstance(exc, AppError) else "EVIDENCE_SOURCE_UNAVAILABLE"
                )
                response.unverified_gaps.append(ProbeGap(code=code, relation_id=item["id"]))
                continue
            previous = list(response.evidence)
            response.relations.append(
                ProbeRelation(
                    **item,
                    status="hypothesis"
                    if item["type"] == "possible_equivalent"
                    else "human_reviewed",
                )
            )
            current_ids = {e.id for e in response.evidence}
            response.evidence.extend(e for e in verified if e.id not in current_ids)
            if len(response.model_dump_json().encode()) > 128 * 1024:
                response.relations.pop()
                response.evidence = previous
                response.truncated = True
                break
        if not caps:
            response.status = "knowledge_empty"
            response.unverified_gaps.append(ProbeGap(code="NO_PUBLISHED_CAPABILITIES"))
        elif response.candidates:
            response.status = "candidate_retrieval"
        else:
            response.unverified_gaps.append(ProbeGap(code="NO_VERIFIED_LEXICAL_MATCH"))
        if response.truncated:
            response.unverified_gaps.append(
                ProbeGap(
                    code="PROBE_RESULT_LIMIT",
                )
            )
        if response.truncated or any(
            g.capability_id or g.relation_id for g in response.unverified_gaps
        ):
            response.status = "partial"
        response.duration_ms = round((time.monotonic() - started) * 1000, 3)
        if len(response.model_dump_json().encode()) > 128 * 1024:
            raise AppError(
                413, "PROBE_RESPONSE_LIMIT", "完整边界和覆盖信息超限；未截断为不完整能力"
            )
        observe("probe_requests", response.duration_ms)
        return response
