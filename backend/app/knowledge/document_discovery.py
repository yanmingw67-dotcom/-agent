"""P1.6 文档独立能力发现。

只做“文档声明了什么”的候选发现与来源登记：
- 结构优先，预算受限，结果确定性可复现；
- 不因源码未命中而否定文档，也不因文档命中而推导未记载的底层机制；
- 不批准、不发布，不把证明材料/规划/商务内容当作当前能力。
"""

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Document, DocumentSegment, Module
from app.db.knowledge_models import (
    CapabilityClaim,
    ClaimEvidenceLink,
    DocumentDiscoveryRun,
    Evidence,
    Investigation,
)
from app.db.models import AuditEvent, KnowledgeJob, Snapshot, now
from app.db.session import session_factory
from app.knowledge.classification import classification as trusted_classification
from app.knowledge.document_semantics import (
    SemanticUnit,
    extract_semantics,
)
from app.knowledge.document_structure import ALLOWED, build_units, load_documents
from app.knowledge.evidence_registry import register
from app.knowledge.schemas import EvidenceRequest
from app.retrieval.schemas import RunContext

MODE_MODEL = {"model", "hybrid"}
CLASSIFICATION_TO_CLAIM = {
    "current_capability": "current_capability",
    "deployment": "current_capability",
    "configuration": "current_capability",
    "performance_metric": "current_capability",
    "technical_implementation": "technical_implementation",
    "support_scope": "support_scope",
    "limitation": "limitation",
    "exclusion": "exclusion",
}
ACTION = {
    "deployment": "提供部署接入能力",
    "configuration": "提供配置能力",
    "performance_metric": "提供性能指标",
    "technical_implementation": "采用技术机制",
    "limitation": "存在使用限制",
    "exclusion": "明确不支持",
}
LOW_VALUE = re.compile(r"^(?:目录|前言|概述|简介|说明|附录|版本|更新记录)$")


@dataclass
class Candidate:
    claim_id: str
    local_key: str
    document: Document
    unit: object
    name: str
    category: str
    claim_type: str
    evidence_ids: list[str]
    anchors: list[dict] | None = None
    semantic: dict | None = None

    def evidence_segment_ids(self):
        if self.anchors:
            return [item["segment_id"] for item in self.anchors][:10]
        return list(self.unit.segment_ids)[:10]

    def draft(self):
        unit = self.unit
        if self.semantic:
            data = {
                **self.semantic,
                "key": self.local_key,
                "name": self.name[:1000],
                "category": self.category[:1000],
                "evidence_ids": self.evidence_ids,
                "unresolved_questions": list(
                    dict.fromkeys(
                        self.semantic.get("unresolved_questions", [])
                        + [
                            "模型仅提取候选，未批准、未发布；须管理员逐条核对文档语义",
                            "底层实现机制需由源码或其他受控来源单独核验",
                        ]
                    )
                )[:30],
                "status": "evidence_found",
                "source_channel": "document",
                "claim_type": self.claim_type,
                "discovery_unit": {
                    "key": unit.key,
                    "kind": "semantic",
                    "locator": unit.locator,
                    "classification": unit.classification,
                    "classification_reasons": unit.reasons,
                    "headers": getattr(unit, "headers", {}),
                    "merged_context": getattr(unit, "merged_context", {}),
                    "segment_ids": self.evidence_segment_ids(),
                    "document_id": unit.document_id,
                    "filename": unit.filename,
                },
            }
            return data
        return {
            "key": self.local_key,
            "name": self.name[:1000],
            "category": self.category[:1000],
            "action": ACTION.get(unit.classification, "文档声明能力"),
            "target": self.name[:1000],
            "business_goal": "文档未单独记录业务目标，须管理员结合产品上下文核对",
            "implementation_mechanism": "文档仅声明能力，未记录底层实现机制；不得据此推导实现细节",
            "supported_scope": [],
            "conditions": [],
            "limitations": [unit.text[:1000]] if unit.classification == "limitation" else [],
            "evidence_ids": self.evidence_ids,
            "unresolved_questions": [
                "底层实现机制需由源码或其他受控来源单独核验",
                "文档声明不等同于运行时可验证能力",
            ],
            "status": "evidence_found",
            "source_channel": "document",
            "claim_type": self.claim_type,
            "discovery_unit": {
                "key": unit.key,
                "kind": unit.kind,
                "locator": unit.locator,
                "classification": unit.classification,
                "classification_reasons": unit.reasons,
                "headers": unit.headers,
                "merged_context": unit.merged_context,
                "segment_ids": unit.segment_ids,
                "document_id": unit.document_id,
                "filename": unit.filename,
            },
        }

    def origin(self, run_id):
        origin = {
            "source_channel": "document",
            "discovery_run_id": run_id,
            "document_id": self.document.id,
            "document_sha256": self.document.sha256,
            "parser_version": self.document.parser_version,
            "unit_key": self.unit.key,
            "locator": self.unit.locator,
            "classification": self.unit.classification,
            "classification_reasons": self.unit.reasons,
        }
        if self.semantic:
            origin["extraction"] = {
                "model_mode": self.semantic.get("model_mode"),
                "anchors": [
                    {
                        "segment_id": anchor["segment_id"],
                        "locator": anchor["locator"],
                    }
                    for anchor in self.anchors or []
                ],
            }
        return origin


