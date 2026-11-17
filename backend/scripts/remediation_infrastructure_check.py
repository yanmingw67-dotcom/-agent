"""P1.1 整改专用：真实 PostgreSQL/Redis/RQ + 可控故障注入，不覆盖旧日志。"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker

from app.core.config import get_settings
from app.db.models import KnowledgeJob, now
from app.db.session import get_engine, session_factory
from app.jobs import queue
from app.main import create_app


def main():
    env = dotenv_values(".env")
    os.environ.update(
        {
            "ADMIN_TOKEN": env["ADMIN_TOKEN"],
            "READ_TOKEN": env["READ_TOKEN"],
            "DATABASE_URL": f"postgresql+psycopg://knowledge:{env['POSTGRES_PASSWORD']}@127.0.0.1:15432/knowledge",
            "REDIS_URL": "redis://127.0.0.1:16379/0",
            "ARCHIVE_ROOT": str(Path("storage/remediation/archives").resolve()),
            "INGESTION_ROOT": str(Path("storage/remediation/ingestion").resolve()),
            "SOURCE_ROOT": str(Path("storage/remediation/ingestion").resolve()),
            "QUEUE_NAME": "p11-remediation-" + uuid4().hex[:8],
        }
    )
    get_settings.cache_clear()
    get_engine.cache_clear()
    settings = get_settings()
    migration = Config("alembic.ini")
    command.upgrade(migration, "head")
    command.check(migration)
    headers = {"Authorization": f"Bearer {env['ADMIN_TOKEN']}"}
    data = Path("tests/fixtures/demo_waf_source.zip").read_bytes()
    report = {"mode": "REAL_POSTGRES_REDIS_RQ_WITH_EXPLICIT_FAULT_INJECTION", "checks": {}}

    def state(jid):
        with session_factory()() as db:
            return db.get(KnowledgeJob, jid)

    def consume():
        SimpleWorker([settings.queue_name], connection=queue.connection()).work(
            burst=True, logging_level="WARNING"
        )

    with TestClient(create_app()) as client:

        def category():
            response = client.post(
                "/api/v1/categories",
                headers=headers,
                json={"code": "remediation-" + uuid4().hex[:12], "name": "整改人工验证"},
            )
            assert response.status_code == 201, response.text
            return response.json()["id"]

        def upload(cid):
            return client.post(
                f"/api/v1/categories/{cid}/source",
                headers=headers,
                files={"file": ("demo.zip", data, "application/zip")},
            )

        cid = category()
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: upload(cid), range(2)))
        assert sorted(r.status_code for r in responses) == [202, 409]
        ids = next(r.json() for r in responses if r.status_code == 202)
        report["checks"]["concurrent_same_category_upload"] = [202, 409]

        class ImmediateRealQueue(Queue):
            def enqueue(self, *args, **kwargs):
                persisted = state(args[1])
                assert persisted.dispatch_token == args[2]
                message = super().enqueue(*args, **kwargs)
                # 真 Redis 已入队；在 enqueue 返回前消费，复现审查方指定时序。
                consume()
                assert state(args[1]).status == "succeeded"
                return message

        queue.Queue = ImmediateRealQueue
        try:
            assert queue.dispatch_pending() == 1
        finally:
            queue.Queue = Queue
        assert state(ids["job_id"]).dispatch_enqueued_at is not None
        report["checks"]["real_worker_before_enqueue_return"] = "passed"
        report["immediate_job_id"] = ids["job_id"]

        # 已投递、确认前崩溃：不删除 Redis 消息，模拟租约过期并投递相同 ID。
        ids2 = upload(category()).json()
        real_record = queue.record_delivery

        def crash_before_ack(*args, **kwargs):
            raise SystemExit("测试故障注入：Redis 投递后、回写前退出")

        queue.record_delivery = crash_before_ack
        try:
            try:
                queue.dispatch_pending()
            except SystemExit:
                pass
            else:
                raise AssertionError("应注入投递后退出")
        finally:
            queue.record_delivery = real_record
        first_token = state(ids2["job_id"]).dispatch_token
        with session_factory().begin() as db:
            db.get(KnowledgeJob, ids2["job_id"]).dispatched_at = now() - timedelta(seconds=1000)
        assert queue.dispatch_pending() == 1
        assert state(ids2["job_id"]).dispatch_token == first_token
        redis_queue = Queue(settings.queue_name, connection=queue.connection())
        message_ids = redis_queue.job_ids
        assert len(message_ids) == 2 and message_ids[0] == message_ids[1]
        consume()
        assert state(ids2["job_id"]).status == "succeeded"
        assert redis_queue.count == 0
        assert len(list(settings.ingestion_root.glob(f"{ids2['snapshot_id']}/manifest.json"))) == 1
        report["checks"]["real_duplicate_message_after_ack_crash"] = "passed"

        # 从真实 Redis 连接错误恢复：临时使用不可达端口，不修改容器、不清库。
        ids3 = upload(category()).json()
        os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"
        get_settings.cache_clear()
        from redis.exceptions import ConnectionError

        try:
            queue.dispatch_pending()
        except ConnectionError:
            pass
        else:
            raise AssertionError("不可达 Redis 应拒绝投递")
        assert state(ids3["job_id"]).status == "queued"
        assert state(ids3["job_id"]).dispatched_at is None
        os.environ["REDIS_URL"] = "redis://127.0.0.1:16379/0"
        get_settings.cache_clear()
        assert queue.dispatch_pending() == 1
        consume()
        assert state(ids3["job_id"]).status == "succeeded"
        report["checks"]["real_redis_connection_failure_recovery"] = "passed"

        report["checks"]["one_manifest_per_snapshot"] = (
            len(list(settings.ingestion_root.glob("*/manifest.json"))) == 3
        )
    report["migration"] = "0002 / alembic check passed"
    report["model_requests"] = report["tokens"] = report["model_cost"] = 0
    Path("reports/remediation/infrastructure.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
