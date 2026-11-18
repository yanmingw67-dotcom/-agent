"""P1.5 只读发布知识检索；复用既有夹具，不修改历史测试。"""

import copy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.retrieval.probe import probe_db
from tests.conftest import ADMIN, READ
from tests.integration.test_p14_review_publication import (
    accept,
    publish,
    request,
    setup,
)


@pytest.fixture
def channels(client, environment):
    # API 路由在构建时确定；独立构建只读服务，不添加后台写入路由。
    environment.service_mode = "retrieval"
    with TestClient(create_app()) as reader:
        yield client, reader


def query(reader, cid, requirement="事件", **extra):
    return reader.post(
        "/api/v1/probes",
        headers=READ,
        json={"category_id": cid, "requirement_text": requirement, **extra},
    )


def prepared(channels):
    api, reader = channels
    cid, sid, claims, evidence = setup(api)
    body = request(evidence["core/i_preview.py"])
    revision = accept(api, claims[0], body)
    result = publish(api, sid, [revision["id"]])
    assert result.status_code == 200, result.text
    return api, reader, cid, sid, claims, evidence, body, revision, result.json()


def test_unpublished_and_unreviewed_are_not_formal_facts(channels):
    api, reader = channels
    cid, sid, claims, evidence = setup(api)
    assert query(reader, cid).status_code == 409
    revision = accept(api, claims[0], request(evidence["core/i_preview.py"]))
    assert query(reader, cid).status_code == 409
    assert publish(api, sid, [revision["id"]]).status_code == 200
    result = query(reader, cid)
    assert result.status_code == 200, result.text
    data = result.json()
    assert [c["capability_id"] for c in data["candidates"]] == [claims[0]]
    assert not set(claims[1:]) & {c["capability_id"] for c in data["candidates"]}
    assert data["formal_compliance_decision"] is None
    assert data["support_status"] == "not_assessed"


def test_complete_boundaries_and_verified_evidence_are_preserved(channels):
    api, reader, cid, sid, _, _, body, revision, publication = prepared(channels)
    response = query(reader, cid)
    assert response.status_code == 200, response.text
    data = response.json()
    candidate = data["candidates"][0]
    assert candidate["content"] == body["content"]
    assert candidate["assertions"] == revision["assertions"]
    assert candidate["revision_id"] == revision["id"]
    assert data["snapshot_id"] == sid
    assert (
        data["coverage"]
        == api.get("/api/v1/capabilities", headers=READ, params={"category_id": cid}).json()[
            "coverage"
        ]
    )
    assert data["publication_id"] == publication["publication_id"]
    assert response.headers["cache-control"] == "no-store"
    assert data["evidence"]
    for evidence in data["evidence"]:
        assert evidence["snapshot_id"] == sid
        assert evidence["source_hash"] and evidence["quote_hash"] and evidence["quote"]
        assert evidence["semantic_verification"] == "unverified"


def test_unknown_is_not_unsupported(channels):
    _, reader, cid, *_ = prepared(channels)
    response = query(reader, cid, "totally_unrelated_feature_987654")
    assert response.status_code == 200
    data = response.json()
    assert data["candidates"] == []
    assert data["status"] == "no_match"
    assert data["support_status"] == "not_assessed"
    assert "no_match_does_not_mean_unsupported" in data["warnings"]
    assert data["formal_compliance_decision"] is None


@pytest.mark.parametrize(
    "override",
    [
        {"snapshot_id": "internal"},
        {"publication_id": "historical"},
        {"policy": "admin_staging"},
        {"limit": 0},
        {"limit": 21},
        {"requirement_text": "   "},
    ],
)
def test_request_cannot_override_scope(channels, override):
    _, reader = channels
    data = {"category_id": "test", "requirement_text": "事件"}
    data.update(override)
    assert reader.post("/api/v1/probes", headers=READ, json=data).status_code == 422