def normalized(text):
    return re.sub(r"\s+", "", text or "").strip("；;。-•")


def claim_id_for(snapshot_id, document_id, unit_key):
    raw = f"{snapshot_id}|{document_id}|{unit_key}".encode()
    return "claim-doc-" + hashlib.sha256(raw).hexdigest()[:28]


def local_key_for(unit_key):
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", unit_key).strip("-")
    if len(value) > 72:
        value = value[:40] + "-" + hashlib.sha256(unit_key.encode()).hexdigest()[:16]
    return value or "document-unit"


def candidate_name(unit):
    for part in re.split(r"[；;]", unit.text):
        match = re.match(
            r"^(?:功能名称|能力名称|功能|能力|名称|项目)\s*[:：]\s*(.+)$", part.strip()
        )
        if match:
            return match.group(1).strip()[:200]
    first = re.split(r"[；;]", unit.text)[0].strip()
    return (first or unit.text).strip()[:200]


def candidate_category(unit):
    return (unit.sheet or unit.section or "技术文档").strip()[:200]


def scope_context(run):
    return RunContext(
        category_id=run.category_id,
        snapshot_id=run.snapshot_id,
        principal_id="admin",
        policy="admin_staging",
    )


def semantic_claim_id(snapshot_id, document_id, segment_ids, name):
    raw = "|".join(
        [snapshot_id, document_id, ",".join(sorted(segment_ids)), normalized(name)]
    ).encode()
    return "claim-doc-" + hashlib.sha256(raw).hexdigest()[:28]


@dataclass
class Prepared:
    candidates: list
    metrics: dict
    coverage: dict
    gaps: list
    units: list
    documents: dict
    semantic_units: list


def model_state(run, snapshot, mode, classification, model=None):
    settings = get_settings()
    if mode == "structural":
        return "NOT_USED", None
    allowed = (classification == "synthetic_public" and settings.builder_allow_synthetic) or (
        classification != "synthetic_public" and settings.builder_allow_enterprise
    )
    if not allowed:
        return "UNAVAILABLE", "DOCUMENT_DISCOVERY_MODEL_NOT_AUTHORIZED"
    if model is not None:
        label = (
            "TEST_FUNCTION_MODEL" if type(model).__name__ == "FunctionModel" else "INJECTED_MODEL"
        )
        return label, None
    configured = bool(
        settings.builder_model
        and settings.builder_base_url
        and settings.builder_api_key.get_secret_value()
    )
    if not configured:
        return "UNAVAILABLE", "DOCUMENT_DISCOVERY_MODEL_NOT_CONFIGURED"
    # 真实端点只有在校验分类与显式授权后才会被 configured_model 构造并调用。
    return "NOT_INVOKED", "DOCUMENT_DISCOVERY_MODEL_NOT_INVOKED"


