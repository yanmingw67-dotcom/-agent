"""P1.6 文档暂存更新：受控派生新快照，绝不改动已发布快照。"""

import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import AppError
from app.db.index_models import Document, DocumentSegment, SourceFile
from app.db.knowledge_models import Evidence
from app.db.models import AuditEvent, Category, KnowledgeJob, Snapshot
from app.ingestion.capacity import release, reserve
from app.ingestion.content_store import extract_snapshot, hash_file
from app.ingestion.text import digest, source_path


def _copy_verified(source: Path, destination: Path, expected_sha256: str, expected_size: int):
    if source.stat().st_size != expected_size:
        raise AppError(409, "DERIVATION_SOURCE_HASH_MISMATCH", "受控来源文件大小已改变")
    if hash_file(source) != expected_sha256:
        raise AppError(409, "DERIVATION_SOURCE_HASH_MISMATCH", "受控来源文件哈希已改变")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with source.open("rb") as reader, destination.open("xb") as writer:
        destination.chmod(0o640)
        while block := reader.read(65536):
            writer.write(block)
    destination.chmod(0o440)


def _verify_extracted_source(db, snapshot, settings):
    root = settings.source_root / snapshot.id
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise AppError(409, "DERIVATION_MANIFEST_MISSING", "受控源码 manifest 缺失")
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("snapshot_id") != snapshot.id
        or manifest.get("source_sha256") != snapshot.source_sha256
    ):
        raise AppError(409, "DERIVATION_MANIFEST_MISMATCH", "受控源码 manifest 与快照不一致")
    # 排除项保留审计记录但不会落盘，派生校验只覆盖实际索引的文件。
    files = list(
        db.scalars(
            select(SourceFile).where(
                SourceFile.snapshot_id == snapshot.id,
                SourceFile.parse_status != "excluded",
            )
        )
    )
    if not files:
        raise AppError(409, "DERIVATION_SOURCE_EMPTY", "受控源码没有可校验的索引文件")
    for file in files:
        path = source_path(settings.source_root, snapshot.id, file.path)
        if not path.is_file() or path.stat().st_size != file.size:
            raise AppError(409, "DERIVATION_SOURCE_HASH_MISMATCH", "受控源码物理文件缺失或大小改变")
        if hash_file(path) != file.sha256:
            raise AppError(409, "DERIVATION_SOURCE_HASH_MISMATCH", "受控源码物理文件哈希改变")
    return len(files)


def _source_snapshot(db, category):
    # 首次发布前没有 active_snapshot_id：只允许使用该品类最近一次已索引的
    # 基础源码快照作为工作来源。绝不伪造源码，也不把派生快照再派生一层。
    source_id = category.active_snapshot_id
    if not source_id:
        source_id = db.scalar(
            select(Snapshot.id)
            .where(
                Snapshot.category_id == category.id,
                Snapshot.status == "extracted",
                Snapshot.index_status == "indexed",
                Snapshot.derivation_type.is_(None),
            )
            .order_by(Snapshot.created_at.desc(), Snapshot.id.desc())
            .limit(1)
        )
    if not source_id:
        raise AppError(
            409, "DERIVATION_SOURCE_REQUIRED", "品类没有已索引的受控源码，不能派生暂存快照"
        )
    source = db.get(Snapshot, source_id)
    if not source or source.category_id != category.id:
        raise AppError(409, "DERIVATION_SOURCE_INVALID", "当前源码指针无效")
    if source.status not in {"extracted", "published"} or source.index_status != "indexed":
        raise AppError(409, "DERIVATION_SOURCE_NOT_READY", "当前源码未完成索引，不能派生")
    return source


