"""规则只阻断，不以来源真实自动赋予语义通过。人工逐断言确认仍不可省略。"""

import re

from sqlalchemy import select

from app.core.security import AppError
from app.db.index_models import CodeSymbol, Document, DocumentSegment
from app.db.models import Snapshot
from app.db.review_models import ClaimRevision
from app.knowledge.document_structure import ALLOWED as DOCUMENT_ALLOWED
from app.knowledge.document_structure import build_document_units
from app.knowledge.evidence_registry import read_evidence
from app.knowledge.review_schemas import ReviewedContent
from app.retrieval.schemas import RunContext

INVESTIGATION = re.compile(
    r"^(?:检索|调查|核验|追踪|分析|展开|查找|读取|搜索|确认是否|继续调查)|(?:待调查|后续调查|调查计划)|^(?:search|investigate|inspect|trace|read)\b",
    re.I,
)
RISK = re.compile(
    r"持久|原子|幂等|隔离|可配置|任意.{0,12}(?:URL|地址|认证)|跨请求|exactly.once|durab|persist|atomic|idempoten|isolat|configurab",
    re.I,
)
# 文档只能证明其明示范围；这些“扩大化”措辞若不在原文引文中，直接阻断。
DOCUMENT_EXPANSION = re.compile(
    r"所有|全部|任意|零丢失|百分百|100%|强事务|全局一致|绝对|exactly.once|zero.loss",
    re.I,
)
NEGATIVE = re.compile(
    r"未(?:核验|验证|实现|证明)|不(?:保证|证明|代表|承诺)|未知|依赖.{0,30}实现|取决于|unknown|unverified|not guaranteed",
    re.I,
)


def classify(data):
    if INVESTIGATION.search(str(data.get("name", ""))) or INVESTIGATION.search(
        str(data.get("action", ""))
    ):
        return "investigation_action"
    return data.get("declaration_type", "unknown")


def context(db, sid):
    snapshot = db.get(Snapshot, sid)
    if not snapshot:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    return RunContext(
        category_id=snapshot.category_id,
        snapshot_id=sid,
        principal_id="admin",
        policy="admin_staging",
    )


def latest(db, claim_id):
    return db.scalar(
        select(ClaimRevision)
        .where(ClaimRevision.claim_id == claim_id)
        .order_by(ClaimRevision.number.desc())
        .limit(1)
        .execution_options(populate_existing=True, autoflush=False)
    )


def fields(content):
    result = {}
    for key, value in content.items():
        if isinstance(value, list):
            result.update({f"{key}.{i}": text for i, text in enumerate(value)})
        else:
            result[key] = value
    return result


def flatten(value):
    return re.sub(r"\s+", "", value or "")


def document_units(db, ctx, document_id, cache):
    if document_id in cache:
        return cache[document_id]
    document = db.get(Document, document_id)
    if not document or document.snapshot_id != ctx.snapshot_id:
        cache[document_id] = {}
        return cache[document_id]
    segments = list(
        db.scalars(
            select(DocumentSegment)
            .where(DocumentSegment.document_id == document_id)
            .order_by(DocumentSegment.id)
        )
    )
    units = build_document_units(document, segments)
    mapping = {}
    for unit in units:
        for segment_id in unit.segment_ids:
            mapping[segment_id] = unit
    cache[document_id] = mapping
    return mapping


def document_support(db, ctx, item, cache):
    """返回 (unit, error)：文档证据必须落在当前产品能力单元内。"""
    segment_id = item["source_ref"].get("segment_id")
    document_id = item["source_ref"].get("document_id")
    unit = document_units(db, ctx, document_id, cache).get(segment_id)
    if unit is None:
        return None, "DOCUMENT_EVIDENCE_UNIT_UNRESOLVED"
    if not unit.eligible or unit.classification not in DOCUMENT_ALLOWED:
        return unit, "DOCUMENT_EVIDENCE_NOT_PRODUCT_SCOPE"
    if flatten(item["quote"]) not in flatten(unit.text):
        return unit, "DOCUMENT_EVIDENCE_QUOTE_MISMATCH"
    return unit, None


