import pytest

from app.core.security import AppError
from app.db.models import KnowledgeJob
from app.jobs.status_machine import transition


def test_ordered_stages_and_terminal_guard():
    job = KnowledgeJob(stage="uploaded", status="queued")
    with pytest.raises(AppError):
        transition(job, "published")
    transition(job, "validating")
    job.status = "running"
    transition(job, "extracted")
    job.status = "succeeded"
    with pytest.raises(AppError):
        transition(job, "failed")
