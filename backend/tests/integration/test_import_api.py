import json
from datetime import timedelta

import pytest
from redis.exceptions import ConnectionError
from sqlalchemy import select

from app.db.models import AuditEvent, Category, KnowledgeJob, Snapshot, now
from app.db.session import session_factory
from app.jobs.handlers import import_source, mark_failed
from app.jobs.queue import dispatch_pending
from tests.conftest import ADMIN, READ, make_zip


def category(client, code="waf"):
    response = client.post(
        "/api/v1/categories", headers=ADMIN, json={"code": code, "name": "测试产品"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, category_id, data=None, headers=None, name="source.zip", mime="application/zip"):
    return client.post(
        f"/api/v1/categories/{category_id}/source",
        headers=headers or ADMIN,
        files={"file": (name, data if data is not None else make_zip(), mime)},
    )


def execute(job_id):
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, job_id)
        job.dispatch_token = "test-dispatch"
    import_source(job_id, "test-dispatch")


def test_authentication_and_roles(client):
    for headers, expected in [({}, 401), ({"Authorization": "Bearer invalid"}, 401), (READ, 403)]:
        response = client.post(
            "/api/v1/categories", headers=headers, json={"code": "waf", "name": "WAF"}
        )
        assert response.status_code == expected
        assert {"error_code", "message", "correlation_id"} == response.json().keys()
        assert response.headers["x-correlation-id"] == response.json()["correlation_id"]
    assert client.get("/api/v1/categories", headers=READ).status_code == 200
    assert client.get("/api/v1/jobs/unknown", headers=READ).status_code == 403


def test_category_conflict_and_validation(client):
    category(client)
    assert (
        client.post(
            "/api/v1/categories", headers=ADMIN, json={"code": "waf", "name": "重复"}
        ).status_code
        == 409
    )
    response = client.post(
        "/api/v1/categories", headers=ADMIN, json={"code": "../bad", "name": "x"}
    )
    assert response.status_code == 422
    assert "../bad" not in response.text


def test_real_import_handler_no_model_and_idempotency(client, environment):
    cid = category(client)
    accepted = upload(client, cid)
    assert accepted.status_code == 202
    jid = accepted.json()["job_id"]
    assert client.get(f"/api/v1/jobs/{jid}", headers=ADMIN).json()["status"] == "queued"
    assert not environment.ingestion_root.exists()
    execute(jid)
    first = client.get(f"/api/v1/jobs/{jid}", headers=ADMIN).json()
    assert first["status"] == "succeeded" and first["stage"] == "extracted"
    assert first["metrics_json"]["file_count"] == 1
    assert first["metrics_json"]["model_requests"] == 0
    assert first["metrics_json"]["model_mode"] == "NOT_USED"
    import_source(jid, "test-dispatch")
    assert client.get(f"/api/v1/jobs/{jid}", headers=ADMIN).json() == first
    manifest = json.loads(
        (environment.ingestion_root / accepted.json()["snapshot_id"] / "manifest.json").read_text()
    )
    assert manifest["files"][0]["path"] == "main.py"
    with session_factory()() as db:
        assert db.get(Category, cid).active_snapshot_id is None
        assert len(db.scalars(select(AuditEvent)).all()) == 2
    assert client.get("/api/v1/categories", headers=READ).json()[0]["knowledge_ready"] is False
    assert "archive_key" not in first and "manifest_key" not in first


def test_conflict_cleans_unused_archive(client, environment):
    cid = category(client)
    assert upload(client, cid).status_code == 202
    assert upload(client, cid).status_code == 409
    assert len(list(environment.archive_root.iterdir())) == 1


@pytest.mark.parametrize(
    "data,name,mime,status",
    [
        (b"not zip", "a.zip", "application/zip", 422),
        (b"", "a.zip", "application/zip", 422),
        (None, "../a.zip", "application/zip", 422),
        (None, "a.txt", "application/zip", 422),
        (None, "a.zip", "image/png", 422),
    ],
)
def test_invalid_upload(client, environment, data, name, mime, status):
    response = upload(client, category(client), data=data, name=name, mime=mime)
    assert response.status_code == status
    assert not environment.archive_root.exists() or list(environment.archive_root.iterdir()) == []


def test_upload_file_limit(client, environment):
    object.__setattr__(environment, "max_upload_bytes", 10)
    response = upload(client, category(client))
    assert response.status_code == 413
    assert list(environment.archive_root.iterdir()) == []


