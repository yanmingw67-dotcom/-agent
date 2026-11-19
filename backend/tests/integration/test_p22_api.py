"""P2.2 API、固定发布、隔离和 5/15 参数结论数量的集成测试。

知识内容由测试构造并标注为合成；这些断言不证明真实产品能力。
"""

import copy
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.knowledge_models import CapabilityClaim
from app.db.review_models import Publication
from app.db.session import session_factory
from app.evaluation import service
from app.knowledge.quality import fields
from app.main import create_app
from tests.conftest import ADMIN, READ
from tests.integration.test_p14_review_publication import accept, publish, setup
from tests.p21_assets import PARAM_NAMES, five_text, waf_docx

SYNTHETIC_FIVE = [
    "支持 HTTP/1.1",
    "HTTP/2",
    "WebSocket 协议解析",
    "SQL 注入",
    "XSS",
    "命令注入等攻击检测与阻断",
    "支持日志导出 PDF",
    "支持多租户策略",
    "支持双机热备部署",
]


def phrase_set(names=None, five=False):
    values = list(SYNTHETIC_FIVE) if five else []
    if names:
        for index, name in enumerate(names, 1):
            # 质量门禁把“隔离”等词视为高风险实现承诺；合成用例跳过该名称，
            # 让对应参数保持无法确认，避免伪造底层实现证据。
            if "隔离" in name:
                continue
            values.extend(
                [
                    f"支持{name}能力{index}A",
                    f"支持{name}能力{index}B",
                    f"支持{name}能力{index}C",
                ]
            )
    return values


def synthetic_content(phrases, label):
    return {
        "name": f"合成测试知识-{label}",
        "action": "；".join(phrases),
        "target": "合成技术参数样例",
        "business_goal": "验证三值评估管线",
        "implementation_mechanism": "测试构造的合成证据，不代表真实产品实现",
        "supported_scope": ["测试构造的合成 WAF 样例范围"],
        "conditions": [],
        "limitations": ["不证明真实产品能力或生产性能", "不代表真实雷池 WAF 能力"],
    }


def review_body(content, eid):
    return {
        "action": "accept",
        "expected_revision": 0,
        "declaration_type": "technical_mechanism",
        "content": content,
        "assertions": [
            {
                "field": key,
                "text": value,
                "evidence_ids": [eid],
                "semantic_decision": "supported",
                "rationale": "测试构造的合成知识，仅验证评估管线，不代表真实能力",
            }
            for key, value in fields(content).items()
        ],
        "comment": "合成评估测试知识，明确非真实产品能力",
        "human_confirmed": True,
    }


def publish_synthetic(client, phrases, label):
    cid, sid, claims, evidence = setup(client)
    body = review_body(synthetic_content(phrases, label), evidence["core/i_preview.py"])
    revision = accept(client, claims[0], body)
    result = publish(client, sid, [revision["id"]])
    assert result.status_code == 200, result.text
    return cid, result.json()


