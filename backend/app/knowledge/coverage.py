"""覆盖是定位观察，不是全仓语义召回率。"""

from sqlalchemy import select

from app.db.index_models import DocumentSegment, Entrypoint, SourceFile
from app.db.knowledge_models import CapabilityClaim, Investigation
from app.knowledge.quality import latest


def coverage(db, sid):
    files = list(db.scalars(select(SourceFile).where(SourceFile.snapshot_id == sid)))
    runs = list(db.scalars(select(Investigation).where(Investigation.snapshot_id == sid)))
    analyzed = {}
    pending, deferred, assertions = [], [], []
    for run in runs:
        state = run.metrics_json.get("exploration", {})
        for task in state.get("tasks", []):
            if task["status"] == "done":
                analyzed.setdefault(task["file_id"], []).append(
                    [task["start_line"], task["end_line"]]
                )
            else:
                pending.append(task)
        deferred.extend(state.get("deferred", []))
    for claim in db.scalars(select(CapabilityClaim).where(CapabilityClaim.snapshot_id == sid)):
        revision = latest(db, claim.id)
        if not revision or revision.decision != "accept" or revision.gate_json.get("errors"):
            assertions.append(
                {
                    "claim_id": claim.id,
                    "review_decision": revision.decision if revision else "unreviewed",
                    "gaps": revision.gate_json if revision else {"reason": "assertions_unverified"},
                }
            )
    entries = list(db.scalars(select(Entrypoint).where(Entrypoint.snapshot_id == sid)))
    segments = list(
        db.scalars(select(DocumentSegment.id).where(DocumentSegment.snapshot_id == sid).limit(1001))
    )
    return {
        "snapshot_id": sid,
        "coverage_incomplete": True,
        "knowledge_build_complete": False,
        "semantic_coverage": "unknown",
        "source_file_count": len(files),
        "analyzed_ranges": analyzed,
        "uninvestigated_files": [
            {"id": f.id, "path": f.path, "status": f.parse_status}
            for f in files
            if f.id not in analyzed
        ],
        "unexplained_entrypoints": [
            {"id": e.id, "type": e.type, "source_ref": e.source_ref} for e in entries
        ],
        "pending": pending,
        "deferred": deferred,
        "unverified_assertions": assertions,
        "unverified_document_segments": segments[:1000],
        "documents_truncated": len(segments) > 1000,
        "note": "已阅读入口不自动视为运行时接线已验证；普通bounded及旧explore没有全片段覆盖记录。",
    }


def coverage_summary(value):
    """只读发布的覆盖摘要；不暴露候选、审核、任务或源码文件明细。

    原始 coverage 是管理员定位/审核诊断数据。普通读取契约只保留计数和缺口类型，
    避免把发布时尚未审核的候选、内部调查任务或源码清单带入 Probe。
    """
    data = value if isinstance(value, dict) else {}

    def count(key):
        items = data.get(key)
        return len(items) if isinstance(items, (list, dict, tuple, set)) else 0

    coverage_incomplete = bool(data.get("coverage_incomplete", True))
    knowledge_build_complete = bool(data.get("knowledge_build_complete", False))
    semantic = data.get("semantic_coverage")
    semantic_coverage = semantic if isinstance(semantic, str) and semantic else "unknown"
    source_file_count = data.get("source_file_count")
    if not isinstance(source_file_count, int) or source_file_count < 0:
        source_file_count = 0
    result = {
        "coverage_incomplete": coverage_incomplete,
        "knowledge_build_complete": knowledge_build_complete,
        "semantic_coverage": semantic_coverage,
        "source_file_count": source_file_count,
        "uninvestigated_file_count": count("uninvestigated_files"),
        "unexplained_entrypoint_count": count("unexplained_entrypoints"),
        "pending_investigation_count": count("pending"),
        "deferred_investigation_count": count("deferred"),
        "unverified_assertion_count": count("unverified_assertions"),
        "unverified_document_segment_count": count("unverified_document_segments"),
        "documents_truncated": bool(data.get("documents_truncated", False)),
        "gap_types": [],
    }
    gaps = result["gap_types"]
    if result["coverage_incomplete"]:
        gaps.append("COVERAGE_INCOMPLETE")
    if result["semantic_coverage"] == "unknown":
        gaps.append("SEMANTIC_COVERAGE_UNKNOWN")
    for key, code in [
        ("uninvestigated_file_count", "UNINVESTIGATED_FILES"),
        ("unexplained_entrypoint_count", "UNEXPLAINED_ENTRYPOINTS"),
        ("pending_investigation_count", "PENDING_INVESTIGATION"),
        ("deferred_investigation_count", "DEFERRED_INVESTIGATION"),
        ("unverified_assertion_count", "UNVERIFIED_ASSERTIONS"),
        ("unverified_document_segment_count", "UNVERIFIED_DOCUMENT_SEGMENTS"),
    ]:
        if result[key]:
            gaps.append(code)
    if result["documents_truncated"]:
        gaps.append("DOCUMENTS_TRUNCATED")
    return result