def gate(
    db,
    sid,
    declaration_type,
    content,
    assertions,
    reachability,
    availability_basis=None,
    product_scope_confirmed=False,
):
    content = ReviewedContent.model_validate(content).model_dump()
    ctx = context(db, sid)
    errors = []
    if declaration_type not in {"product_function", "technical_mechanism"}:
        errors.append("DECLARATION_NOT_PUBLISHABLE")
    if classify(content) == "investigation_action":
        errors.append("INVESTIGATION_ACTION_NOT_CAPABILITY")
    expected = fields(content)
    by_field = {a["field"]: a for a in assertions}
    if len(by_field) != len(assertions) or set(by_field) != set(expected):
        errors.append("ASSERTION_FIELDS_INCOMPLETE")
    evidence = {}
    dependency_gaps = []
    document_cache = {}
    document_only_ok = False
    for field, text in expected.items():
        a = by_field.get(field)
        if not a or a["text"] != text:
            errors.append("ASSERTION_TEXT_MISMATCH")
            continue
        if a["semantic_decision"] != "supported":
            errors.append("ASSERTION_SEMANTICS_UNVERIFIED")
        ids = set(a["evidence_ids"]) | set(a.get("implementation_evidence_ids", []))
        for dependency in a.get("dependencies", []):
            ids.update(dependency["evidence_ids"])
        for eid in ids:
            if eid not in evidence:
                evidence[eid] = read_evidence(db, ctx, eid)
        sources = [
            evidence[e]
            for e in a["evidence_ids"]
            if evidence[e]["kind"] == "source" and evidence[e]["source_ref"].get("role") != "test"
        ]
        documents = [evidence[e] for e in a["evidence_ids"] if evidence[e]["kind"] == "document"]
        if documents and not a.get("proof_basis"):
            errors.append("ASSERTION_PROOF_BASIS_REQUIRED")
        if a.get("proof_basis"):
            expected_basis = (
                "mixed"
                if sources and documents
                else ("document" if documents else ("source" if sources else None))
            )
            if expected_basis and a["proof_basis"] != expected_basis:
                errors.append("ASSERTION_PROOF_BASIS_MISMATCH")
        if documents and not a.get("document_scope_confirmed"):
            errors.append("DOCUMENT_SCOPE_CONFIRMATION_REQUIRED")
        if documents and availability_basis not in {"document_declared", "mixed"}:
            errors.append("DOCUMENT_AVAILABILITY_BASIS_REQUIRED")
        if documents and not sources and not product_scope_confirmed:
            errors.append("PRODUCT_SCOPE_CONFIRMATION_REQUIRED")
        if documents and not sources and a.get("document_scope_confirmed"):
            document_only_ok = True
        positive_text = not re.search(r"不支持|不提供|不具备|仅支持|仅限|除外|限制", text, re.I)
        for item in documents:
            unit, problem = document_support(db, ctx, item, document_cache)
            if problem:
                errors.append(problem)
                continue
            if positive_text and unit.classification in {"limitation", "exclusion"}:
                errors.append("DOCUMENT_EVIDENCE_NEGATIVE_SCOPE")
        if documents:
            quotes = " ".join(item.get("quote", "") for item in documents)
            for term in DOCUMENT_EXPANSION.findall(text):
                if term.casefold() not in quotes.casefold():
                    errors.append("DOCUMENT_SCOPE_EXPANSION")
                    break
        if not sources and not documents:
            errors.append("ASSERTION_NEEDS_NONTEST_SOURCE")
        for dependency in a.get("dependencies", []):
            symbol = db.get(CodeSymbol, dependency["symbol_id"])
            if not symbol or symbol.snapshot_id != sid or symbol.file_id != dependency["file_id"]:
                errors.append("DEPENDENCY_SCOPE_INVALID")
                continue
            supports = [evidence[e] for e in dependency["evidence_ids"]]
            if not any(
                e["kind"] == "source"
                and e["source_ref"].get("file_id") == symbol.file_id
                and e["locator"]["start_line"] <= symbol.start_line
                and e["locator"]["end_line"] >= symbol.end_line
                for e in supports
            ):
                dependency_gaps.append(
                    {
                        "field": field,
                        "file_id": symbol.file_id,
                        "symbol_id": symbol.id,
                        "reason": "callee_not_fully_read",
                    }
                )
        # 高风险业务承诺至少需要声明依赖及不同实现文件，人工仍须核对真正底层语义。
        risk_clauses = re.split(r"[，。；;\n]", text)
        risk_promise = any(
            RISK.search(clause)
            and not re.match(
                r"^\s*(?:不保证|不证明|不代表|不承诺|未核验|未验证|未知|依赖|取决于|不含|不提供)",
                clause,
            )
            for clause in risk_clauses
        )
        if risk_promise and documents:
            # 文档明示范围可支持产品级能力，但不得借此推导未记载的底层机制。
            # 双源（document+source）同样以文档明示范围为准，避免把“函数存在”误当底层机制证明。
            document_only_ok = True
            if availability_basis not in {"document_declared", "mixed"}:
                errors.append("HIGH_RISK_DOCUMENT_BASIS_REQUIRED")
            if not product_scope_confirmed:
                errors.append("HIGH_RISK_PRODUCT_SCOPE_UNCONFIRMED")
            if any(
                evidence[e].get("kind") == "document" and not a.get("document_scope_confirmed")
                for e in a["evidence_ids"]
            ):
                errors.append("HIGH_RISK_DOCUMENT_SCOPE_UNCONFIRMED")
        elif risk_promise:
            implementation = [
                evidence[e]
                for e in a.get("implementation_evidence_ids", [])
                if evidence[e]["kind"] == "source"
                and evidence[e]["source_ref"].get("role") != "test"
            ]
            if not a.get("dependencies") or not implementation:
                errors.append("HIGH_RISK_IMPLEMENTATION_GAP")
                dependency_gaps.append({"field": field, "reason": "high_risk_unclosed"})
            elif not any(
                e["source_ref"]["file_id"] not in {s["source_ref"]["file_id"] for s in sources}
                for e in implementation
            ):
                errors.append("HIGH_RISK_NEEDS_IMPLEMENTATION_SOURCE")
            for implementation_source in implementation:
                code = implementation_source["quote"]
                called = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", code))
                definitions = set(re.findall(r"def\s+([A-Za-z_]\w*)", code))
                builtins = {
                    "str",
                    "int",
                    "len",
                    "dict",
                    "list",
                    "set",
                    "tuple",
                    "bool",
                    "range",
                    "ValueError",
                    "KeyError",
                }
                for name in called - definitions - builtins:
                    candidates = list(
                        db.scalars(
                            select(CodeSymbol)
                            .where(CodeSymbol.snapshot_id == sid, CodeSymbol.qualified_name == name)
                            .limit(20)
                        )
                    )
                    if not any(
                        any(
                            e["kind"] == "source"
                            and e["source_ref"].get("file_id") == symbol.file_id
                            and e["locator"]["start_line"] <= symbol.start_line
                            and e["locator"]["end_line"] >= symbol.end_line
                            for e in implementation
                        )
                        for symbol in candidates
                    ):
                        dependency_gaps.append(
                            {
                                "field": field,
                                "symbol": name,
                                "reason": "bottom_implementation_unresolved",
                            }
                        )
        # 对被引用的实际调用名，如果正文据其行为作断言，必须提供被调方定位。
        quotes = "\n".join(e["quote"] for e in sources)
        calls = set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", text))
        for name in calls:
            if re.search(r"\b" + re.escape(name) + r"\s*\(", quotes):
                symbols = list(
                    db.scalars(
                        select(CodeSymbol)
                        .where(CodeSymbol.snapshot_id == sid, CodeSymbol.qualified_name == name)
                        .limit(20)
                    )
                )
                source_files = {s["source_ref"]["file_id"] for s in sources}
                external = [s for s in symbols if s.file_id not in source_files]
                covered = {d["symbol_id"] for d in a.get("dependencies", [])}
                if (
                    external
                    and not covered.intersection(s.id for s in external)
                    and not NEGATIVE.search(text)
                ):
                    dependency_gaps.append(
                        {"field": field, "symbol": name, "reason": "called_implementation_missing"}
                    )
    if dependency_gaps:
        errors.append("DEPENDENCY_EVIDENCE_GAP")
    if declaration_type == "product_function":
        reachable = [read_evidence(db, ctx, eid) for eid in reachability]
        wired = any(
            e["kind"] == "source"
            and e["source_ref"].get("role") != "test"
            and re.search(
                r"(?:add_route|register|subscribe|\.route|\.HandleFunc|callback|install|bootstrap)",
                e["quote"],
                re.I,
            )
            for e in reachable
        )
        if not wired:
            # 文档单源不要求伪造运行时接线；但必须由管理员显式确认产品范围。
            document_route = (
                document_only_ok
                and availability_basis == "document_declared"
                and product_scope_confirmed
                and all(
                    a.get("document_scope_confirmed")
                    for a in assertions
                    if any(evidence[e]["kind"] == "document" for e in a["evidence_ids"])
                )
            )
            if not document_route:
                errors.append("RUNTIME_WIRING_EVIDENCE_REQUIRED")
    return {
        "eligible_for_human_acceptance": not errors,
        "errors": sorted(set(errors)),
        "dependency_gaps": dependency_gaps,
        "source_verified_is_not_semantic_verified": True,
        "semantic_verification": "human_review_required",
        "evidence_ids": sorted(evidence),
        "reachability_evidence_ids": reachability,
        "availability_basis": availability_basis,
        "product_scope_confirmed": product_scope_confirmed,
        "document_evidence_used": any(e["kind"] == "document" for e in evidence.values()),
    }
