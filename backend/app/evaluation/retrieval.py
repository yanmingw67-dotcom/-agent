"""P2.2 只读固定发布版本检索与证据回读。

评估在任务创建时固定 publication_id 与 payload_hash；运行和重试只按该发布
清单读取。管理员之后发布或回滚不会改变已固定任务；固定发布被删除、哈希改变
或快照状态异常时失败关闭，不会退回当前生效版本。
"""

from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Document, DocumentSegment, SourceFile
from app.db.knowledge_models import Evidence
from app.db.models import Snapshot
from app.db.review_models import ClaimRevision, Publication
from app.evaluation.equivalence import assertion_evidence, coverage, terms
from app.ingestion.text import decode_source, digest, source_path
from app.knowledge.evidence_registry import canonical
from app.knowledge.review_schemas import ReviewedContent

ALLOWED_DECLARATIONS = {"product_function", "technical_mechanism"}
MAX_EVIDENCE_RANGE_LINES = 200


@dataclass
class Candidate:
    capability_id: str
    revision_id: str
    declaration_type: str
    content: dict
    assertions: list[dict]
    text: str
    evidence: dict = field(default_factory=dict)
    score: float = 0.0
    verified: bool = False
    error_code: str | None = None


@dataclass
class RetrievalResult:
    candidates: list[Candidate]
    gaps: list[dict]
    truncated: bool = False
    query_terms: list[str] = field(default_factory=list)


def _flatten(content: dict) -> dict[str, str]:
    expected = {}
    for key, value in content.items():
        if isinstance(value, list):
            expected.update({f"{key}.{index}": item for index, item in enumerate(value)})
        else:
            expected[key] = value
    return expected


def _identity(kind: str, source_ref: dict, locator: dict, source_hash: str, quote: str) -> dict:
    return {
        "snapshot_id": None,
        "kind": kind,
        "source_ref": source_ref,
        "locator": locator,
        "source_hash": source_hash,
        "quote_hash": digest(quote.encode()),
    }