def test_authentication_and_absence_of_write_routes(channels):
    _, reader = channels
    body = {"category_id": "test", "requirement_text": "事件"}
    assert reader.post("/api/v1/probes", json=body).status_code == 401
    assert reader.post("/api/v1/categories", headers=ADMIN, json={}).status_code == 404
    assert (
        reader.post("/api/v1/capabilities/example/review", headers=ADMIN, json={}).status_code
        == 404
    )
    assert (
        reader.post("/api/v1/snapshots/example/publish", headers=ADMIN, json={}).status_code == 404
    )
    assert reader.post("/api/v1/probes?snapshot_id=x", headers=READ, json=body).status_code == 422


def test_api_service_does_not_run_probe(client):
    response = client.post(
        "/api/v1/probes",
        headers=READ,
        json={"category_id": "test", "requirement_text": "事件"},
    )
    assert response.status_code == 503


def test_cross_product_query_has_no_shared_candidates_or_evidence(channels):
    api, reader, cid, sid, claims, *_ = prepared(channels)
    cid2, sid2, claims2, evidence2 = setup(api)
    r2 = accept(api, claims2[0], request(evidence2["core/i_preview.py"]))
    assert publish(api, sid2, [r2["id"]]).status_code == 200
    first, second = query(reader, cid).json(), query(reader, cid2).json()
    assert {c["capability_id"] for c in first["candidates"]} == {claims[0]}
    assert {c["capability_id"] for c in second["candidates"]} == {claims2[0]}
    assert all(e["snapshot_id"] == sid for e in first["evidence"])
    assert all(e["snapshot_id"] == sid2 for e in second["evidence"])
    assert not {e["id"] for e in first["evidence"]} & {e["id"] for e in second["evidence"]}
    assert query(reader, "missing-product").status_code == 404


@pytest.mark.parametrize("decision", ["modify", "return", "reject", "accept"])
def test_unpublished_review_does_not_change_frozen_publication(channels, decision):
    api, reader, cid, _, claims, _, body, revision, publication = prepared(channels)
    update = copy.deepcopy(body)
    update["action"] = decision
    update["expected_revision"] = revision["number"]
    newer = accept(api, claims[0], update)
    response = query(reader, cid)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["publication_id"] == publication["publication_id"]
    assert data["publication_hash"] == publication["payload_hash"]
    assert [c["capability_id"] for c in data["candidates"]] == [claims[0]]
    assert [c["revision_id"] for c in data["candidates"]] == [revision["id"]]
    assert data["candidates"][0]["content"] == body["content"]
    assert newer["id"] not in response.text
    assert not any(g["code"] == "NEWER_REVIEW_NOT_PUBLISHED" for g in data["unverified_gaps"])
    frozen = api.get("/api/v1/capabilities", headers=READ, params={"category_id": cid})
    assert frozen.status_code == 200
    assert [c["revision_id"] for c in frozen.json()["capabilities"]] == [revision["id"]]


def test_new_publication_must_use_latest_accepted_revision(channels):
    api, reader, cid, sid, claims, evidence, _, revision, first = prepared(channels)
    body = request(evidence["core/i_preview.py"], "第二审核版本")
    body["expected_revision"] = revision["number"]
    newer = accept(api, claims[0], body)
    stale = publish(api, sid, [revision["id"]], first["publication_id"])
    assert stale.status_code == 422, stale.text
    assert "PUBLICATION_REVIEW_REQUIRED" in stale.text
    frozen = query(reader, cid).json()
    assert frozen["publication_id"] == first["publication_id"]
    assert frozen["candidates"][0]["revision_id"] == revision["id"]
    second = publish(api, sid, [newer["id"]], first["publication_id"])
    assert second.status_code == 200, second.text
    switched = query(reader, cid).json()
    assert switched["publication_id"] == second.json()["publication_id"]
    assert switched["candidates"][0]["revision_id"] == newer["id"]
    assert switched["candidates"][0]["content"]["name"] == "第二审核版本"


def test_readonly_transaction_denies_writes_and_restores_pool(environment):
    dependency = probe_db()
    db = next(dependency)
    with pytest.raises(Exception, match="readonly"):
        db.execute(text("CREATE TABLE probe_must_not_write (id INTEGER)"))
    dependency.close()
    from app.db.session import session_factory

    with session_factory()() as session:
        session.execute(text("CREATE TABLE ordinary_connection_still_writable (id INTEGER)"))
        session.rollback()