def test_malicious_import_fails_and_retry_is_explicit(client, environment):
    cid = category(client)
    accepted = upload(client, cid, make_zip([("../escape", "bad")])).json()
    execute(accepted["job_id"])
    result = client.get(f"/api/v1/jobs/{accepted['job_id']}", headers=ADMIN).json()
    assert result["status"] == "failed" and result["error_code"] == "UNSAFE_PATH"
    assert not (environment.ingestion_root.parent / "escape").exists()
    response = client.post(f"/api/v1/jobs/{accepted['job_id']}/retry", headers=ADMIN)
    assert response.status_code == 202
    assert response.json()["attempt"] == 2
    assert response.json()["status"] == "queued"
    assert client.post(f"/api/v1/jobs/{accepted['job_id']}/retry", headers=ADMIN).status_code == 409
    execute(accepted["job_id"])
    assert (
        client.get(f"/api/v1/jobs/{accepted['job_id']}", headers=ADMIN).json()["error_code"]
        == "UNSAFE_PATH"
    )


def test_archive_tampering_does_not_change_active_pointer(client, environment):
    cid = category(client)
    accepted = upload(client, cid).json()
    with session_factory()() as db:
        snapshot = db.get(Snapshot, accepted["snapshot_id"])
        path = environment.archive_root / snapshot.archive_key
    path.chmod(0o640)
    path.write_bytes(b"tamper")
    execute(accepted["job_id"])
    response = client.get(f"/api/v1/jobs/{accepted['job_id']}", headers=ADMIN)
    assert response.json()["error_code"] == "ARCHIVE_HASH_MISMATCH"
    with session_factory()() as db:
        assert db.get(Category, cid).active_snapshot_id is None


def test_job_failure_callback_is_scoped_and_idempotent(client):
    accepted = upload(client, category(client)).json()
    jid = accepted["job_id"]
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "right-token"
    mark_failed(jid, "wrong-token", "SHOULD_NOT_APPLY")
    assert client.get(f"/api/v1/jobs/{jid}", headers=ADMIN).json()["status"] == "queued"
    mark_failed(jid, "right-token", "WORKER_INTERRUPTED")
    assert client.get(f"/api/v1/jobs/{jid}", headers=ADMIN).json()["status"] == "failed"


def test_queue_outage_preserves_durable_task(client, monkeypatch):
    jid = upload(client, category(client)).json()["job_id"]

    class UnavailableQueue:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, *args, **kwargs):
            raise ConnectionError("测试替身：Redis 不可用")

    monkeypatch.setattr("app.jobs.queue.Queue", UnavailableQueue)
    with pytest.raises(ConnectionError):
        dispatch_pending()
    with session_factory()() as db:
        job = db.get(KnowledgeJob, jid)
        assert job.status == "queued"
        assert job.dispatch_token is None


def test_outbox_dispatch_and_worker_lost_recovery(client, monkeypatch, environment):
    jid = upload(client, category(client)).json()["job_id"]
    calls = []

    class CapturingQueue:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr("app.jobs.queue.Queue", CapturingQueue)
    assert dispatch_pending() == 1
    assert dispatch_pending() == 0
    fn, job_id, token = calls[0][0]
    assert job_id == jid
    fn(job_id, token)
    assert client.get(f"/api/v1/jobs/{jid}", headers=ADMIN).json()["stage"] == "extracted"
    cid2 = category(client, "ndr")
    jid2 = upload(client, cid2).json()["job_id"]
    with session_factory().begin() as db:
        job = db.get(KnowledgeJob, jid2)
        job.status, job.stage = "running", "validating"
        job.started_at = now() - timedelta(seconds=environment.job_timeout_seconds + 120)
    dispatch_pending()
    assert client.get(f"/api/v1/jobs/{jid2}", headers=ADMIN).json()["error_code"] == "WORKER_LOST"


def test_product_scope_and_unknown_routes(client):
    cid1, cid2 = category(client, "waf"), category(client, "ndr")
    first = upload(client, cid1)
    second = upload(client, cid2)
    assert first.status_code == second.status_code == 202
    assert first.json()["snapshot_id"] != second.json()["snapshot_id"]
    assert upload(client, "unknown").status_code == 404
    assert client.get("/api/v1/jobs/unknown", headers=ADMIN).status_code == 404
    assert client.get("/api/v1/capabilities", headers=READ).status_code == 404
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503


def test_error_logs_do_not_contain_secrets(client, caplog):
    with caplog.at_level("INFO", logger="knowledge.audit"):
        response = client.get("/api/v1/jobs/missing?private=must-not-log", headers=ADMIN)
    assert response.status_code == 404
    assert "must-not-log" not in caplog.text
    assert "a" * 48 not in caplog.text
