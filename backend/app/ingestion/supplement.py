"""运维入口：仅在未发布、已完成索引的暂存快照上原子补建。"""

import argparse
import json
from collections import Counter

from sqlalchemy import select

from app.api.indexing import staging
from app.core.config import get_settings
from app.db.index_models import SourceFile
from app.db.models import AuditEvent, Category
from app.db.session import session_factory
from app.ingestion.indexer import index_file
from app.ingestion.text import source_path


def supplement(db, sid, category_id, expected_sha):
    settings = get_settings()
    category = db.scalar(select(Category).where(Category.id == category_id).with_for_update())
    snap = staging(db, sid, category_id)
    if (
        not category
        or category.active_snapshot_id == sid
        or snap.source_sha256 != expected_sha
        or snap.index_status != "indexed"
    ):
        raise ValueError("STAGING_INDEX_REQUIRED")
    manifest = json.loads((settings.source_root / sid / "manifest.json").read_bytes())
    if manifest["snapshot_id"] != sid or manifest["source_sha256"] != expected_sha:
        raise ValueError("MANIFEST_MISMATCH")
    files = {f.path: f for f in db.scalars(select(SourceFile).where(SourceFile.snapshot_id == sid))}
    result = Counter()
    for record in manifest["files"] + manifest["excluded"]:
        f = files.pop(record["path"], None)
        if (
            not f
            or (f.sha256, f.size) != (record["sha256"], record["size"])
            or bool(record.get("reason")) != (f.parse_status == "excluded")
        ):
            raise ValueError("SOURCE_MANIFEST_MISMATCH")
        if not record.get("reason"):
            path = source_path(settings.source_root, sid, f.path)
            if path.stat().st_size != f.size:
                raise ValueError("SOURCE_SIZE_MISMATCH")
            import hashlib

            with path.open("rb") as reader:
                if hashlib.file_digest(reader, "sha256").hexdigest() != f.sha256:
                    raise ValueError("SOURCE_HASH_MISMATCH")
        if f.parse_status == "unsupported" and not f.sensitive:
            result[index_file(sid, record, settings, transaction=db)] += 1
    if files:
        raise ValueError("MANIFEST_INCOMPLETE")
    db.add(
        AuditEvent(
            principal_id="local-admin",
            action="index.supplement",
            resource_id=sid,
            correlation_id=sid,
            details_json=dict(result),
        )
    )
    return dict(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for field in ("snapshot", "category", "sha256"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    try:
        with session_factory().begin() as session:
            result = supplement(session, args.snapshot, args.category, args.sha256)
    except Exception as exc:
        raise SystemExit("SUPPLEMENT_ROLLED_BACK:" + type(exc).__name__) from None
    print(json.dumps(result, sort_keys=True))