def semantic_unit(unit, segment_texts=None):
    texts = {
        segment_id: segment_texts[segment_id]
        for segment_id in unit.segment_ids
        if segment_texts and segment_id in segment_texts
    }
    return SemanticUnit(
        key=unit.key,
        document_id=unit.document_id,
        filename=unit.filename,
        segment_ids=list(unit.segment_ids),
        locator=dict(unit.locator),
        text=unit.text,
        classification=unit.classification,
        section=getattr(unit, "section", ""),
        sheet=getattr(unit, "sheet", None),
        headers=getattr(unit, "headers", {}),
        merged_context=getattr(unit, "merged_context", {}),
        reasons=list(getattr(unit, "reasons", [])),
        segment_texts=texts,
    )


def candidate_from_semantic(item, run, documents):
    unit = item.unit
    document = documents[unit.document_id]
    claim_type = CLASSIFICATION_TO_CLAIM.get(item.claim_type, item.claim_type)
    anchors = list(item.anchors)
    claim_id = semantic_claim_id(
        run.snapshot_id, document.id, [a["segment_id"] for a in anchors], item.name
    )
    payload = {
        "action": item.action[:1000],
        "target": item.target[:1000],
        "business_goal": item.business_goal[:1000],
        "implementation_mechanism": item.implementation_mechanism[:3000],
        "supported_scope": item.supported_scope[:6],
        "conditions": item.conditions[:6],
        "limitations": item.limitations[:6],
        "model_mode": item.model_mode,
        "anchors": anchors,
        "quote": item.quote[:1000],
    }
    return Candidate(
        claim_id=claim_id,
        local_key=local_key_for(f"{unit.key}-semantic-{claim_id[-12:]}"),
        document=document,
        unit=unit,
        name=item.name,
        category=(unit.sheet or unit.section or "技术文档").strip()[:200],
        claim_type=claim_type,
        evidence_ids=[],
        anchors=anchors,
        semantic=payload,
    )


def merge_candidates(structural, semantic, limit):
    merged = list(structural)
    by_name = {normalized(item.name): index for index, item in enumerate(merged)}
    duplicates = 0
    for candidate in semantic:
        key = normalized(candidate.name)
        if key and key in by_name:
            merged[by_name[key]] = candidate
            duplicates += 1
        else:
            by_name[key] = len(merged)
            merged.append(candidate)
    return merged[:limit], duplicates


def prepare_candidates(db, run, limit):
    settings = get_settings()
    context = scope_context(run)
    units, budget = build_units(
        db,
        context,
        max_documents=settings.document_discovery_max_documents,
        max_units=settings.document_discovery_max_units,
    )
    documents = {doc.id: doc for doc in load_documents(db, context)}
    candidates = []
    gaps = []
    per_document = []
    seen = {}
    duplicates = 0
    skipped = 0
    eligible_units = []
    for unit in units:
        document = documents.get(unit.document_id)
        if document is None:
            continue
        if not unit.eligible or unit.classification not in ALLOWED:
            skipped += 1
            continue
        key = normalized(unit.text)
        if len(key) < 4 or LOW_VALUE.match(key):
            skipped += 1
            continue
        if key in seen:
            duplicates += 1
            continue
        seen[key] = unit.key
        eligible_units.append(unit)
    for unit in eligible_units:
        if len(candidates) >= limit:
            gaps.append({"code": "CANDIDATE_BUDGET_EXHAUSTED", "limit": limit})
            break
        document = documents[unit.document_id]
        candidates.append(
            Candidate(
                claim_id=claim_id_for(run.snapshot_id, document.id, unit.key),
                local_key=local_key_for(unit.key),
                document=document,
                unit=unit,
                name=candidate_name(unit),
                category=candidate_category(unit),
                claim_type=CLASSIFICATION_TO_CLAIM[unit.classification],
                evidence_ids=[],
            )
        )
    for document in documents.values():
        doc_units = [u for u in units if u.document_id == document.id]
        doc_gaps = [
            {"unit_key": u.key, "reason": u.reasons, "ocr_required": u.ocr_required}
            for u in doc_units
            if u.kind == "ocr_gap"
        ]
        if document.parse_status in {"partial", "ocr_required", "failed"}:
            doc_gaps.append({"code": "DOCUMENT_PARTIAL", "status": document.parse_status})
        per_document.append(
            {
                "document_id": document.id,
                "filename": document.filename,
                "parse_status": document.parse_status,
                "units": len(doc_units),
                "eligible_units": sum(1 for u in doc_units if u.eligible),
                "gaps": doc_gaps,
            }
        )
        gaps.extend(doc_gaps)
    if budget["truncated"]:
        gaps.append(
            {
                "code": "DOCUMENT_OR_UNIT_BUDGET_EXHAUSTED",
                "documents_considered": budget["documents_considered"],
                "documents_total": budget["documents_total"],
            }
        )
    metrics = {
        "documents_total": budget["documents_total"],
        "documents_considered": budget["documents_considered"],
        "units_total": len(units),
        "units_eligible": len(eligible_units),
        "units_skipped": skipped,
        "duplicates_suppressed": duplicates,
        "candidates": len(candidates),
        "budget": {
            "max_documents": settings.document_discovery_max_documents,
            "max_units": settings.document_discovery_max_units,
            "max_candidates": settings.document_discovery_max_candidates,
        },
        "documents": per_document,
    }
    coverage = {
        "documents_total": budget["documents_total"],
        "documents_considered": budget["documents_considered"],
        "gaps": gaps[:200],
        "gaps_truncated": len(gaps) > 200,
        "ocr_gap_count": sum(1 for g in gaps if g.get("ocr_required")),
        "unclassified_units": sum(1 for u in units if u.classification == "unclassified"),
    }
    segment_ids = {segment_id for unit in eligible_units for segment_id in unit.segment_ids}
    segment_texts = {}
    if segment_ids:
        segment_texts = {
            segment.id: segment.text
            for segment in db.scalars(
                select(DocumentSegment).where(
                    DocumentSegment.snapshot_id == run.snapshot_id,
                    DocumentSegment.id.in_(segment_ids),
                )
            )
        }
    return Prepared(
        candidates=candidates,
        metrics=metrics,
        coverage=coverage,
        gaps=gaps,
        units=units,
        documents=documents,
        semantic_units=[semantic_unit(unit, segment_texts) for unit in eligible_units],
    )


