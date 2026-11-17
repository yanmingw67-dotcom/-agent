"""P14-R01～R03增量回归；原194项测试不改写。"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.security import AppError
from app.db.models import Category, Snapshot
from app.db.session import session_factory
from app.knowledge import review as service
from app.knowledge.review_schemas import MergeRequest, ReviewRequest
from tests.conftest import ADMIN
from tests.integration.test_p14_review_publication import accept, publish, request, setup


@pytest.mark.parametrize("field,value", [("status", "failed"), ("index_status", "not_indexed")])
def test_lock_refreshes_snapshot_from_other_session(client, field, value):
    cid, sid, _, _ = setup(client)
    with session_factory()() as stale:
        cached_snapshot = stale.get(Snapshot, sid)
        cached_category = stale.get(Category, cid)
        stale.commit()
        with session_factory().begin() as writer:
            setattr(writer.get(Snapshot, sid), field, value)
        assert getattr(cached_snapshot, field) != value
        with pytest.raises(AppError) as error:
            service.lock_snapshot(stale, sid)
        assert error.value.error_code == "REVIEW_SOURCE_NOT_READY"
        assert getattr(cached_snapshot, field) == value
        assert stale.get(Category, cid) is cached_category


def test_lock_refreshes_both_publication_pointers(client):
    cid, sid, ids, ev = setup(client)
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))
    with session_factory()() as stale:
        cached = stale.get(Category, cid)
        snapshot = stale.get(Snapshot, sid)
        stale.commit()
        pid = publish(client, sid, [a["id"]]).json()["publication_id"]
        assert cached.active_publication_id is None and snapshot.status == "extracted"
        service.lock_snapshot(stale, sid)
        assert cached.active_publication_id == pid
        assert cached.active_snapshot_id == sid
        assert snapshot.status == "published"


def test_merge_reads_latest_only_after_scope_locks(client, monkeypatch):
    _, _, ids, ev = setup(client)
    for claim in ids[:2]:
        accept(client, claim, request(ev["core/i_preview.py"]))
    original_lock, original_latest = service.lock_snapshot, service.latest
    locked = []

    def lock(db, sid):
        row = original_lock(db, sid)
        locked.append(sid)
        return row

    def latest(db, claim):
        assert locked, "不得锁前读取审核修订"
        return original_latest(db, claim)

    monkeypatch.setattr(service, "lock_snapshot", lock)
    monkeypatch.setattr(service, "latest", latest)
    with session_factory().begin() as db:
        result = service.merge(
            db,
            MergeRequest(
                source_claim_id=ids[0],
                target_claim_id=ids[1],
                source_revision=1,
                target_revision=1,
                relation_type="same_as",
                human_confirmed=True,
                comment="检查锁后读取顺序",
            ),
        )
        assert result.decision == "merged"


@pytest.mark.parametrize("changed_side", [0, 1])
def test_old_session_cannot_merge_after_other_session_review(client, changed_side):
    _, _, ids, ev = setup(client)
    for claim in ids[:2]:
        accept(client, claim, request(ev["core/i_preview.py"]))
    with session_factory()() as stale:
        cached = [service.latest(stale, claim) for claim in ids[:2]]
        stale.commit()
        with session_factory().begin() as writer:
            service.review(
                writer,
                ids[changed_side],
                ReviewRequest(action="return", expected_revision=1, comment="另一审核事务退回"),
            )
        assert all(r.number == 1 for r in cached)
        with pytest.raises(AppError) as error:
            service.merge(
                stale,
                MergeRequest(
                    source_claim_id=ids[0],
                    target_claim_id=ids[1],
                    source_revision=1,
                    target_revision=1,
                    relation_type="same_as",
                    human_confirmed=True,
                    comment="陈旧审核不能合并",
                ),
            )
        assert error.value.error_code == "MERGE_REVISION_CONFLICT"


def test_api_diagnostic_correlates_without_sql_or_credentials(client, monkeypatch, caplog):
    from app.knowledge import publisher

    class DriverError(Exception):
        sqlstate = "23514"
        diag = SimpleNamespace(
            constraint_name="test_publish_guard",
            table_name="source_snapshots",
            column_name=None,
            context=None,
        )

    def fail(*args):
        raise DBAPIError("SECRET_SQL", {"token": "SECRET_TOKEN"}, DriverError("SECRET_BODY"))

    _, sid, ids, ev = setup(client)
    a = accept(client, ids[0], request(ev["core/i_preview.py"]))
    monkeypatch.setattr(publisher, "coverage", fail)
    with caplog.at_level("ERROR", logger="knowledge.audit"):
        response = publish(client, sid, [a["id"]])
    assert response.status_code == 503
    records = [json.loads(r.message) for r in caplog.records if '"database_error"' in r.message]
    assert len(records) == 1
    assert records[0]["correlation_id"] == response.json()["correlation_id"]
    assert records[0]["constraint_name"] == "test_publish_guard"
    assert "SECRET" not in caplog.text and "SECRET" not in response.text


def test_read_role_cannot_trigger_merge(client):
    from tests.conftest import READ

    _, _, ids, _ = setup(client)
    response = client.post(
        "/api/v1/capabilities/merge",
        headers=READ,
        json={
            "source_claim_id": ids[0],
            "target_claim_id": ids[1],
            "source_revision": 1,
            "target_revision": 1,
            "relation_type": "same_as",
            "human_confirmed": True,
            "comment": "普通用户不能合并",
        },
    )
    assert response.status_code == 403
    assert (
        client.get("/api/v1/admin/capabilities/" + ids[0], headers=ADMIN).json()["latest_revision"]
        is None
    )
