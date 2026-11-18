"""P1.6 文档独立发现：结构、噪声过滤、预算与证据来源。"""

from sqlalchemy import select

from app.db.index_models import Document, Module
from app.db.knowledge_models import CapabilityClaim, DocumentDiscoveryRun, Investigation
from app.db.models import KnowledgeJob
from app.db.session import session_factory
from app.ingestion.indexer import run_index
from app.ingestion.text import digest
from app.knowledge.document_discovery import run_document_discovery
from tests import p16_assets
from tests.conftest import ADMIN
from tests.integration.test_p12_index_retrieval import indexed


def upload_document(client, cid, sid, name, data, mime):
    response = client.post(
        f"/api/v1/categories/{cid}/documents",
        headers=ADMIN,
        data={"snapshot_id": sid},
        files={"file": (name, data, mime)},
    )
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def index_document(job_id):
    with session_factory().begin() as db:
        db.get(KnowledgeJob, job_id).dispatch_token = "p16-doc"
    run_index(job_id, "p16-doc")


def discover(client, sid):
    response = client.post(f"/api/v1/snapshots/{sid}/discover-documents", headers=ADMIN)
    assert response.status_code == 202, response.text
    body = response.json()
    with session_factory().begin() as db:
        db.get(KnowledgeJob, body["job_id"]).dispatch_token = "p16-discovery"
    run_document_discovery(body["job_id"], "p16-discovery")
    return body


