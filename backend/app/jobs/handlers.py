import time

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import AppError, ArchiveRejected
from app.db.models import KnowledgeJob, Snapshot, now
from app.db.session import session_factory
from app.ingestion.capacity import release, reserve
from app.ingestion.content_store import extract_snapshot
from app.jobs.status_machine import transition


def import_source(job_id: str, dispatch_token: str):
    settings = get_settings()
    factory = session_factory()
    started = time.monotonic()
    with factory.begin() as db:
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if not job or job.status != "queued" or job.dispatch_token != dispatch_token:
            return
        snapshot = db.get(Snapshot, job.snapshot_id)
        transition(job, "validating")
        job.status = "running"
        job.started_at = now()
        snapshot.status = "validating"
        archive_key, sha256, snapshot_id = snapshot.archive_key, snapshot.source_sha256, snapshot.id
    error = None
    manifest = None
    try:
        reserve(settings, archive_key, settings.max_total_bytes + settings.max_entries * 2048)
        key, manifest = extract_snapshot(archive_key, sha256, snapshot_id, settings)
    except AppError as exc:
        error = exc.error_code
    except ArchiveRejected as exc:
        error = exc.code
    except Exception:
        # 只保存稳定错误码，不将可能包含私密路径/源码的异常传给 API 或日志。
        error = "IMPORT_INTERNAL_ERROR"
    with factory.begin() as db:
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if job.status != "running" or job.dispatch_token != dispatch_token:
            return
        snapshot = db.get(Snapshot, snapshot_id)
        job.finished_at = now()
        job.metrics_json = {
            "duration_ms": round((time.monotonic() - started) * 1000),
            "model_requests": 0,
            "tool_calls": 0,
            "tokens": 0,
            "model_cost": 0,
            "model_mode": "NOT_USED",
            "index_status": "NOT_IMPLEMENTED",
            **(
                {
                    "file_count": len(manifest["files"]),
                    "excluded_count": len(manifest["excluded"]),
                    "total_bytes": manifest["total_bytes"],
                    "sensitive_files": manifest["sensitive_files"],
                }
                if manifest is not None
                else {}
            ),
        }
        if error:
            transition(job, "failed")
            snapshot.status = "failed"
            job.error_code = error
        else:
            transition(job, "extracted")
            job.status = "succeeded"
            snapshot.status = "extracted"
            snapshot.manifest_key = key
    release(settings, archive_key)


def rq_failure(job, connection, exc_type, exc_value, traceback):
    mark_failed(job.args[0], job.args[1], "WORKER_INTERRUPTED")


def mark_failed(job_id, token, code):
    with session_factory().begin() as db:
        job = db.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if job and job.dispatch_token == token and job.status in {"queued", "running"}:
            job.stage = "failed"
            job.status = "failed"
            job.error_code = code
            job.finished_at = now()
            snapshot = db.get(Snapshot, job.snapshot_id)
            if job.job_type == "source_import":
                snapshot.status = "failed"
            else:
                snapshot.index_status = "failed"
