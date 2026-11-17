"""验证模型草稿及原子保存；不生产approved/verified业务事实。"""

from sqlalchemy import select

from app.core.security import AppError
from app.db.index_models import Entrypoint
from app.db.knowledge_models import (
    CapabilityClaim,
    CapabilityRelation,
    ClaimEvidenceLink,
    Investigation,
)
from app.db.models import KnowledgeJob, Snapshot
from app.ingestion.text import digest, sensitive_text
from app.knowledge.evidence_registry import read_evidence
from app.knowledge.schemas import ModuleInvestigationOutput


def fence(db, run_id, token):
    run = db.scalar(select(Investigation).where(Investigation.id == run_id).with_for_update())
    job = db.get(KnowledgeJob, run.job_id)
    snapshot = db.get(Snapshot, run.snapshot_id)
    if job.status != "running" or job.dispatch_token != token or snapshot.status == "published":
        raise AppError(409, "INVESTIGATION_STALE", "调查租约失效或快照已发布")
    return run


def validate_output(db, context, run, output: ModuleInvestigationOutput):
    if output.module_id != run.module_id:
        raise AppError(422, "MODULE_SCOPE_MISMATCH", "输出模块不是服务端指定模块")
    ids = set(e for c in output.capabilities for e in c.evidence_ids)
    ids.update(e for r in output.candidate_relations for e in r.evidence_ids)
    if not ids.issubset(set(run.evidence_ids)):
        raise AppError(422, "UNKNOWN_EVIDENCE_ID", "证据ID不是该模块调查登记所得")
    verified = {eid: read_evidence(db, context, eid) for eid in ids}
    if sensitive_text(output.model_dump_json()):
        raise AppError(422, "SENSITIVE_OUTPUT_RESTRICTED", "模型草稿包含疑似敏感内容，需人工处理")
    output = output.model_copy(deep=True)
    for claim in output.capabilities:
        claim.evidence_ids = list(dict.fromkeys(claim.evidence_ids))
        claim.status = "evidence_found" if claim.evidence_ids else "candidate"
        cautions = ["语义支持与产品可达性未经人工核验；来源哈希核验不等于能力已验证"]
        if not claim.evidence_ids:
            cautions.append("尚无已登记证据，不得据此判定产品不支持")
        supports = [verified[eid] for eid in claim.evidence_ids]
        source = [e for e in supports if e["kind"] == "source"]
        if supports and not source:
            claim.status = "candidate"
            cautions.append("document_claim_unverified：仅文档主张，无源码实现证据")
        if source and all(e["source_ref"].get("role") == "test" for e in source):
            claim.status = "candidate"
            cautions.append("test_only_not_product_capability")
        claim.unresolved_questions = list(dict.fromkeys(cautions + claim.unresolved_questions))[:30]
    actual = set(
        db.scalars(
            select(Entrypoint.id).where(
                Entrypoint.snapshot_id == context.snapshot_id, Entrypoint.module_id == run.module_id
            )
        )
    )
    if not set(output.unexplained_entrypoint_ids).issubset(actual):
        raise AppError(422, "ENTRYPOINT_SCOPE_MISMATCH", "入口不属于该模块")
    # 本批不把语法调用候选自动解释成已覆盖入口。
    output.unexplained_entrypoint_ids = sorted(actual)[:30]
    if len(actual) > 30:
        output.open_questions = ["入口超过本次输出预算；仍有未调查入口"] + output.open_questions[
            :29
        ]
    return output


def save_output(db, context, run_id, token, output):
    run = fence(db, run_id, token)
    output = validate_output(db, context, run, output)
    claim_ids = {}
    for c in output.capabilities:
        cid = "cap-" + digest(f"{run.id}:{c.key}".encode())
        claim_ids[c.key] = cid
        db.add(
            CapabilityClaim(
                id=cid,
                snapshot_id=run.snapshot_id,
                investigation_id=run.id,
                local_key=c.key,
                name=c.name,
                status=c.status,
                draft_json=c.model_dump(),
            )
        )
    db.flush()
    for c in output.capabilities:
        for eid in c.evidence_ids:
            db.add(
                ClaimEvidenceLink(
                    claim_id=claim_ids[c.key],
                    evidence_id=eid,
                    supported_statement=c.action,
                    support_type="candidate_support",
                )
            )
    for i, relation in enumerate(output.candidate_relations):
        db.add(
            CapabilityRelation(
                id="rel-" + digest(f"{run.id}:{i}".encode()),
                snapshot_id=run.snapshot_id,
                investigation_id=run.id,
                source_claim_id=claim_ids[relation.source_key],
                target_claim_id=claim_ids[relation.target_key],
                relation_type=relation.relation_type,
                status="hypothesis",
                details_json=relation.model_dump(),
            )
        )
    run.output_json = {
        "output": output.model_dump(),
        "claim_ids": claim_ids,
        "semantic_verification": "unverified",
        "formal_compliance_decision": None,
    }
    run.status = "succeeded"
    run.error_code = None
    return run.output_json