def setup(client):
    from uuid import uuid4

    cid, sid = indexed(client, code="p16-" + uuid4().hex[:8], data=p16_assets.source_archive())
    for name, data, mime in [
        (
            "manual.docx",
            p16_assets.docx_manual(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "matrix.xlsx",
            p16_assets.xlsx_capabilities(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ]:
        index_document(upload_document(client, cid, sid, name, data, mime))
    return cid, sid


def test_document_discovery_creates_evidence_backed_candidates(client):
    cid, sid = setup(client)
    body = discover(client, sid)
    with session_factory()() as db:
        job = db.get(KnowledgeJob, body["job_id"])
        run = db.scalar(
            select(DocumentDiscoveryRun).where(DocumentDiscoveryRun.id == body["run_id"])
        )
        claims = list(
            db.scalars(
                select(CapabilityClaim).where(
                    CapabilityClaim.snapshot_id == sid,
                    CapabilityClaim.source_channel == "document",
                )
            )
        )
    assert job.status in {"succeeded", "partial"}
    assert run.status == job.status
    assert claims
    assert run.metrics_json["documents_total"] == 2
    for claim in claims:
        assert claim.draft_json["source_channel"] == "document"
        assert claim.draft_json["evidence_ids"]
        assert claim.origin_json["document_id"]
    names = {c.draft_json["name"] for c in claims}
    assert "多租户策略隔离" in names
    assert "智能编排" not in names
    assert "语义检索" not in names
    assert all("需提供" not in c.draft_json["name"] for c in claims)


def draft_claim(sid, name, claim_type="current_capability"):
    with session_factory()() as db:
        return db.scalar(
            select(CapabilityClaim).where(
                CapabilityClaim.snapshot_id == sid,
                CapabilityClaim.source_channel == "document",
                CapabilityClaim.name == name,
                CapabilityClaim.draft_json["claim_type"].as_string() == claim_type,
            )
        )


def document_review(claim, name="多租户策略隔离"):
    from app.knowledge.quality import fields

    evidence_id = claim.draft_json["evidence_ids"][0]
    content = {
        "name": name,
        "action": "提供租户策略隔离",
        "target": "租户策略",
        "business_goal": "使不同租户的策略配置相互独立",
        "implementation_mechanism": "文档声明租户策略独立，但未记录底层实现机制",
        "supported_scope": ["文档所述当前产品版本"],
        "conditions": [],
        "limitations": ["文档未记录底层实现与运行时接线，不能推导隔离强度"],
    }
    return {
        "action": "accept",
        "expected_revision": 0,
        "declaration_type": "product_function",
        "content": content,
        "assertions": [
            {
                "field": key,
                "text": value,
                "evidence_ids": [evidence_id],
                "semantic_decision": "supported",
                "rationale": "逐字核对文档表格的产品能力行，仅限文档声明范围",
                "proof_basis": "document",
                "document_scope_confirmed": True,
            }
            for key, value in fields(content).items()
        ],
        "comment": "人工确认文档单源声明，不推导底层机制",
        "human_confirmed": True,
        "availability_basis": "document_declared",
        "product_scope_confirmed": True,
    }


def test_document_only_capability_can_be_accepted_and_published(client):
    _, sid = setup(client)
    discover(client, sid)
    with session_factory()() as db:
        claim = next(
            item
            for item in db.scalars(
                select(CapabilityClaim).where(
                    CapabilityClaim.snapshot_id == sid,
                    CapabilityClaim.name == "多租户策略隔离",
                )
            )
            if "sheet" in item.origin_json["locator"]
        )
    assert claim.origin_json["locator"]["sheet"] == "功能参数"
    body = document_review(claim)
    response = client.post(f"/api/v1/capabilities/{claim.id}/review", headers=ADMIN, json=body)
    assert response.status_code == 200, response.text
    revision = response.json()
    assert revision["gate"]["document_evidence_used"] is True
    assert revision["gate"]["availability_basis"] == "document_declared"
    publish = client.post(
        f"/api/v1/snapshots/{sid}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [revision["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "发布文档单源能力，保留缺口",
        },
    )
    assert publish.status_code == 200, publish.text


def test_document_scope_flags_are_required(client):
    _, sid = setup(client)
    discover(client, sid)
    claim = draft_claim(sid, "多租户策略隔离")
    body = document_review(claim)
    body["assertions"][0]["document_scope_confirmed"] = False
    response = client.post(f"/api/v1/capabilities/{claim.id}/review", headers=ADMIN, json=body)
    assert response.status_code == 422
    body = document_review(claim)
    body["availability_basis"] = None
    response = client.post(f"/api/v1/capabilities/{claim.id}/review", headers=ADMIN, json=body)
    assert response.status_code == 422


def test_cross_source_same_name_is_reported_as_possible_duplicate(client):
    _, sid = setup(client)
    body = discover(client, sid)
    with session_factory().begin() as db:
        document_claim = db.scalar(
            select(CapabilityClaim).where(
                CapabilityClaim.snapshot_id == sid,
                CapabilityClaim.source_channel == "document",
                CapabilityClaim.name == "多租户隔离",
            )
        )
        module = Module(snapshot_id=sid, name="source-duplicate", path_globs=["*.py"])
        db.add(module)
        db.flush()
        investigation = Investigation(
            job_id=body["job_id"],
            snapshot_id=sid,
            module_id=module.id,
            status="succeeded",
        )
        db.add(investigation)
        db.flush()
        source_claim = CapabilityClaim(
            id="claim-source-dup",
            snapshot_id=sid,
            investigation_id=investigation.id,
            local_key="source-duplicate",
            name="多租户隔离",
            status="candidate",
            draft_json={"name": "多租户隔离"},
            source_channel="source",
        )
        db.add(source_claim)
        db.flush()
    scanned = client.post(f"/api/v1/snapshots/{sid}/conflicts/scan", headers=ADMIN)
    assert scanned.status_code == 200, scanned.text
    conflicts = client.get(f"/api/v1/snapshots/{sid}/conflicts", headers=ADMIN).json()["results"]
    duplicate = next(
        item
        for item in conflicts
        if item["type"] == "possible_duplicate"
        and {item["source_claim_id"], item["target_claim_id"]}
        == {document_claim.id, source_claim.id}
    )
    assert duplicate["status"] == "unresolved"
    assert duplicate["details"]["requires_human_resolution"] is True


def test_docx_paragraph_capability_has_paragraph_locator(client):
    _, sid = setup(client)
    discover(client, sid)
    with session_factory()() as db:
        claim = next(
            item
            for item in db.scalars(
                select(CapabilityClaim).where(
                    CapabilityClaim.snapshot_id == sid,
                    CapabilityClaim.source_channel == "document",
                )
            )
            if "paragraph" in item.origin_json["locator"]
        )
    assert claim.origin_json["document_id"]
    assert claim.name


def test_unresolved_conflict_blocks_accept_until_admin_resolution(client):
    _, sid = setup(client)
    discover(client, sid)
    conflicts = client.get(f"/api/v1/snapshots/{sid}/conflicts", headers=ADMIN).json()["results"]
    assert any(item["type"] == "support_exclusion_conflict" for item in conflicts)
    claim = draft_claim(sid, "规则同步")
    assert claim is not None
    body = document_review(claim, name="规则同步")
    response = client.post(f"/api/v1/capabilities/{claim.id}/review", headers=ADMIN, json=body)
    assert response.status_code == 422
    assert response.json()["error_code"] == "UNRESOLVED_CONFLICT"
    conflict = next(
        item for item in conflicts if claim.id in {item["source_claim_id"], item["target_claim_id"]}
    )
    resolved = client.post(
        f"/api/v1/conflicts/{conflict['id']}/resolve",
        headers=ADMIN,
        json={
            "decision": "resolved",
            "explanation": "逐行核对：一条为旧版本节点限制，一条为当前版本能力，范围不同",
            "human_confirmed": True,
        },
    )
    assert resolved.status_code == 200, resolved.text
    accepted = client.post(f"/api/v1/capabilities/{claim.id}/review", headers=ADMIN, json=body)
    assert accepted.status_code == 200, accepted.text


def source_evidence(cid, sid):
    from app.db.index_models import SourceFile
    from app.knowledge.evidence_registry import register
    from app.knowledge.schemas import EvidenceRequest
    from app.retrieval.schemas import RunContext

    with session_factory()() as db:
        file = db.scalar(
            select(SourceFile).where(
                SourceFile.snapshot_id == sid, SourceFile.path == "tenant/policy.py"
            )
        )
        receipt = register(
            db,
            RunContext(
                category_id=cid,
                snapshot_id=sid,
                principal_id="admin",
                policy="admin_staging",
            ),
            EvidenceRequest(
                kind="source",
                file_id=file.id,
                start_line=1,
                end_line=2,
                expected_sha256=file.sha256,
                supported_statement="源码声明租户策略隔离函数",
            ),
        )
        db.commit()
        return receipt.evidence_id


def test_dual_source_evidence_keeps_declared_scope(client):
    cid, sid = setup(client)
    discover(client, sid)
    claim = draft_claim(sid, "多租户隔离")
    body = document_review(claim, name="租户策略配置")
    from app.knowledge.quality import fields

    body["content"]["action"] = "提供租户策略隔离"
    body["content"]["target"] = "租户策略"
    body["content"]["implementation_mechanism"] = (
        "文档声明租户策略独立，源码仅显示隔离函数存在，不推导底层实现"
    )
    body["assertions"] = [
        {
            "field": key,
            "text": value,
            "evidence_ids": [claim.draft_json["evidence_ids"][0]],
            "semantic_decision": "supported",
            "rationale": "人工核对文档声明与源码函数存在，不作底层机制外推",
            "proof_basis": "document",
            "document_scope_confirmed": True,
        }
        for key, value in fields(body["content"]).items()
    ]
    source_id = source_evidence(cid, sid)
    for assertion in body["assertions"]:
        assertion["evidence_ids"].append(source_id)
        assertion["proof_basis"] = "mixed"
    body["declaration_type"] = "technical_mechanism"
    body["availability_basis"] = "mixed"
    response = client.post(f"/api/v1/capabilities/{claim.id}/review", headers=ADMIN, json=body)
    assert response.status_code == 200, response.text
    revision = response.json()
    assert revision["gate"]["document_evidence_used"] is True
    assert revision["gate"]["availability_basis"] == "mixed"
    from app.db.review_models import ClaimRevision

    with session_factory()() as db:
        assert db.get(ClaimRevision, revision["id"]).proof_kind == "mixed"
    published = client.post(
        f"/api/v1/snapshots/{sid}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [revision["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "双源合并但保持声明范围",
        },
    )
    assert published.status_code == 200, published.text
    current = client.get("/api/v1/capabilities", headers=ADMIN, params={"category_id": cid}).json()
    capability = current["capabilities"][0]
    assert capability["content"]["supported_scope"] == ["文档所述当前产品版本"]
    assert capability["review_status"] == "human_accepted"


def test_cross_snapshot_document_evidence_is_rejected(client):
    cid_a, sid_a = setup(client)
    discover(client, sid_a)
    cid_b, sid_b = setup(client)
    discover(client, sid_b)
    claim_b = draft_claim(sid_b, "多租户策略隔离")
    body = document_review(claim_b)
    foreign_evidence = body["assertions"][0]["evidence_ids"][0]
    claim_a = draft_claim(sid_a, "多租户隔离")
    body_a = document_review(claim_a, name="多租户隔离")
    for assertion in body_a["assertions"]:
        assertion["evidence_ids"] = [foreign_evidence]
        assertion["proof_basis"] = "document"
        assertion["document_scope_confirmed"] = True
    response = client.post(f"/api/v1/capabilities/{claim_a.id}/review", headers=ADMIN, json=body_a)
    assert response.status_code == 404
    assert response.json()["error_code"] == "EVIDENCE_NOT_FOUND"


def test_document_hash_change_fails_closed_in_probe(client, environment):
    from tests.conftest import READ

    object.__setattr__(environment, "service_mode", "retrieval")
    cid, sid = setup(client)
    discover(client, sid)
    claim = draft_claim(sid, "多租户隔离")
    revision = client.post(
        f"/api/v1/capabilities/{claim.id}/review",
        headers=ADMIN,
        json=document_review(claim, name="多租户隔离"),
    )
    assert revision.status_code == 200, revision.text
    published = client.post(
        f"/api/v1/snapshots/{sid}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [revision.json()["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "发布文档单源能力用于哈希关闭测试",
        },
    )
    assert published.status_code == 200, published.text
    with session_factory()() as db:
        doc = db.scalar(select(Document).where(Document.snapshot_id == sid))
    path = environment.document_store / doc.storage_key
    original = path.read_bytes()
    path.chmod(0o640)
    path.write_bytes(original + b"tampered")
    try:
        tampered = client.post(
            "/api/v1/probes",
            headers=READ,
            json={"category_id": cid, "requirement_text": "多租户隔离"},
        )
        assert tampered.status_code == 200
        assert tampered.json()["candidates"] == []
        assert any(
            gap["code"] == "DOCUMENT_HASH_MISMATCH" for gap in tampered.json()["unverified_gaps"]
        )
    finally:
        path.write_bytes(original)
        path.chmod(0o440)
    with session_factory().begin() as db:
        from app.db.index_models import DocumentSegment
        from app.db.knowledge_models import Evidence
        from app.db.review_models import ClaimRevision

        evidence_id = db.get(ClaimRevision, revision.json()["id"]).assertions_json[0][
            "evidence_ids"
        ][0]
        segment_id = db.get(Evidence, evidence_id).source_ref["segment_id"]
        segment = db.get(DocumentSegment, segment_id)
        segment.text_hash = "0" * 64
    broken = client.post(
        "/api/v1/probes",
        headers=READ,
        json={"category_id": cid, "requirement_text": "多租户隔离"},
    )
    assert broken.json()["candidates"] == []
    assert any(gap["code"] == "SEGMENT_HASH_MISMATCH" for gap in broken.json()["unverified_gaps"])
    with session_factory().begin() as db:
        segment = db.get(DocumentSegment, segment_id)
        segment.text_hash = digest(segment.text.encode())
    path.unlink()
    missing = client.post(
        "/api/v1/probes",
        headers=READ,
        json={"category_id": cid, "requirement_text": "多租户隔离"},
    )
    assert missing.json()["candidates"] == []
    assert any(
        gap["code"] == "EVIDENCE_SOURCE_UNAVAILABLE" for gap in missing.json()["unverified_gaps"]
    )


