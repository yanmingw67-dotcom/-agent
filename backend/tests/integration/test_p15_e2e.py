"""P1.5完整静态构建链；模型为明确标注的本地FunctionModel，不调用外部服务。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.runtime import run_builder
from app.db.knowledge_models import CapabilityClaim
from app.db.models import KnowledgeJob
from app.db.session import get_engine
from tests.integration.test_p13_autonomous import model, setup
from tests.integration.test_p14_review_publication import accept, publish, request
from tests.integration.test_p15_probe import channels as _shared_channels
from tests.integration.test_p15_probe import query

channels = _shared_channels


def test_upload_autonomous_review_publish_probe(channels):
    api, reader = channels
    cid, sid, jid, _ = setup(api, count=3)
    assert query(reader, cid).status_code == 409
    fixture_model, _ = model()
    run_builder(jid, "auto-test", fixture_model)
    with Session(get_engine()) as db:
        job = db.get(KnowledgeJob, jid)
        assert job.status == "partial"
        assert job.error_code is None
        claims = list(db.scalars(select(CapabilityClaim).where(CapabilityClaim.snapshot_id == sid)))
        preview = next(c for c in claims if "i_preview.py" in str(c.draft_json))
        claim_id = preview.id
        draft = dict(preview.draft_json)
    assert query(reader, cid).status_code == 409
    # 从实际自主探索登记的证据构造人工审核，不将模型草稿自动批准。
    detail = api.get(
        f"/api/v1/admin/capabilities/{claim_id}", headers={"Authorization": "Bearer " + "a" * 48}
    )
    assert detail.status_code == 200, detail.text
    evidence_ids = draft.get("evidence_ids", [])
    assert evidence_ids, draft
    body = request(evidence_ids[0])
    revision = accept(api, claim_id, body)
    assert query(reader, cid).status_code == 409
    publication = publish(api, sid, [revision["id"]])
    assert publication.status_code == 200, publication.text
    result = query(reader, cid)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["publication_id"] == publication.json()["publication_id"]
    assert [c["capability_id"] for c in data["candidates"]] == [claim_id]
    assert data["formal_compliance_decision"] is None
    assert data["support_status"] == "not_assessed"
    assert data["evidence"]
    assert data["coverage"]
    with Session(get_engine()) as db:
        assert db.get(CapabilityClaim, claim_id).draft_json == draft
