"""P1.5独立边界回归；不修改历史测试。"""

import copy

from sqlalchemy.orm import Session

from app.db.review_models import Publication
from app.db.session import get_engine
from tests.integration.test_p14_review_publication import ADMIN, accept, publish, request
from tests.integration.test_p15_probe import channels as _shared_channels
from tests.integration.test_p15_probe import prepared, query

channels = _shared_channels


def test_json_field_names_are_not_product_facts(channels):
    _, reader, cid, *_ = prepared(channels)
    result = query(reader, cid, "supported_scope")
    assert result.status_code == 200
    assert result.json()["candidates"] == []
    assert result.json()["support_status"] == "not_assessed"


def test_publish_update_and_rollback_read_only_current_manifest(channels):
    api, reader, cid, sid, claims, evidence, _, revision, first = prepared(channels)
    old = query(reader, cid).json()
    second_revision = accept(
        api, claims[1], request(evidence["core/i_preview.py"], "第二事件列表机制")
    )
    # 新审核并不自动成为当前正式产品事实。
    assert [c["revision_id"] for c in query(reader, cid).json()["candidates"]] == [revision["id"]]
    second = publish(api, sid, [second_revision["id"]], first["publication_id"])
    assert second.status_code == 200, second.text
    new = query(reader, cid).json()
    assert new["publication_id"] == second.json()["publication_id"]
    assert [c["revision_id"] for c in new["candidates"]] == [second_revision["id"]]
    restored = api.post(
        f"/api/v1/categories/{cid}/rollback",
        headers=ADMIN,
        json={
            "publication_id": first["publication_id"],
            "expected_active_publication_id": second.json()["publication_id"],
            "comment": "P1.5只读回滚验收",
        },
    )
    assert restored.status_code == 200, restored.text
    actual = query(reader, cid).json()
    old.pop("duration_ms")
    actual.pop("duration_ms")
    assert actual == old


def test_manifest_tamper_is_not_returned_as_knowledge(channels):
    _, reader, cid, _, _, _, _, _, first = prepared(channels)
    with Session(get_engine()) as db:
        row = db.get(Publication, first["publication_id"])
        payload = copy.deepcopy(row.payload_json)
        payload["capabilities"][0]["content"]["name"] = "被篡改能力"
        row.payload_json = payload
        db.commit()
    result = query(reader, cid)
    assert result.status_code == 409
    assert "PUBLICATION_TAMPERED" in result.text
    assert "被篡改能力" not in result.text


def test_cross_product_pointer_is_rejected(channels):
    _, reader, cid, _, _, _, _, _, first = prepared(channels)
    with Session(get_engine()) as db:
        db.get(Publication, first["publication_id"]).category_id = "unrelated-category"
        db.commit()
    result = query(reader, cid)
    assert result.status_code == 409
    assert result.json().get("candidates") is None


def test_candidate_limit_does_not_truncate_business_boundaries(channels):
    api, reader, cid, sid, claims, evidence, _, revision, first = prepared(channels)
    second_revision = accept(
        api, claims[1], request(evidence["core/i_preview.py"], "第二事件列表机制")
    )
    second = publish(api, sid, [revision["id"], second_revision["id"]], first["publication_id"])
    assert second.status_code == 200, second.text
    result = query(reader, cid, limit=1)
    assert result.status_code == 200, result.text
    body = result.json()
    assert len(body["candidates"]) == 1
    assert body["truncated"] is True
    assert body["candidates"][0]["content"]["limitations"] == ["不证明存储行为或网络送达"]
    assert body["formal_compliance_decision"] is None