def _copy_documents(db, source, target, category, settings):
    copied = 0
    for doc in db.scalars(
        select(Document).where(
            Document.snapshot_id == source.id,
            Document.category_id == category.id,
        )
    ):
        original = settings.document_store / doc.storage_key
        if not original.is_file():
            raise AppError(409, "DERIVATION_DOCUMENT_MISSING", "受控文档物理文件缺失")
        key = str(uuid4()) + Path(doc.filename).suffix.lower()
        reserve(settings, key, doc.size)
        destination = settings.document_store / key
        try:
            _copy_verified(original, destination, doc.sha256, doc.size)
            new_doc = Document(
                category_id=category.id,
                snapshot_id=target.id,
                filename=doc.filename,
                storage_key=key,
                sha256=doc.sha256,
                size=doc.size,
                parse_status=doc.parse_status,
                error_code=doc.error_code,
                parser_version=doc.parser_version,
                parse_details=doc.parse_details,
                origin_json={
                    "derived_from_snapshot_id": source.id,
                    "source_document_id": doc.id,
                    "source_sha256": doc.sha256,
                },
            )
            db.add(new_doc)
            db.flush()
            for segment in db.scalars(
                select(DocumentSegment).where(DocumentSegment.document_id == doc.id)
            ):
                if segment.snapshot_id != source.id:
                    raise AppError(409, "DERIVATION_SCOPE_INVALID", "文档分段跨快照")
                if digest(segment.text.encode()) != segment.text_hash:
                    raise AppError(409, "DERIVATION_SEGMENT_HASH_MISMATCH", "文档分段哈希改变")
                db.add(
                    DocumentSegment(
                        snapshot_id=target.id,
                        document_id=new_doc.id,
                        locator=segment.locator,
                        text=segment.text,
                        text_hash=segment.text_hash,
                        sensitive=segment.sensitive,
                    )
                )
            copied += 1
        except BaseException:
            if destination.exists():
                destination.unlink(missing_ok=True)
            release(settings, key)
            raise
        finally:
            release(settings, key)
    return copied


