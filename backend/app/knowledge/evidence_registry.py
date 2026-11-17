"""只登记回读所得真实来源，不评价业务语义是否成立。"""

import json

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Document, DocumentSegment
from app.db.knowledge_models import Evidence
from app.ingestion.text import digest
from app.knowledge.schemas import EvidenceReceipt, EvidenceRequest
from app.retrieval.schemas import ToolRequest
from app.retrieval.tools import ReadOnlyTools


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def verify_source(db, context, request: EvidenceRequest):
    tools = ReadOnlyTools(db, context)
    if request.kind == "source":
        if request.end_line - request.start_line + 1 > get_settings().read_max_lines:
            raise AppError(422, "EVIDENCE_RANGE_LIMIT", "证据行范围超限")
        file = tools.file(request.file_id)
        item = tools.read(file, request.start_line, request.end_line)
        if item.end_line != request.end_line or not item.snippet.strip():
            raise AppError(422, "EVIDENCE_RANGE_INVALID", "证据行范围不完整或为空")
        expected, source_hash = item.sha256, file.sha256
        source_ref = {"file_id": file.id, "path": file.path, "role": file.role}
        locator = {"start_line": item.start_line, "end_line": item.end_line}
        quote = item.snippet
        warnings = ["semantic_support_unverified", "runtime_reachability_unverified"]
        if file.role == "test":
            warnings.append("test_only_not_product_capability")
    else:
        result = tools.execute(ToolRequest(tool="read_document", segment_id=request.segment_id))
        if result.truncated or not result.results or not result.results[0].snippet.strip():
            raise AppError(422, "EVIDENCE_INCOMPLETE", "证据分段不可完整回读")
        item = result.results[0]
        segment = db.get(DocumentSegment, request.segment_id)
        doc = db.scalar(
            select(Document).where(
                Document.id == segment.document_id,
                Document.snapshot_id == context.snapshot_id,
                Document.category_id == context.category_id,
            )
        )
        if not doc:
            raise AppError(404, "DOCUMENT_NOT_FOUND", "作用域不匹配")
        original = get_settings().document_store / doc.storage_key
        if (
            original.stat().st_size > get_settings().document_max_bytes
            or digest(original.read_bytes()) != doc.sha256
        ):
            raise AppError(409, "DOCUMENT_HASH_MISMATCH", "原始文档哈希变化")
        if digest(item.snippet.encode()) != segment.text_hash:
            raise AppError(409, "SEGMENT_HASH_MISMATCH", "文档分段哈希变化")
        expected, source_hash = segment.text_hash, doc.sha256
        source_ref = {"document_id": doc.id, "segment_id": segment.id, "path": doc.filename}
        locator, quote = segment.locator, item.snippet
        warnings = ["semantic_support_unverified"]
        if result.coverage_incomplete:
            warnings.append("document_partial")
    if expected != request.expected_sha256:
        raise AppError(409, "EVIDENCE_HASH_MISMATCH", "回读哈希与登记请求不符")
    identity = {
        "snapshot_id": context.snapshot_id,
        "kind": request.kind,
        "source_ref": source_ref,
        "locator": locator,
        "source_hash": source_hash,
        "quote_hash": digest(quote.encode()),
    }
    return identity, warnings, quote


def register(db, context, request: EvidenceRequest):
    identity, warnings, _ = verify_source(db, context, request)
    eid = "ev-" + digest(canonical(identity).encode())
    row = db.get(Evidence, eid)
    if not row:
        row = Evidence(id=eid, **identity, warnings_json=warnings)
        db.add(row)
        db.flush()
    return EvidenceReceipt(
        evidence_id=eid,
        snapshot_id=context.snapshot_id,
        source_ref=row.source_ref,
        locator=row.locator,
        quote_hash=row.quote_hash,
        warnings=row.warnings_json,
    )


def read_evidence(db, context, eid):
    ReadOnlyTools(db, context)
    row = db.scalar(
        select(Evidence).where(Evidence.id == eid, Evidence.snapshot_id == context.snapshot_id)
    )
    if not row:
        raise AppError(404, "EVIDENCE_NOT_FOUND", "作用域内没有该证据")
    request = EvidenceRequest(
        kind=row.kind,
        file_id=row.source_ref.get("file_id"),
        segment_id=row.source_ref.get("segment_id"),
        start_line=row.locator.get("start_line", 1),
        end_line=row.locator.get("end_line", 1),
        expected_sha256=row.source_hash if row.kind == "source" else row.quote_hash,
        supported_statement="重新核验来源，不确认业务语义",
    )
    identity, _, quote = verify_source(db, context, request)
    if (
        row.id != "ev-" + digest(canonical(identity).encode())
        or row.quote_hash != identity["quote_hash"]
    ):
        raise AppError(409, "EVIDENCE_TAMPERED", "证据定位或哈希已改变")
    return {
        "id": row.id,
        **identity,
        "quote": quote,
        "verification_state": "source_verified",
        "semantic_verification": "unverified",
        "warnings": row.warnings_json,
    }