def create_extraction(client, cid, headers=READ, text=None):
    response = client.post(
        "/api/v1/tender/extractions",
        headers=headers,
        json={"category_id": cid, "text": text or five_text()},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    detail = client.get(f"/api/v1/tender/extractions/{job_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    return job_id, detail.json()


def create_evaluation(client, extraction_id, headers=READ, key=None):
    payload = {"extraction_job_id": extraction_id}
    if key:
        payload["idempotency_key"] = key
    response = client.post("/api/v1/tender/evaluations", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def test_five_parameters_yield_exactly_five_verdicts_and_fixed_publication(client):
    cid, publication = publish_synthetic(client, phrase_set(five=True), "五项")
    extraction_id, extraction = create_extraction(client, cid)
    assert extraction["extracted_count"] == 5

    created = create_evaluation(client, extraction_id, key="five-key")
    job_id = created["job_id"]
    repeated = create_evaluation(client, extraction_id, key="five-key")
    assert repeated["job_id"] == job_id and repeated["created"] is False

    detail = client.get(f"/api/v1/tender/evaluations/{job_id}", headers=READ)
    assert detail.status_code == 200, detail.text
    job = detail.json()
    assert job["status"] == "succeeded", job
    assert job["publication_id"] == publication["publication_id"]
    assert job["parameter_count"] == 5
    assert job["checkpoint"] == {"completed": 5, "total": 5}

    results = client.get(f"/api/v1/tender/evaluations/{job_id}/results", headers=READ)
    assert results.status_code == 200, results.text
    data = results.json()
    assert len(data["parameters"]) == 5
    assert {item["verdict"] for item in data["parameters"]} <= {"支持", "不支持", "无法确认"}
    assert any(item["verdict"] == "支持" for item in data["parameters"])
    for item in data["parameters"]:
        assert item["atomic_requirements"]
        assert item["rationale"]
        for atom in item["atomic_requirements"]:
            assert atom["verdict"] in {"支持", "不支持", "无法确认"}
            if atom["verdict"] == "支持":
                assert atom["evidence"]
                assert all(
                    evidence["verification_state"] == "verified_support"
                    for evidence in atom["evidence"]
                )

    evidence = client.get(f"/api/v1/tender/evaluations/{job_id}/evidence", headers=READ)
    assert evidence.status_code == 200
    for item in evidence.json()["evidence"]:
        assert item["source_hash"] and item["quote_hash"] and item["quote"]
        assert item["verification_state"] in {
            "verified_support",
            "verified_contradiction",
            "verified_context",
        }

    # 管理员之后发布新版本，不改变已固定运行/已完成任务。
    cid2, sid2, claims2, evidence2 = setup(client)
    assert cid2 != cid
    newer_body = review_body(
        synthetic_content(["支持 HTTP/1.1", "支持日志导出 PDF"], "新发布"),
        evidence2["core/i_preview.py"],
    )
    newer = accept(client, claims2[0], newer_body)
    assert publish(client, sid2, [newer["id"]]).status_code == 200
    after = client.get(f"/api/v1/tender/evaluations/{job_id}/results", headers=READ).json()
    assert after["job"]["publication_id"] == publication["publication_id"]
    assert after["job"]["published_snapshot_id"] == publication["snapshot_id"]
    assert [p["verdict"] for p in after["parameters"]] == [p["verdict"] for p in data["parameters"]]


def test_document_evidence_is_verified_against_pinned_document(client):
    from tests.integration.test_p16_discovery import (
        discover,
        document_review,
    )
    from tests.integration.test_p16_discovery import (
        setup as document_setup,
    )

    cid, snapshot_id = document_setup(client)
    discover(client, snapshot_id)
    with session_factory()() as db:
        claim = next(
            item
            for item in db.scalars(
                select(CapabilityClaim).where(
                    CapabilityClaim.snapshot_id == snapshot_id,
                    CapabilityClaim.name == "多租户策略隔离",
                )
            )
            if "sheet" in item.origin_json["locator"]
        )
    reviewed = client.post(
        f"/api/v1/capabilities/{claim.id}/review",
        headers=ADMIN,
        json=document_review(claim),
    )
    assert reviewed.status_code == 200, reviewed.text
    revision = reviewed.json()
    published = client.post(
        f"/api/v1/snapshots/{snapshot_id}/publish",
        headers=ADMIN,
        json={
            "revision_ids": [revision["id"]],
            "expected_active_publication_id": None,
            "acknowledge_coverage_gaps": True,
            "comment": "P2.2 文档证据核验用例",
        },
    )
    assert published.status_code == 200, published.text

    extraction_id, extraction = create_extraction(client, cid, text="1、支持多租户策略隔离。")
    assert extraction["extracted_count"] == 1
    job_id = create_evaluation(client, extraction_id)["job_id"]
    data = client.get(f"/api/v1/tender/evaluations/{job_id}/results", headers=READ).json()
    assert data["job"]["status"] == "succeeded", data["job"]
    assert data["job"]["publication_id"] == published.json()["publication_id"]
    assert any(
        atom["verdict"] == "支持"
        for item in data["parameters"]
        for atom in item["atomic_requirements"]
    )
    evidence = client.get(f"/api/v1/tender/evaluations/{job_id}/evidence", headers=READ).json()[
        "evidence"
    ]
    assert evidence
    assert {item["kind"] for item in evidence} == {"document"}
    assert all(item["verification_state"] == "verified_support" for item in evidence)
    assert all(item["quote_hash"] and item["source_hash"] for item in evidence)


def test_single_parameter_failure_is_isolated_and_fail_closed(client, monkeypatch):
    cid, _ = publish_synthetic(client, phrase_set(five=True), "参数失败")
    extraction_id, _ = create_extraction(client, cid)
    original = service.AtomicEvaluator.evaluate

    def flaky(evaluator, requirements, parameter_name):
        if "SQL" in parameter_name:
            raise RuntimeError("synthetic parameter failure")
        return original(evaluator, requirements, parameter_name)

    monkeypatch.setattr(service.AtomicEvaluator, "evaluate", flaky)
    job_id = create_evaluation(client, extraction_id)["job_id"]
    data = client.get(f"/api/v1/tender/evaluations/{job_id}/results", headers=READ).json()
    assert data["job"]["status"] == "partial", data["job"]
    assert data["job"]["metrics"]["failed_parameters"] == 1
    assert len(data["parameters"]) == 5
    failed = [item for item in data["parameters"] if item["error_code"]]
    assert len(failed) == 1
    assert failed[0]["verdict"] == "无法确认"
    assert all(
        atom["assessment_method"] == "failed_closed" for atom in failed[0]["atomic_requirements"]
    )
    assert (
        sum(item["verdict"] == "支持" for item in data["parameters"] if not item["error_code"]) == 4
    )


def test_word_fifteen_parameters_and_45_atoms_yield_15_verdicts(client):
    cid, publication = publish_synthetic(client, phrase_set(PARAM_NAMES), "十五项")
    response = client.post(
        "/api/v1/tender/extractions/upload",
        headers=READ,
        data={"category_id": cid},
        files={
            "file": (
                "synthetic-waf.docx",
                waf_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 202, response.text
    extraction_id = response.json()["job_id"]
    extraction = client.get(f"/api/v1/tender/extractions/{extraction_id}", headers=READ).json()
    assert extraction["extracted_count"] == 15
    assert sum(len(item["atomic_requirements"]) for item in extraction["parameters"]) == 45

    job_id = create_evaluation(client, extraction_id)["job_id"]
    data = client.get(f"/api/v1/tender/evaluations/{job_id}/results", headers=READ).json()
    assert data["job"]["status"] == "succeeded", data["job"]
    assert data["job"]["publication_id"] == publication["publication_id"]
    assert len(data["parameters"]) == 15
    assert sum(len(p["atomic_requirements"]) for p in data["parameters"]) == 45
    assert {p["verdict"] for p in data["parameters"]} <= {"支持", "不支持", "无法确认"}


def test_cross_user_and_admin_cannot_read_evaluation(environment, monkeypatch):
    monkeypatch.setenv("READ_USER_TOKENS", json.dumps({"alice": "x" * 48, "bob": "y" * 48}))
    get_settings.cache_clear()
    alice = {"Authorization": "Bearer " + "x" * 48}
    bob = {"Authorization": "Bearer " + "y" * 48}
    with TestClient(create_app()) as client:
        cid, _ = publish_synthetic(client, phrase_set(five=True), "越权")
        extraction_id, _ = create_extraction(client, cid, headers=alice)
        job_id = create_evaluation(client, extraction_id, headers=alice)["job_id"]
        for headers in (bob, READ, ADMIN):
            assert (
                client.get(f"/api/v1/tender/evaluations/{job_id}", headers=headers).status_code
                == 404
            )
            assert (
                client.get(
                    f"/api/v1/tender/evaluations/{job_id}/results", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.get(
                    f"/api/v1/tender/evaluations/{job_id}/evidence", headers=headers
                ).status_code
                == 404
            )
            assert (
                client.post(
                    f"/api/v1/tender/evaluations/{job_id}/retry", headers=headers
                ).status_code
                == 404
            )
        assert client.get("/api/v1/tender/evaluations", headers=alice).json()["jobs"]
        assert client.get("/api/v1/tender/evaluations", headers=bob).json()["jobs"] == []


def test_tampered_pinned_publication_fails_closed_and_retry_resumes(client, monkeypatch):
    cid, publication = publish_synthetic(client, phrase_set(five=True), "固定发布")
    extraction_id, _ = create_extraction(client, cid)
    original = service.run_evaluation_job
    monkeypatch.setattr(service, "run_evaluation_job", lambda *args, **kwargs: None)
    created = create_evaluation(client, extraction_id)
    job_id = created["job_id"]
    monkeypatch.setattr(service, "run_evaluation_job", original)

    with session_factory().begin() as db:
        row = db.get(Publication, publication["publication_id"])
        original_payload = copy.deepcopy(row.payload_json)
        row.payload_json = {**row.payload_json, "tampered": True}
    original(job_id)
    failed = client.get(f"/api/v1/tender/evaluations/{job_id}", headers=READ).json()
    assert failed["status"] == "failed"
    assert failed["error_code"] == "PUBLICATION_TAMPERED"

    with session_factory().begin() as db:
        db.get(Publication, publication["publication_id"]).payload_json = original_payload
    retried = client.post(f"/api/v1/tender/evaluations/{job_id}/retry", headers=READ)
    assert retried.status_code == 202, retried.text
    recovered = client.get(f"/api/v1/tender/evaluations/{job_id}", headers=READ).json()
    assert recovered["status"] == "succeeded", recovered
    assert recovered["checkpoint"] == {"completed": 5, "total": 5}
