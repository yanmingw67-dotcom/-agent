"""跨进程容量预留；异常退出的预留不自动删除，宁可拒绝新导入而非超额。"""

import fcntl
import json
import os
import shutil
from contextlib import contextmanager

from app.core.security import AppError


def tree_bytes(root):
    if not root.exists():
        return 0
    total = 0
    for parent, _, names in os.walk(root, followlinks=False):
        for name in names:
            path = root.__class__(parent) / name
            if not path.is_symlink():
                try:
                    total += path.stat().st_size
                except FileNotFoundError:
                    pass
    return total


@contextmanager
def ledger(settings):
    root = settings.control_store
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    with (root / "capacity.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "reservations.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        yield state
        temp = root / "reservations.tmp"
        temp.write_text(json.dumps(state))
        temp.chmod(0o640)
        temp.replace(path)


def reserve(settings, key, amount):
    roots = {settings.archive_root, settings.source_root, settings.document_store}
    with ledger(settings) as state:
        used = sum(tree_bytes(root) for root in roots)
        reserved = sum(v["bytes"] for k, v in state.items() if k != key)
        if used + reserved + amount > settings.storage_quota_bytes:
            raise AppError(413, "STORAGE_QUOTA_EXCEEDED", "受控存储容量不足，需管理员检查配额")
        for root in roots:
            existing = root
            while not existing.exists():
                existing = existing.parent
            if (
                shutil.disk_usage(existing).free
                < amount + reserved + settings.storage_min_free_bytes
            ):
                raise AppError(507, "STORAGE_LOW_SPACE", "磁盘剩余空间不足")
        import time

        state[key] = {"bytes": amount, "created_at": time.time()}


def release(settings, key):
    with ledger(settings) as state:
        state.pop(key, None)


def scan(settings, db, limit=200):
    from sqlalchemy import select

    from app.db.index_models import Document
    from app.db.models import KnowledgeJob, Snapshot

    # 报告只读，绝不删除；数据库关联优先于时间判断。
    snapshot_rows = list(db.scalars(select(Snapshot)))
    document_rows = list(db.scalars(select(Document)))
    archives = {row.archive_key for row in snapshot_rows}
    snapshots = {row.id for row in snapshot_rows}
    documents = {row.storage_key for row in document_rows}
    active = set(
        db.scalars(
            select(KnowledgeJob.snapshot_id).where(KnowledgeJob.status.in_(["queued", "running"]))
        )
    )
    protected_keys = (
        active
        | {row.archive_key for row in snapshot_rows if row.id in active}
        | {row.storage_key for row in document_rows if row.snapshot_id in active}
    )
    rows, total = [], 0
    for kind, root, referenced in [
        ("archive", settings.archive_root, archives),
        ("snapshot", settings.source_root, snapshots),
        ("document", settings.document_store, documents),
    ]:
        if root.exists():
            for entry in root.iterdir():
                total += 1
                if len(rows) < limit:
                    rows.append(
                        {
                            "kind": kind,
                            "key": entry.name,
                            "referenced": entry.name in referenced,
                            "inflight_protected": entry.name in protected_keys,
                            "orphan_candidate": entry.name not in referenced
                            and entry.name not in protected_keys,
                            "action": "report_only",
                        }
                    )
    with ledger(settings) as state:
        reservations = dict(state)
    return {
        "quota_bytes": settings.storage_quota_bytes,
        "used_bytes": sum(
            tree_bytes(r)
            for r in {settings.archive_root, settings.source_root, settings.document_store}
        ),
        "reserved_bytes": sum(v["bytes"] for v in reservations.values()),
        "reservation_count": len(reservations),
        "reservations": [
            {"key": key, **value} for key, value in list(reservations.items())[:limit]
        ],
        "entries": rows,
        "truncated": total > limit,
        "scanned_entries": total,
        "automatic_deletion": False,
    }
