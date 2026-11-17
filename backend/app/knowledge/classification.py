"""外发分类只信持久化来源；人工例外限于随代码审阅的精确内容清单。"""

import json
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Document, SourceFile
from app.db.models import Snapshot
from app.ingestion.text import digest, source_path


def classification(db, snapshot_id):
    snapshot = db.get(Snapshot, snapshot_id)
    if not snapshot or snapshot.data_classification != "synthetic_public":
        return "enterprise"
    docs = db.scalars(select(Document).where(Document.snapshot_id == snapshot_id)).all()
    if any(d.data_classification != "synthetic_public" for d in docs):
        return "enterprise"
    return "synthetic_public"


def approve_fixture(db, snapshot):
    allowed = json.loads(Path(__file__).with_name("public_fixture_manifest.json").read_text())
    files = db.scalars(select(SourceFile).where(SourceFile.snapshot_id == snapshot.id)).all()
    actual = {f.path: f.sha256 for f in files}
    if actual != allowed["files"]:
        raise AppError(
            403, "PUBLIC_FIXTURE_MISMATCH", "仅允许服务端固定人工公开样例，不接受任意降级"
        )
    # 不只信索引，重新核对实际只读源码，防止索引后文件变化。
    for f in files:
        source = source_path(get_settings().source_root, snapshot.id, f.path)
        if digest(source.read_bytes()) != f.sha256:
            raise AppError(409, "SOURCE_HASH_MISMATCH", "人工来源已改变")
    docs = db.scalars(select(Document).where(Document.snapshot_id == snapshot.id)).all()
    for d in docs:
        if (
            d.sha256 != allowed["manual_sha256"]
            or digest((get_settings().document_store / d.storage_key).read_bytes()) != d.sha256
        ):
            raise AppError(403, "PUBLIC_DOCUMENT_MISMATCH", "文档不属于固定人工公开样例")
    snapshot.data_classification = "synthetic_public"
    for d in docs:
        d.data_classification = "synthetic_public"