def ensure_document_module(db, run, document):
    name = ("__document__:" + document.id)[:200]
    module = db.scalar(
        select(Module).where(Module.snapshot_id == run.snapshot_id, Module.name == name)
    )
    if not module:
        module = Module(
            snapshot_id=run.snapshot_id,
            name=name,
            path_globs=[],
            summary="文档独立发现作用域；非源码模块",
            analysis_status="document",
        )
        db.add(module)
        db.flush()
    investigation = db.scalar(
        select(Investigation).where(
            Investigation.job_id == run.job_id, Investigation.module_id == module.id
        )
    )
    if not investigation:
        investigation = Investigation(
            job_id=run.job_id,
            snapshot_id=run.snapshot_id,
            module_id=module.id,
            status="succeeded",
            metrics_json={"source_channel": "document", "document_id": document.id},
        )
        db.add(investigation)
        db.flush()
    return investigation


def persist_candidates(db, run, candidates):
    context = scope_context(run)
    created = {"candidates": 0, "evidence": 0, "existing": 0}
    by_document = {}
    for candidate in candidates:
        evidence_ids = []
        anchors = {item["segment_id"]: item.get("quote", "") for item in candidate.anchors or []}
        for segment_id in candidate.evidence_segment_ids():
            segment = db.get(DocumentSegment, segment_id)
            if not segment or segment.sensitive:
                continue
            try:
                receipt = register(
                    db,
                    context,
                    EvidenceRequest(
                        kind="document",
                        segment_id=segment.id,
                        expected_sha256=segment.text_hash,
                        supported_statement=(anchors.get(segment.id) or candidate.unit.text)[:1000],
                    ),
                )
            except AppError:
                # 单个分段不可回读时不伪造证据；该单元不计入可审核候选。
                continue
            evidence_ids.append(receipt.evidence_id)
            if db.get(Evidence, receipt.evidence_id) is not None:
                created["evidence"] += 1
        if not evidence_ids:
            continue
        candidate.evidence_ids = evidence_ids
        existing = db.get(CapabilityClaim, candidate.claim_id)
        if existing:
            created["existing"] += 1
            continue
        investigation = by_document.get(candidate.document.id)
        if investigation is None:
            investigation = ensure_document_module(db, run, candidate.document)
            by_document[candidate.document.id] = investigation
        db.add(
            CapabilityClaim(
                id=candidate.claim_id,
                snapshot_id=run.snapshot_id,
                investigation_id=investigation.id,
                local_key=candidate.local_key,
                name=candidate.name[:1000],
                status="evidence_found",
                draft_json=candidate.draft(),
                source_channel="document",
                origin_json=candidate.origin(run.id),
            )
        )
        for eid in evidence_ids:
            db.add(
                ClaimEvidenceLink(
                    claim_id=candidate.claim_id,
                    evidence_id=eid,
                    support_type="document_declaration",
                    supported_statement=candidate.unit.text[:1000],
                )
            )
        created["candidates"] += 1
    db.flush()
    from app.knowledge.conflicts import scan

    created["conflicts"] = len(scan(db, run.snapshot_id, run.category_id))
    return created