def test_document_discovery_budget_marks_partial(client, monkeypatch):
    from app.core.config import get_settings

    _, sid = setup(client)
    monkeypatch.setenv("DOCUMENT_DISCOVERY_MAX_DOCUMENTS", "1")
    get_settings.cache_clear()
    body = discover(client, sid)
    with session_factory()() as db:
        job = db.get(KnowledgeJob, body["job_id"])
        run = db.get(DocumentDiscoveryRun, body["run_id"])
    assert job.status == "partial"
    assert run.status == "partial"
    assert run.metrics_json["truncated"] is True
    assert run.metrics_json["documents_considered"] == 1
    codes = {gap["code"] for gap in run.coverage_json["gaps"]}
    assert "DOCUMENT_OR_UNIT_BUDGET_EXHAUSTED" in codes


def test_pdf_only_current_capability_can_be_discovered_and_published(client):
    from uuid import uuid4

    cid, sid = indexed(client, code="p16pdf-" + uuid4().hex[:8], data=p16_assets.source_archive())
    index_document(
        upload_document(
            client, cid, sid, "manual.pdf", p16_assets.pdf_document(), "application/pdf"
        )
    )
    discover(client, sid)
    with session_factory()() as db:
        claims = list(
            db.scalars(
                select(CapabilityClaim).where(
                    CapabilityClaim.snapshot_id == sid,
                    CapabilityClaim.source_channel == "document",
                )
            )
        )
    claim = next(item for item in claims if item.origin_json["locator"]["page"] == 2)
    assert "multi-tenant" in claim.name
    body = document_review(claim)
    response = client.post(f"/api/v1/capabilities/{claim.id}/review", headers=ADMIN, json=body)
    assert response.status_code == 200, response.text
    revision = response.json()
    published = client.post(
        f"/api/v1/snapshots/{sid}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [revision["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "PDF 单源能力发布，页码定位保持",
        },
    )
    assert published.status_code == 200, published.text


