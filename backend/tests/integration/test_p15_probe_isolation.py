"""P1.5 R02：普通 Probe 只返回安全覆盖摘要，未发布候选不得泄漏。"""

import json

from tests.conftest import ADMIN, READ
from tests.integration.test_p14_review_publication import accept, request
from tests.integration.test_p15_probe import channels as _shared_channels
from tests.integration.test_p15_probe import prepared, query

channels = _shared_channels

SAFE_COVERAGE_KEYS = {
    "coverage_incomplete",
    "knowledge_build_complete",
    "semantic_coverage",
    "source_file_count",
    "uninvestigated_file_count",
    "unexplained_entrypoint_count",
    "pending_investigation_count",
    "deferred_investigation_count",
    "unverified_assertion_count",
    "unverified_document_segment_count",
    "documents_truncated",
    "gap_types",
}
FORBIDDEN_DETAIL_KEYS = {
    "snapshot_id",
    "analyzed_ranges",
    "uninvestigated_files",
    "unexplained_entrypoints",
    "pending",
    "deferred",
    "unverified_assertions",
    "unverified_document_segments",
    "note",
}


def test_probe_coverage_is_safe_and_hides_unpublished_candidates(channels):
    api, reader, cid, sid, claims, evidence, _, revision, first = prepared(channels)
    body = request(evidence["core/i_preview.py"], "未发布候选泄漏标记")
    body["expected_revision"] = revision["number"]
    newer = accept(api, claims[0], body)

    response = query(reader, cid)
    assert response.status_code == 200, response.text
    data = response.json()
    coverage = data["coverage"]

    assert set(coverage) == SAFE_COVERAGE_KEYS
    assert not (FORBIDDEN_DETAIL_KEYS & set(coverage))
    assert coverage["coverage_incomplete"] is True
    assert coverage["knowledge_build_complete"] is False
    assert coverage["semantic_coverage"] == "unknown"
    assert coverage["source_file_count"] > 0
    assert coverage["unverified_assertion_count"] > 0
    assert {"COVERAGE_INCOMPLETE", "SEMANTIC_COVERAGE_UNKNOWN", "UNVERIFIED_ASSERTIONS"} <= set(
        coverage["gap_types"]
    )
    assert data["publication_id"] == first["publication_id"]
    assert data["candidates"][0]["revision_id"] == revision["id"]
    assert newer["id"] not in response.text
    assert "未发布候选泄漏标记" not in response.text
    for unpublished in claims[1:]:
        assert unpublished not in response.text
        assert unpublished not in json.dumps(data["unverified_gaps"])
    assert "no_match_does_not_mean_unsupported" in data["warnings"]
    assert data["formal_compliance_decision"] is None
    assert data["support_status"] == "not_assessed"


def test_admin_can_still_read_full_coverage_while_read_role_gets_summary(channels):
    api, reader, cid, sid, claims, *_ = prepared(channels)
    admin_coverage = api.get(f"/api/v1/snapshots/{sid}/coverage", headers=ADMIN)
    assert admin_coverage.status_code == 200, admin_coverage.text
    detail = admin_coverage.json()
    assert "unverified_assertions" in detail
    assert "uninvestigated_files" in detail
    assert any(item["claim_id"] in claims[1:] for item in detail["unverified_assertions"])

    admin_caps = api.get("/api/v1/capabilities", headers=ADMIN, params={"category_id": cid})
    assert admin_caps.status_code == 200
    assert "unverified_assertions" in admin_caps.json()["coverage"]

    read_caps = api.get("/api/v1/capabilities", headers=READ, params={"category_id": cid})
    assert read_caps.status_code == 200
    assert set(read_caps.json()["coverage"]) == SAFE_COVERAGE_KEYS
    assert not (FORBIDDEN_DETAIL_KEYS & set(read_caps.json()["coverage"]))

    denied = api.get(f"/api/v1/snapshots/{sid}/coverage", headers=READ)
    assert denied.status_code == 403

    admin_probe = reader.post(
        "/api/v1/probes",
        headers=ADMIN,
        json={"category_id": cid, "requirement_text": "事件"},
    )
    assert admin_probe.status_code == 200, admin_probe.text
    assert set(admin_probe.json()["coverage"]) == SAFE_COVERAGE_KEYS
    assert not (FORBIDDEN_DETAIL_KEYS & set(admin_probe.json()["coverage"]))