class PinnedKnowledge:
    def __init__(self, db, job):
        self.db = db
        self.job = job
        self.settings = get_settings()
        self.cache: dict[str, dict] = {}
        publication = db.get(Publication, job.publication_id)
        if (
            not publication
            or publication.category_id != job.category_id
            or publication.snapshot_id != job.published_snapshot_id
        ):
            raise AppError(409, "PUBLICATION_PIN_UNAVAILABLE", "固定的已发布知识版本不可用")
        if digest(canonical(publication.payload_json).encode()) != publication.payload_hash:
            raise AppError(409, "PUBLICATION_TAMPERED", "发布清单哈希已改变")
        if publication.payload_hash != job.publication_hash:
            raise AppError(409, "PUBLICATION_TAMPERED", "任务固定的发布哈希不一致")
        snapshot = db.get(Snapshot, job.published_snapshot_id)
        if (
            not snapshot
            or snapshot.category_id != job.category_id
            or snapshot.status != "published"
        ):
            raise AppError(409, "PUBLICATION_PIN_UNAVAILABLE", "固定快照不再处于已发布状态")
        payload = publication.payload_json or {}
        if payload.get("snapshot_id") != job.published_snapshot_id:
            raise AppError(409, "PUBLICATION_TAMPERED", "发布清单快照不一致")
        self.snapshot_id = job.published_snapshot_id
        self.category_id = job.category_id
        self.capabilities = self._trusted_capabilities(payload.get("capabilities") or [])

    def _trusted_capabilities(self, capabilities: list[dict]) -> list[dict]:
        trusted = []
        for item in capabilities:
            if item.get("declaration_type") not in ALLOWED_DECLARATIONS:
                continue
            if item.get("review_status") != "human_accepted":
                continue
            revision_id = item.get("revision_id")
            revision = self.db.get(ClaimRevision, revision_id) if revision_id else None
            if (
                not revision
                or revision.snapshot_id != self.snapshot_id
                or revision.claim_id != item.get("id")
                or revision.decision != "accept"
                or revision.declaration_type != item.get("declaration_type")
            ):
                continue
            try:
                content = ReviewedContent.model_validate(item.get("content") or {}).model_dump()
            except Exception:
                continue
            assertions = item.get("assertions") or []
            trusted.append(
                {
                    "capability_id": item["id"],
                    "revision_id": revision_id,
                    "declaration_type": item["declaration_type"],
                    "content": content,
                    "assertions": assertions,
                    # 只把正向能力字段用于支持检索；限制、条件等否定文本不能变成支持证据。
                    "text": "\n".join(
                        str(value)
                        for key in (
                            "name",
                            "action",
                            "target",
                            "business_goal",
                            "implementation_mechanism",
                            "supported_scope",
                        )
                        for value in (
                            content.get(key)
                            if isinstance(content.get(key), list)
                            else [content.get(key)]
                        )
                        if value
                    ),
                }
            )
        return trusted

    def _read_evidence(self, eid: str) -> dict:
        if eid in self.cache:
            cached = self.cache[eid]
            if cached.get("error"):
                raise AppError(409, cached["error"], "证据回读失败")
            return cached
        row = self.db.scalar(
            select(Evidence).where(Evidence.id == eid, Evidence.snapshot_id == self.snapshot_id)
        )
        if not row:
            raise AppError(404, "EVIDENCE_NOT_FOUND", "固定快照内没有该证据")
        if row.kind == "source":
            result = self._verify_source(row)
        elif row.kind == "document":
            result = self._verify_document(row)
        else:
            raise AppError(422, "EVIDENCE_KIND_INVALID", "证据类型不可用")
        if result["id"] != row.id or result["quote_hash"] != row.quote_hash:
            raise AppError(409, "EVIDENCE_TAMPERED", "证据定位或哈希已改变")
        self.cache[eid] = result
        return result

    def _verify_source(self, row: Evidence) -> dict:
        file = self.db.get(SourceFile, row.source_ref.get("file_id"))
        if not file or file.snapshot_id != self.snapshot_id:
            raise AppError(404, "SOURCE_NOT_FOUND", "固定快照内没有来源文件")
        if file.sensitive or file.role == "test":
            raise AppError(403, "SOURCE_SCOPE_RESTRICTED", "来源文件不可用于产品能力证据")
        if file.parse_status in {"excluded", "oversized", "invalid_encoding"}:
            raise AppError(422, "SOURCE_UNREADABLE", "来源文件不可回读")
        path = source_path(self.settings.source_root, self.snapshot_id, file.path)
        if not path.exists():
            raise AppError(409, "SOURCE_NOT_FOUND", "来源文件不可回读")
        if path.stat().st_size > self.settings.index_max_file_bytes:
            raise AppError(413, "READ_SIZE_LIMIT", "来源文件超过回读限额")
        data = path.read_bytes()
        if digest(data) != file.sha256:
            raise AppError(409, "SOURCE_HASH_MISMATCH", "来源文件哈希变化")
        text, _ = decode_source(data)
        lines = text.splitlines()
        start = int(row.locator.get("start_line", 0))
        end = int(row.locator.get("end_line", 0))
        if start < 1 or end < start or start > len(lines):
            raise AppError(422, "EVIDENCE_RANGE_INVALID", "证据行范围无效")
        if end - start + 1 > min(MAX_EVIDENCE_RANGE_LINES, self.settings.read_max_lines):
            raise AppError(422, "EVIDENCE_RANGE_LIMIT", "证据行范围超限")
        end = min(end, len(lines))
        quote = "\n".join(lines[start - 1 : end])
        if not quote.strip() or digest(quote.encode()) != row.quote_hash:
            raise AppError(409, "EVIDENCE_HASH_MISMATCH", "证据引文哈希变化")
        source_ref = {"file_id": file.id, "path": file.path, "role": file.role}
        locator = {"start_line": start, "end_line": end}
        identity = _identity("source", source_ref, locator, file.sha256, quote)
        identity["snapshot_id"] = self.snapshot_id
        return {
            "id": "ev-" + digest(canonical(identity).encode()),
            "kind": "source",
            "source_ref": source_ref,
            "locator": locator,
            "source_hash": file.sha256,
            "quote_hash": row.quote_hash,
            "quote": quote,
        }

    def _verify_document(self, row: Evidence) -> dict:
        segment = self.db.get(DocumentSegment, row.source_ref.get("segment_id"))
        if not segment or segment.snapshot_id != self.snapshot_id or segment.sensitive:
            raise AppError(404, "SEGMENT_NOT_FOUND", "固定快照内没有可读取段落")
        document = self.db.scalar(
            select(Document).where(
                Document.id == segment.document_id,
                Document.snapshot_id == self.snapshot_id,
                Document.category_id == self.category_id,
            )
        )
        if not document or document.parse_status not in {"parsed", "partial"}:
            raise AppError(404, "DOCUMENT_NOT_FOUND", "固定快照内没有可用文档")
        original = self.settings.document_store / document.storage_key
        if not original.exists():
            raise AppError(409, "DOCUMENT_NOT_FOUND", "原始文档不可回读")
        if (
            original.stat().st_size > self.settings.document_max_bytes
            or digest(original.read_bytes()) != document.sha256
        ):
            raise AppError(409, "DOCUMENT_HASH_MISMATCH", "原始文档哈希变化")
        quote = segment.text
        if not quote.strip() or digest(quote.encode()) != segment.text_hash:
            raise AppError(409, "SEGMENT_HASH_MISMATCH", "文档分段哈希变化")
        if digest(quote.encode()) != row.quote_hash:
            raise AppError(409, "EVIDENCE_HASH_MISMATCH", "文档证据引文哈希变化")
        source_ref = {
            "document_id": document.id,
            "segment_id": segment.id,
            "path": document.filename,
        }
        identity = _identity("document", source_ref, segment.locator, document.sha256, quote)
        identity["snapshot_id"] = self.snapshot_id
        return {
            "id": "ev-" + digest(canonical(identity).encode()),
            "kind": "document",
            "source_ref": source_ref,
            "locator": segment.locator,
            "source_hash": document.sha256,
            "quote_hash": row.quote_hash,
            "quote": quote,
        }

    def verify_capability(self, capability: dict) -> Candidate:
        candidate = Candidate(
            capability_id=capability["capability_id"],
            revision_id=capability["revision_id"],
            declaration_type=capability["declaration_type"],
            content=capability["content"],
            assertions=capability["assertions"],
            text=capability["text"],
        )
        expected = _flatten(candidate.content)
        by_field = {item.get("field"): item for item in candidate.assertions}
        if set(by_field) != set(expected) or len(by_field) != len(candidate.assertions):
            candidate.error_code = "ASSERTION_FIELDS_INCOMPLETE"
            return candidate
        for field_name, value in expected.items():
            assertion = by_field[field_name]
            if assertion.get("text") != value or assertion.get("semantic_decision") != "supported":
                candidate.error_code = "ASSERTION_NOT_SUPPORTED"
                return candidate
        ids = assertion_evidence(candidate.assertions)
        if len(ids) > self.settings.evaluation_evidence_per_capability:
            candidate.error_code = "EVIDENCE_LIMIT"
            return candidate
        try:
            candidate.evidence = {eid: self._read_evidence(eid) for eid in ids}
        except AppError as exc:
            candidate.error_code = exc.error_code
            return candidate
        candidate.verified = True
        return candidate

    def retrieve(self, requirement: str, parameter_name: str) -> RetrievalResult:
        query = terms(requirement) | terms(parameter_name)
        if not query:
            return RetrievalResult([], [{"reason": "no_query_terms"}], query_terms=[])
        scored = []
        for capability in self.capabilities:
            text = capability["text"]
            score = coverage(requirement, text) + 0.2 * coverage(parameter_name, text)
            if not (query & terms(text)):
                continue
            scored.append((score, capability))
        scored.sort(key=lambda item: (-item[0], item[1]["capability_id"]))
        limit = self.settings.evaluation_retrieval_limit
        selected = scored[:limit]
        candidates = []
        gaps = []
        for score, capability in selected:
            item = self.verify_capability(capability)
            item.score = round(score, 4)
            if item.error_code:
                gaps.append(
                    {
                        "capability_id": capability["capability_id"],
                        "reason": item.error_code,
                    }
                )
                continue
            candidates.append(item)
        return RetrievalResult(
            candidates=candidates,
            gaps=gaps,
            truncated=len(scored) > limit,
            query_terms=sorted(query)[:30],
        )


def citation(candidate: Candidate) -> dict:
    evidence = list(candidate.evidence.values())
    return {
        "capability_id": candidate.capability_id,
        "revision_id": candidate.revision_id,
        "declaration_type": candidate.declaration_type,
        "content_hash": digest(canonical(candidate.content).encode()),
        "evidence_ids": [item["id"] for item in evidence[:20]],
        "channels": sorted({item["kind"] for item in evidence}),
        "score": candidate.score,
    }