def test_document_assertion_cannot_expand_beyond_quote(client):
    from app.knowledge.quality import fields

    _, sid = setup(client)
    discover(client, sid)
    claim = draft_claim(sid, "多租户隔离")
    body = document_review(claim)
    body["content"]["action"] = "所有数据访问具备强事务隔离"
    body["content"]["limitations"] = ["文档仅声明租户策略隔离，未声明事务语义"]
    body["assertions"] = [
        {
            "field": key,
            "text": value,
            "evidence_ids": [claim.draft_json["evidence_ids"][0]],
            "semantic_decision": "supported",
            "rationale": "故意扩大断言，验证文档范围阻断",
            "proof_basis": "document",
            "document_scope_confirmed": True,
        }
        for key, value in fields(body["content"]).items()
    ]
    response = client.post(f"/api/v1/capabilities/{claim.id}/review", headers=ADMIN, json=body)
    assert response.status_code == 422
    assert response.json()["error_code"] == "REVIEW_GATE_FAILED"
    with session_factory()() as db:
        assert not db.scalar(
            select(CapabilityClaim.id).where(
                CapabilityClaim.id == claim.id,
                CapabilityClaim.status == "human_accepted",
            )
        )


def test_document_only_category_derivation_is_not_supported(client):
    from uuid import uuid4

    created = client.post(
        "/api/v1/categories",
        headers=ADMIN,
        json={"code": "p16-only-" + uuid4().hex[:8], "name": "仅文档品类"},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    response = client.post(f"/api/v1/categories/{cid}/document-staging-snapshots", headers=ADMIN)
    assert response.status_code == 409
    assert response.json()["error_code"] == "DERIVATION_SOURCE_REQUIRED"
