"""Probe 的证据失效与空知识边界；不修改历史测试。"""

from pathlib import Path

from sqlalchemy.orm import Session

from app.db.review_models import Publication
from app.db.session import get_engine
from app.ingestion.text import digest
from app.knowledge.evidence_registry import canonical
from tests.integration.test_p15_probe import channels as _shared_channels
from tests.integration.test_p15_probe import prepared, query

channels = _shared_channels


def test_changed_source_is_not_returned_as_product_fact(channels, environment):
    _, reader, cid, sid, _, _, _, _, _ = prepared(channels)
    source = Path(environment.source_root) / sid / "source" / "core" / "i_preview.py"
    assert source.exists()
    source.chmod(0o640)
    source.write_text("# 证据登记后源码改变\n", encoding="utf-8")
    response = query(reader, cid)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["candidates"] == []
    assert data["evidence"] == []
    assert data["status"] == "partial"
    assert data["support_status"] == "not_assessed"
    assert data["unverified_gaps"]


def test_missing_source_fails_closed_without_disclosing_file_path(channels, environment):
    _, reader, cid, sid, _, _, _, _, _ = prepared(channels)
    source = Path(environment.source_root) / sid / "source" / "core" / "i_preview.py"
    assert source.exists()
    source.parent.chmod(0o750)
    source.unlink()
    response = query(reader, cid)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["candidates"] == []
    assert data["status"] == "partial"
    assert str(environment.source_root) not in response.text


def test_empty_manifest_is_unknown_not_unsupported(channels):
    _, reader, cid, _, _, _, _, _, published = prepared(channels)
    with Session(get_engine()) as db:
        row = db.get(Publication, published["publication_id"])
        payload = dict(row.payload_json)
        payload["capabilities"] = []
        payload["relations"] = []
        row.payload_json = payload
        row.payload_hash = digest(canonical(payload).encode())
        db.commit()
    response = query(reader, cid)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "knowledge_empty"
    assert data["support_status"] == "not_assessed"
    assert data["formal_compliance_decision"] is None
    assert not data["candidates"]