def derive_staging_snapshot(db, category_id, actor, correlation_id):
    """从当前受控源码与文档物理派生新的待发布快照。"""
    with db.no_autoflush:
        category = db.scalar(
            select(Category)
            .where(Category.id == category_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if not category:
        raise AppError(404, "CATEGORY_NOT_FOUND", "品类不存在")
    if db.scalar(
        select(KnowledgeJob.id)
        .where(
            KnowledgeJob.category_id == category.id,
            KnowledgeJob.status.in_(["queued", "running"]),
        )
        .limit(1)
    ):
        raise AppError(409, "JOB_CONFLICT", "品类已有在途任务")
    source = _source_snapshot(db, category)
    settings = get_settings()
    _verify_extracted_source(db, source, settings)
    archive = settings.archive_root / source.archive_key
    if not archive.is_file():
        raise AppError(409, "DERIVATION_ARCHIVE_MISSING", "受控源码归档缺失")
    if hash_file(archive) != source.source_sha256:
        raise AppError(409, "DERIVATION_SOURCE_HASH_MISMATCH", "受控源码归档哈希改变")
    archive_key = f"{uuid4()}.zip"
    target = Snapshot(
        category_id=category.id,
        source_sha256=source.source_sha256,
        status="extracted",
        archive_key=archive_key,
        data_classification=source.data_classification,
        derived_from_snapshot_id=source.id,
        derivation_type="document_update",
    )
    db.add(target)
    db.flush()
    reserve(settings, archive_key, archive.stat().st_size)
    copied_archive = settings.archive_root / archive_key
    try:
        _copy_verified(archive, copied_archive, source.source_sha256, archive.stat().st_size)
        manifest_key, _ = extract_snapshot(archive_key, source.source_sha256, target.id, settings)
        target.manifest_key = manifest_key
        target.index_status = "not_indexed"
        copied_documents = _copy_documents(db, source, target, category, settings)
        db.add(
            AuditEvent(
                principal_id=actor,
                action="documents.staging_snapshot.derived",
                resource_id=target.id,
                correlation_id=correlation_id,
                details_json={
                    "derived_from_snapshot_id": source.id,
                    "documents_copied": copied_documents,
                    "derivation_type": "document_update",
                },
            )
        )
        db.commit()
        return {
            "snapshot_id": target.id,
            "derived_from_snapshot_id": source.id,
            "derivation_type": "document_update",
            "documents_copied": copied_documents,
            "status": target.status,
            "index_status": target.index_status,
        }
    except BaseException:
        db.rollback()
        if copied_archive.exists():
            copied_archive.unlink(missing_ok=True)
        from app.ingestion.content_store import remove_tree

        extracted = settings.ingestion_root / target.id
        if extracted.exists():
            try:
                remove_tree(extracted)
            except OSError:
                pass
        raise
    finally:
        release(settings, archive_key)


def require_derived_staging(db, snapshot_id, category_id=None, lock=True):
    query = select(Snapshot).where(Snapshot.id == snapshot_id)
    if lock:
        query = query.with_for_update()
    snapshot = db.scalar(query)
    if not snapshot or (category_id and snapshot.category_id != category_id):
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在或不属于指定品类")
    if snapshot.status != "extracted" or snapshot.derivation_type != "document_update":
        raise AppError(409, "DOCUMENT_STAGING_REQUIRED", "只允许修改派生暂存快照中的文档")
    if db.scalar(
        select(KnowledgeJob.id)
        .where(
            KnowledgeJob.category_id == snapshot.category_id,
            KnowledgeJob.status.in_(["queued", "running"]),
        )
        .limit(1)
    ):
        raise AppError(409, "JOB_CONFLICT", "品类已有在途任务")
    return snapshot


def _document_in_use(db, snapshot_id, document_id):
    segment_ids = set(
        db.scalars(select(DocumentSegment.id).where(DocumentSegment.document_id == document_id))
    )
    evidence = db.scalars(
        select(Evidence).where(Evidence.snapshot_id == snapshot_id, Evidence.kind == "document")
    )
    if any(row.source_ref.get("segment_id") in segment_ids for row in evidence):
        raise AppError(409, "DOCUMENT_IN_USE", "文档证据已被审核引用，不能修改")
    return segment_ids


def remove_document(db, snapshot_id, document_id, actor, correlation_id):
    snapshot = require_derived_staging(db, snapshot_id)
    doc = db.get(Document, document_id)
    if not doc or doc.snapshot_id != snapshot.id:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "暂存快照中没有该文档")
    _document_in_use(db, snapshot.id, doc.id)
    key = doc.storage_key
    db.execute(DocumentSegment.__table__.delete().where(DocumentSegment.document_id == doc.id))
    db.execute(
        Document.__table__.delete().where(
            Document.id == doc.id, Document.snapshot_id == snapshot.id
        )
    )
    db.add(
        AuditEvent(
            principal_id=actor,
            action="documents.staging.removed",
            resource_id=doc.id,
            correlation_id=correlation_id,
            details_json={"snapshot_id": snapshot.id, "filename": doc.filename},
        )
    )
    db.commit()
    (get_settings().document_store / key).unlink(missing_ok=True)
    return {"document_id": document_id, "snapshot_id": snapshot.id, "status": "removed"}


def replace_document(db, snapshot_id, document_id, key, sha256, size, actor, correlation_id):
    snapshot = require_derived_staging(db, snapshot_id)
    doc = db.get(Document, document_id)
    if not doc or doc.snapshot_id != snapshot.id:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "暂存快照中没有该文档")
    _document_in_use(db, snapshot.id, doc.id)
    old_key = doc.storage_key
    old_sha256 = doc.sha256
    db.execute(DocumentSegment.__table__.delete().where(DocumentSegment.document_id == doc.id))
    doc.storage_key, doc.sha256, doc.size = key, sha256, size
    doc.parse_status, doc.error_code, doc.parser_version, doc.parse_details = (
        "pending",
        None,
        None,
        {},
    )
    doc.origin_json = {
        **(doc.origin_json or {}),
        "replaced": True,
        "previous_storage_key_sha256": old_sha256,
    }
    job = KnowledgeJob(
        category_id=snapshot.category_id,
        snapshot_id=snapshot.id,
        job_type="document_index",
        stage="extracted",
    )
    db.add(job)
    db.flush()
    db.add(
        AuditEvent(
            principal_id=actor,
            action="documents.staging.replaced",
            resource_id=doc.id,
            correlation_id=correlation_id,
            details_json={"job_id": job.id, "snapshot_id": snapshot.id},
        )
    )
    db.commit()
    (get_settings().document_store / old_key).unlink(missing_ok=True)
    return {"document_id": doc.id, "job_id": job.id, "snapshot_id": snapshot.id}


def reparse_document(db, snapshot_id, document_id, actor, correlation_id):
    """在派生暂存快照内重建单个文档解析结果；已引用证据的文档禁止重解析。"""
    snapshot = require_derived_staging(db, snapshot_id)
    doc = db.get(Document, document_id)
    if not doc or doc.snapshot_id != snapshot.id:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "暂存快照中没有该文档")
    _document_in_use(db, snapshot.id, doc.id)
    doc.parse_status, doc.error_code, doc.parse_details = "pending", None, {}
    job = KnowledgeJob(
        category_id=snapshot.category_id,
        snapshot_id=snapshot.id,
        job_type="document_index",
        stage="extracted",
        metrics_json={"model_mode": "NOT_USED", "reparse_document_id": doc.id},
    )
    db.add(job)
    db.flush()
    db.add(
        AuditEvent(
            principal_id=actor,
            action="documents.staging.reparse_requested",
            resource_id=doc.id,
            correlation_id=correlation_id,
            details_json={"job_id": job.id, "snapshot_id": snapshot.id},
        )
    )
    db.commit()
    return {"document_id": doc.id, "job_id": job.id, "snapshot_id": snapshot.id}
