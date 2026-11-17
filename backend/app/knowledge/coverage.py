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
