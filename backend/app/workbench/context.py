"""固定发布版本的能力适用范围与证据展示辅助。

这里只读取 P2.2 任务已经固定的 publication_id，不读取当前生效指针。
发布清单哈希、任务固定哈希或快照归属不一致时返回错误码，由调用方决定
页面标注“证据来源不可用”或导出失败关闭；绝不静默替换为其他来源。
"""

from app.db.models import Snapshot
from app.db.review_models import ClaimRevision, Publication
from app.ingestion.text import digest
from app.knowledge.evidence_registry import canonical

ALLOWED_DECLARATIONS = {"product_function", "technical_mechanism"}


def pinned_capabilities(db, job):
    """返回 (capability_id -> 展示上下文, error_code)。"""
    publication = db.get(Publication, job.publication_id)
    if (
        not publication
        or publication.category_id != job.category_id
        or publication.snapshot_id != job.published_snapshot_id
    ):
        return {}, "PUBLICATION_PIN_UNAVAILABLE"
    try:
        if digest(canonical(publication.payload_json).encode()) != publication.payload_hash:
            return {}, "PUBLICATION_TAMPERED"
    except Exception:
        return {}, "PUBLICATION_TAMPERED"
    if publication.payload_hash != job.publication_hash:
        return {}, "PUBLICATION_TAMPERED"
    snapshot = db.get(Snapshot, job.published_snapshot_id)
    if not snapshot or snapshot.category_id != job.category_id or snapshot.status != "published":
        return {}, "PUBLICATION_PIN_UNAVAILABLE"

    capabilities = {}
    for item in publication.payload_json.get("capabilities") or []:
        if item.get("declaration_type") not in ALLOWED_DECLARATIONS:
            continue
        if item.get("review_status") != "human_accepted":
            continue
        revision_id = item.get("revision_id")
        revision = db.get(ClaimRevision, revision_id) if revision_id else None
        if (
            not revision
            or revision.snapshot_id != job.published_snapshot_id
            or revision.claim_id != item.get("id")
            or revision.decision != "accept"
            or revision.declaration_type != item.get("declaration_type")
        ):
            continue
        content = item.get("content") or {}
        capabilities[item["id"]] = {
            "capability_id": item["id"],
            "revision_id": revision_id,
            "declaration_type": item.get("declaration_type"),
            "name": content.get("name") or "",
            "supported_scope": content.get("supported_scope") or [],
            "conditions": content.get("conditions") or [],
            "limitations": content.get("limitations") or [],
            "implementation_mechanism": content.get("implementation_mechanism") or "",
        }
    return capabilities, None


def source_type_label(kind: str):
    return {"source": "源码", "document": "技术文档"}.get(kind, kind or "未知")


def _join(values):
    if isinstance(values, list):
        return "；".join(str(value) for value in values if value)
    return str(values or "")


def locator_text(locator: dict | None):
    locator = locator or {}
    if locator.get("start_line") and locator.get("end_line"):
        return f"第 {locator['start_line']}-{locator['end_line']} 行"
    parts = []
    labels = (
        ("page", "第 {} 页"),
        ("sheet", "工作表 {}"),
        ("paragraph", "第 {} 段"),
        ("row", "第 {} 行"),
        ("cell", "单元格 {}"),
        ("section", "章节 {}"),
        ("line", "第 {} 行"),
    )
    for key, template in labels:
        value = locator.get(key)
        if value not in (None, ""):
            parts.append(template.format(value))
    if parts:
        return " ".join(parts)
    return "；".join(f"{key}={value}" for key, value in locator.items() if value not in (None, ""))


def source_file(source_ref: dict | None):
    source_ref = source_ref or {}
    return source_ref.get("path") or source_ref.get("file_id") or source_ref.get("segment_id") or ""


def enrich_evidence(item: dict, capabilities: dict, context_error: str | None):
    capability = capabilities.get(item.get("capability_id")) or {}
    available = bool(capability)
    enriched = dict(item)
    enriched.update(
        {
            "source_type_label": source_type_label(item.get("kind", "")),
            "source_file": source_file(item.get("source_ref")),
            "locator_text": locator_text(item.get("locator")),
            "capability_name": capability.get("name", ""),
            "declaration_type": capability.get("declaration_type", ""),
            "supported_scope": capability.get("supported_scope") or [],
            "conditions": capability.get("conditions") or [],
            "limitations": capability.get("limitations") or [],
            "supported_scope_text": _join(capability.get("supported_scope")),
            "conditions_text": _join(capability.get("conditions")),
            "limitations_text": _join(capability.get("limitations")),
            "context_status": "available" if available else "unavailable",
            "context_note": (
                ""
                if available
                else "固定发布版本中未找到该证据对应的已审核能力修订；不替换其他来源。"
                + (f"（{context_error}）" if context_error else "")
            ),
        }
    )
    return enriched
