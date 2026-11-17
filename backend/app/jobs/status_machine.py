from app.core.security import AppError

STAGES = [
    "uploaded",
    "validating",
    "extracted",
    "indexed",
    "module_planning",
    "investigating",
    "merging",
    "validating_evidence",
    "review_ready",
    "published",
]


def transition(job, stage: str):
    if job.status not in {"queued", "running"}:
        raise AppError(409, "INVALID_TRANSITION", "终态任务不能直接变更")
    if stage in {"failed", "partial", "cancelled"}:
        job.stage = stage
        job.status = stage
        return
    if (
        stage not in STAGES
        or job.stage not in STAGES
        or STAGES.index(stage) != STAGES.index(job.stage) + 1
    ):
        raise AppError(409, "INVALID_TRANSITION", "任务阶段转换不合法")
    job.stage = stage