def record_failure(run_id, code, job_id=None, detail=None):
    with session_factory().begin() as db:
        run = db.scalar(
            select(DocumentDiscoveryRun).where(DocumentDiscoveryRun.id == run_id).with_for_update()
        )
        if not run:
            return
        run.status = "failed"
        run.error_code = code
        run.finished_at = now()
        run.coverage_json = {**(run.coverage_json or {}), **(detail or {})}
        if job_id:
            job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
            if job:
                job.status = "failed"
                job.stage = "failed"
                job.error_code = code
                job.finished_at = now()
                job.metrics_json = {**(job.metrics_json or {}), **run.metrics_json}


def run_document_discovery(job_id, token, model=None):
    """Worker 入口：先收集，后单事务落库；失败不留下半成品候选。"""
    settings = get_settings()
    started = time.monotonic()
    with session_factory().begin() as db:
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if not job or job.status != "queued" or job.dispatch_token != token:
            return
        job.status, job.started_at = "running", now()
        run = db.scalar(
            select(DocumentDiscoveryRun)
            .where(DocumentDiscoveryRun.job_id == job.id)
            .with_for_update()
        )
        if not run:
            job.status, job.stage, job.error_code = "failed", "failed", "DISCOVERY_RUN_NOT_FOUND"
            job.finished_at = now()
            return
        snapshot = db.get(Snapshot, run.snapshot_id)
        run.status, run.started_at = "running", now()
        mode = run.mode
        run_id = run.id
    with session_factory()() as db:
        resource_classification = trusted_classification(db, run.snapshot_id)
    model_mode, model_error = model_state(run, snapshot, mode, resource_classification, model)
    # 未授权时即使调用方注入了测试模型也不得越过外发授权检查。
    actual_model = None if model_error else model
    if actual_model is None and not model_error and mode in {"model", "hybrid"}:
        try:
            from app.agents.runtime import configured_model

            actual_model = configured_model(resource_classification)
            model_mode = "REAL"
            model_error = None
        except AppError as exc:
            model_error = exc.error_code
    if mode == "model" and actual_model is None:
        record_failure(
            run.id,
            model_error or "DOCUMENT_DISCOVERY_MODEL_UNAVAILABLE",
            job_id,
            {"model_mode": model_mode},
        )
        return
    with session_factory()() as db:
        prepared = prepare_candidates(db, run, settings.document_discovery_max_candidates)
    candidates = prepared.candidates
    metrics = prepared.metrics
    coverage = prepared.coverage
    gaps = prepared.gaps
    if model_error:
        coverage["gaps"] = list(coverage["gaps"]) + [{"code": model_error}]
        coverage["gaps_truncated"] = len(coverage["gaps"]) > 200
    if actual_model is not None and mode in {"model", "hybrid"}:
        runner = asyncio.Runner()
        try:
            extraction = runner.run(
                extract_semantics(
                    prepared.semantic_units,
                    actual_model,
                    run.id,
                    job_id,
                    injected=model is not None,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 语义层错误必须可审计
            extraction = None
            model_error = "DOCUMENT_DISCOVERY_MODEL_ERROR"
            gaps.append(
                {
                    "code": model_error,
                    "exception_type": type(exc).__name__,
                }
            )
        finally:
            runner.close()
        if extraction is not None:
            semantic_candidates = [
                candidate_from_semantic(item, run, prepared.documents)
                for item in extraction.candidates
            ]
            candidates, replaced = merge_candidates(
                candidates,
                semantic_candidates,
                settings.document_discovery_max_candidates,
            )
            metrics["semantic"] = extraction.metrics
            metrics["semantic_merged"] = replaced
            metrics["semantic_candidates"] = len(semantic_candidates)
            model_mode = extraction.model_mode
            gaps.extend(extraction.gaps[:200])
            model_failures = [
                gap
                for gap in extraction.gaps
                if gap.get("code")
                in {
                    "DOCUMENT_DISCOVERY_MODEL_ERROR",
                    "DOCUMENT_DISCOVERY_MODEL_BUDGET_EXCEEDED",
                    "DOCUMENT_DISCOVERY_CONTEXT_EXCEEDED",
                }
            ]
            if model_failures:
                model_error = model_failures[0]["code"]
            coverage["gaps"] = list(coverage["gaps"]) + extraction.gaps[:200]
            coverage["gaps_truncated"] = len(coverage["gaps"]) > 200
    if mode == "model" and model_error:
        # 模型模式失败不得落下纯结构候选，避免把降级结果误当模型能力。
        record_failure(run.id, model_error, job_id, {"coverage": coverage, "metrics": metrics})
        return
    metrics["candidates"] = len(candidates)
    metrics["model_mode"] = model_mode
    status = "partial" if coverage["gaps"] else "succeeded"
    error = None
    created = {"candidates": 0, "evidence": 0, "existing": 0, "conflicts": 0}
    if candidates:
        try:
            with session_factory().begin() as db:
                run = db.scalar(
                    select(DocumentDiscoveryRun)
                    .where(DocumentDiscoveryRun.id == run.id)
                    .with_for_update()
                )
                job = db.scalar(
                    select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update()
                )
                if job.status != "running" or job.dispatch_token != token:
                    return
                created = persist_candidates(db, run, candidates)
                run.status = status
                run.error_code = model_error if mode == "model" else None
                run.metrics_json = {
                    **metrics,
                    **created,
                    "model_mode": model_mode,
                    "mode": mode,
                    "truncated": bool(coverage["gaps"]),
                }
                run.coverage_json = coverage
                run.finished_at = now()
                job.status = "partial" if run.status == "partial" else "succeeded"
                job.stage = "documents_discovered"
                job.error_code = run.error_code
                job.metrics_json = {
                    **run.metrics_json,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
                job.finished_at = now()
                db.add(
                    AuditEvent(
                        principal_id="admin",
                        action="documents.discovery.completed",
                        resource_id=run.id,
                        correlation_id=job.id,
                        details_json={
                            "status": run.status,
                            "candidates": created["candidates"],
                            "gaps": len(coverage["gaps"]),
                            "model_mode": model_mode,
                        },
                    )
                )
        except Exception as exc:
            error = "DOCUMENT_DISCOVERY_INTERNAL_ERROR"
            error_detail = {"exception_type": type(exc).__name__}
    if error:
        record_failure(run_id, error, job_id, error_detail)
        return
    if not candidates:
        with session_factory().begin() as db:
            run = db.scalar(
                select(DocumentDiscoveryRun)
                .where(DocumentDiscoveryRun.id == run.id)
                .with_for_update()
            )
            job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
            if job and job.dispatch_token == token and job.status == "running":
                run.status = status
                run.metrics_json = {**metrics, "model_mode": model_mode, "mode": mode}
                run.coverage_json = coverage
                run.finished_at = now()
                if mode == "model" and model_error:
                    run.status, run.error_code = "failed", model_error
                job.status = "failed" if run.status == "failed" else run.status
                job.stage, job.error_code = "documents_discovered", run.error_code
                job.metrics_json = run.metrics_json
                job.finished_at = now()


def create_run(db, snapshot, job):
    settings = get_settings()
    mode = settings.document_discovery_mode
    run = DocumentDiscoveryRun(
        category_id=snapshot.category_id,
        snapshot_id=snapshot.id,
        job_id=job.id,
        mode=mode,
        status="pending",
        metrics_json={
            "mode": mode,
            "model_mode": "NOT_USED" if mode == "structural" else "PENDING",
            "budget": {
                "max_documents": settings.document_discovery_max_documents,
                "max_units": settings.document_discovery_max_units,
                "max_candidates": settings.document_discovery_max_candidates,
            },
        },
        coverage_json={},
    )
    db.add(run)
    db.flush()
    return run
