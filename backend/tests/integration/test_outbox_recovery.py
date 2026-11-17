from datetime import timedelta

import pytest

from app.db.models import KnowledgeJob, now
from app.db.session import session_factory
from app.jobs import queue
from app.jobs.handlers import import_source
from tests.conftest import ADMIN
from tests.integration.test_import_api import category, upload


def get_job(jid):
    with session_factory()() as db:
        return db.get(KnowledgeJob, jid)


def expire_lease(jid):
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatched_at = now() - timedelta(seconds=1000)


def test_worker_consumes_inside_enqueue_before_return(client, monkeypatch, environment):
    ids = upload(client, category(client)).json()
    jid = ids["job_id"]

    class ImmediateQueue:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, fn, business_job_id, token, **kwargs):
            # 独立会话必须已能看到派发令牌，且调度事务已释放锁。
            persisted = get_job(business_job_id)
            assert persisted.dispatch_token == token
            assert persisted.dispatched_at is not None
            fn(business_job_id, token)
            assert get_job(business_job_id).status == "succeeded"

    monkeypatch.setattr(queue, "Queue", ImmediateQueue)
    assert queue.dispatch_pending() == 1
    assert get_job(jid).status == "succeeded"
    assert get_job(jid).dispatch_enqueued_at is not None
    assert len(list(environment.ingestion_root.glob("*/manifest.json"))) == 1


def test_crash_after_claim_before_enqueue_reuses_message_identity(client, monkeypatch):
    jid = upload(client, category(client)).json()["job_id"]
    claims = queue.claim_pending()
    token = claims[0][1]
    assert get_job(jid).dispatch_token == token
    assert queue.claim_pending() == []
    expire_lease(jid)
    sent = []

    class CapturingQueue:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, fn, business_job_id, dispatch_token, **kwargs):
            sent.append(kwargs["job_id"])
            assert dispatch_token == token
            fn(business_job_id, dispatch_token)

    monkeypatch.setattr(queue, "Queue", CapturingQueue)
    assert queue.dispatch_pending() == 1
    assert sent == [f"{jid}-{token}"]
    assert get_job(jid).status == "succeeded"


def test_crash_after_enqueue_before_ack_and_duplicate_delivery(client, monkeypatch, environment):
    jid = upload(client, category(client)).json()["job_id"]
    messages = []

    class CapturingQueue:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, *args, **kwargs):
            messages.append((args, kwargs))

    monkeypatch.setattr(queue, "Queue", CapturingQueue)
    real_record = queue.record_delivery

    def crash(*args, **kwargs):
        raise SystemExit("测试故障：投递成功、记录完成前退出")

    monkeypatch.setattr(queue, "record_delivery", crash)
    with pytest.raises(SystemExit):
        queue.dispatch_pending()
    token = get_job(jid).dispatch_token
    expire_lease(jid)
    monkeypatch.setattr(queue, "record_delivery", real_record)
    assert queue.dispatch_pending() == 1
    assert messages[0][1]["job_id"] == messages[1][1]["job_id"] == f"{jid}-{token}"
    for (fn, job_id, msg_token), _ in messages:
        fn(job_id, msg_token)
    assert get_job(jid).status == "succeeded"
    assert len(list(environment.ingestion_root.glob("*/manifest.json"))) == 1
    assert queue.dispatch_pending() == 0


def test_enqueue_delivered_then_connection_error_is_safe_to_retry(client, monkeypatch):
    jid = upload(client, category(client)).json()["job_id"]
    messages = []

    class UncertainQueue:
        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, *args, **kwargs):
            messages.append((args, kwargs))
            raise ConnectionError("测试故障：已发送但响应丢失")

    monkeypatch.setattr(queue, "Queue", UncertainQueue)
    with pytest.raises(ConnectionError):
        queue.dispatch_pending()
    first = get_job(jid)
    assert first.dispatch_token and first.dispatched_at is None
    with pytest.raises(ConnectionError):
        queue.dispatch_pending()
    assert messages[0][1]["job_id"] == messages[1][1]["job_id"]
    for (fn, job_id, token), _ in messages:
        fn(job_id, token)
    assert get_job(jid).status == "succeeded"


def test_extracted_before_database_commit_crash_recovers_same_snapshot(
    client, monkeypatch, environment
):
    ids = upload(client, category(client)).json()
    jid = ids["job_id"]
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).dispatch_token = "first-attempt"
    from app.ingestion.content_store import extract_snapshot
    from app.jobs import handlers

    def extract_then_die(*args):
        extract_snapshot(*args)
        raise SystemExit("测试故障：目录已提交，任务结果未提交")

    monkeypatch.setattr(handlers, "extract_snapshot", extract_then_die)
    with pytest.raises(SystemExit):
        import_source(jid, "first-attempt")
    assert get_job(jid).status == "running"
    with session_factory().begin() as db:
        db.get(KnowledgeJob, jid).started_at = now() - timedelta(seconds=1000)
    queue.claim_pending()
    assert get_job(jid).error_code == "WORKER_LOST"
    assert client.post(f"/api/v1/jobs/{jid}/retry", headers=ADMIN).status_code == 202
    claim = queue.claim_pending()[0]
    monkeypatch.setattr(handlers, "extract_snapshot", extract_snapshot)
    import_source(claim[0], claim[1])
    assert get_job(jid).status == "succeeded"
    assert get_job(jid).attempt == 2
    assert len(list(environment.ingestion_root.iterdir())) == 1
    assert (environment.ingestion_root / ids["snapshot_id"] / "manifest.json").exists()


def test_database_commit_failure_never_enqueues(client, monkeypatch):
    from contextlib import contextmanager

    upload(client, category(client))
    # factory.begin() 使用事务上下文；模拟提交阶段失败且回滚后不返回 claim。
    real_factory = queue.session_factory

    class FailingFactory:
        @contextmanager
        def begin(self):
            with real_factory()() as db:
                with db.begin():
                    yield db
                    raise RuntimeError("测试故障：数据库提交失败")

    calls = []
    monkeypatch.setattr(queue, "session_factory", lambda: FailingFactory())
    monkeypatch.setattr(queue, "Queue", lambda *a, **k: calls.append("不应创建队列"))
    with pytest.raises(RuntimeError, match="数据库提交失败"):
        queue.dispatch_pending()
    assert calls == []
