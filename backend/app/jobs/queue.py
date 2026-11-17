from datetime import timedelta
from uuid import uuid4

from redis import Redis
from rq import Queue
from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db.models import KnowledgeJob, Snapshot, now
from app.db.session import session_factory
from app.jobs.handlers import import_source, rq_failure


def connection():
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=5)


def dispatch_pending():
    settings = get_settings()
    threshold = now() - timedelta(seconds=settings.job_timeout_seconds + 60)
    count = 0
    # 数据库任务表同时作为 outbox。先提交上传，再可靠投递；Redis 停机不会丢上传任务。
    with session_factory().begin() as db:
        stale = db.scalars(
            select(KnowledgeJob)
            .where(KnowledgeJob.status == "running", KnowledgeJob.started_at < threshold)
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            job.status = job.stage = "failed"
            job.error_code = "WORKER_LOST"
            job.finished_at = now()
            db.get(Snapshot, job.snapshot_id).status = "failed"
        pending = db.scalars(
            select(KnowledgeJob)
            .where(
                KnowledgeJob.status == "queued",
                or_(KnowledgeJob.dispatched_at.is_(None), KnowledgeJob.dispatched_at < threshold),
            )
            .order_by(KnowledgeJob.created_at)
            .limit(50)
            .with_for_update(skip_locked=True)
        ).all()
        queue = Queue(settings.queue_name, connection=connection())
        for job in pending:
            token = str(uuid4())
            queue.enqueue(
                import_source,
                job.id,
                token,
                job_id=f"{job.id}-{token}",
                job_timeout=settings.job_timeout_seconds,
                result_ttl=3600,
                failure_ttl=86400,
                on_failure=rq_failure,
            )
            job.dispatch_token = token
            job.dispatched_at = now()
            count += 1
    return count
