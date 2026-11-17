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


def claim_pending():
    settings = get_settings()
    indexer = settings.worker_kind == "index"
    builder = settings.worker_kind == "builder"
    types = (
        ["knowledge_build"]
        if builder
        else (["source_index", "document_index"] if indexer else ["source_import"])
    )
    timeout = (
        settings.builder_job_timeout_seconds
        if builder
        else (settings.index_job_timeout_seconds if indexer else settings.job_timeout_seconds)
    )
    threshold = now() - timedelta(seconds=timeout + 60)
    claims = []
    with session_factory().begin() as db:
        stale = db.scalars(
            select(KnowledgeJob)
            .where(
                KnowledgeJob.status == "running",
                KnowledgeJob.started_at < threshold,
                KnowledgeJob.job_type.in_(types),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            job.status = job.stage = "failed"
            job.error_code = "WORKER_LOST"
            job.finished_at = now()
            snapshot = db.get(Snapshot, job.snapshot_id)
            if job.job_type == "source_import":
                snapshot.status = "failed"
            elif job.job_type == "source_index":
                snapshot.index_status = "failed"
        pending = db.scalars(
            select(KnowledgeJob)
            .where(
                KnowledgeJob.status == "queued",
                KnowledgeJob.job_type.in_(types),
                or_(KnowledgeJob.dispatched_at.is_(None), KnowledgeJob.dispatched_at < threshold),
            )
            .order_by(KnowledgeJob.created_at)
            .limit(50)
            .with_for_update(skip_locked=True)
        ).all()
        for job in pending:
            # 同一业务尝试的令牌/消息 ID 稳定；显式 retry 才会更换令牌。
            job.dispatch_token = job.dispatch_token or str(uuid4())
            job.dispatched_at = now()
            job.dispatch_enqueued_at = None
            claims.append((job.id, job.dispatch_token, job.dispatched_at))
    # 只有事务提交成功后，调用者才可能向 Redis 发布。
    return claims


def record_delivery(job_id, token, claimed_at, delivered):
    with session_factory().begin() as db:
        job = db.scalar(
            select(KnowledgeJob)
            .where(
                KnowledgeJob.id == job_id,
                KnowledgeJob.dispatch_token == token,
                KnowledgeJob.dispatched_at == claimed_at,
            )
            .with_for_update()
        )
        if not job:
            return
        if delivered:
            job.dispatch_enqueued_at = now()
        elif job.status == "queued":
            # 投递结果未知也安全重发：保持消息 ID，依赖处理器数据库领取去重。
            job.dispatched_at = None
            job.dispatch_enqueued_at = None


def dispatch_pending():
    settings = get_settings()
    claims = claim_pending()
    if not claims:
        return 0
    indexer = settings.worker_kind == "index"
    if indexer:
        from app.ingestion.indexer import run_index
    builder = settings.worker_kind == "builder"
    if builder:
        from app.agents.runtime import run_builder
    handler = run_builder if builder else (run_index if indexer else import_source)
    queue = Queue(
        settings.builder_queue_name
        if builder
        else (settings.index_queue_name if indexer else settings.queue_name),
        connection=connection(),
    )
    for job_id, token, claimed_at in claims:
        try:
            queue.enqueue(
                handler,
                job_id,
                token,
                job_id=f"{job_id}-{token}",
                job_timeout=settings.builder_job_timeout_seconds
                if builder
                else (
                    settings.index_job_timeout_seconds if indexer else settings.job_timeout_seconds
                ),
                result_ttl=3600,
                failure_ttl=86400,
                on_failure=rq_failure,
            )
        except Exception:
            record_delivery(job_id, token, claimed_at, delivered=False)
            raise
        record_delivery(job_id, token, claimed_at, delivered=True)
    return len(claims)
