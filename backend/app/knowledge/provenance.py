"""R02 真实资料来源确认与快照派生链核验。

管理员确认记录只解决“来源与适用范围”的事实登记；是否可用于 Gate 2 还必须
通过同品类、同根快照和合法派生链校验，不能仅凭哈希或文件名判断。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import AppError
from app.db.governance_models import MaterialProvenance
from app.db.index_models import Document, DocumentSegment
from app.db.models import Category, Snapshot, now

SYNTHETIC_SHA256 = {
    "d082d172f6a6e7d39d36809625a94ef278a6de7d65df718bc247d0c1bc55c65e",
    "fda28d7f561e47ff1aa9d7584d0b6322f520bc87cab06257901c6dd06f076d39",
}
MATERIAL_TYPES = {"source_code", "technical_document"}


def _text(value, field, minimum=3, maximum=1000):
    value = (value or "").strip()
    if len(value) < minimum or len(value) > maximum:
        raise AppError(422, "PROVENANCE_FIELD_INVALID", f"{field}长度不合法")
    return value


def _active_material(db, category_id, material_key):
    return db.scalar(
        select(MaterialProvenance).where(
            MaterialProvenance.category_id == category_id,
            MaterialProvenance.material_key == material_key,
            MaterialProvenance.status == "confirmed",
        )
    )


def confirm_material(
    db: Session,
    *,
    category_id,
    snapshot_id,
    material_type,
    document_id,
    declared_origin,
    usage_scope,
    confirmation_reference,
    confirmed_by,
):
    if material_type not in MATERIAL_TYPES:
        raise AppError(422, "PROVENANCE_TYPE_INVALID", "资料来源类型不合法")
    category = db.get(Category, category_id)
    if category is None:
        raise AppError(404, "CATEGORY_NOT_FOUND", "品类不存在")
    snapshot = db.get(Snapshot, snapshot_id)
    if snapshot is None or snapshot.category_id != category_id:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在或不属于该品类")
    if snapshot.status not in {"extracted", "published"} or snapshot.index_status != "indexed":
        raise AppError(422, "PROVENANCE_SNAPSHOT_NOT_READY", "只有已提取且已索引的快照可登记来源")
    if snapshot.data_classification != "enterprise":
        raise AppError(422, "PROVENANCE_SYNTHETIC_REJECTED", "合成样例不能登记为真实来源")

    document = None
    if material_type == "source_code":
        if snapshot.derivation_type is not None or snapshot.derived_from_snapshot_id is not None:
            raise AppError(422, "PROVENANCE_SOURCE_NOT_ROOT", "源码来源必须是根快照")
        if document_id:
            raise AppError(422, "PROVENANCE_DOCUMENT_UNEXPECTED", "源码来源不能关联文档")
        digest = snapshot.source_sha256
        key = f"source:{snapshot.id}"
    else:
        if not document_id:
            raise AppError(422, "PROVENANCE_DOCUMENT_REQUIRED", "技术文档必须关联文档记录")
        document = db.get(Document, document_id)
        if document is None or document.snapshot_id != snapshot.id:
            raise AppError(404, "DOCUMENT_NOT_FOUND", "文档不存在或不属于该快照")
        if document.category_id != category_id:
            raise AppError(404, "DOCUMENT_NOT_FOUND", "文档不存在或不属于该品类")
        if document.parse_status != "parsed":
            raise AppError(422, "PROVENANCE_DOCUMENT_NOT_PARSED", "文档尚未完成解析")
        segments = db.scalar(
            select(func.count())
            .select_from(DocumentSegment)
            .where(DocumentSegment.document_id == document.id)
        )
        if not segments:
            raise AppError(422, "PROVENANCE_DOCUMENT_EMPTY", "文档没有可核验的正文分段")
        if document.data_classification != "enterprise":
            raise AppError(422, "PROVENANCE_SYNTHETIC_REJECTED", "合成文档不能登记为真实来源")
        digest = document.sha256
        key = f"document:{document.id}"

    if digest in SYNTHETIC_SHA256:
        raise AppError(422, "PROVENANCE_SYNTHETIC_REJECTED", "验收夹具不能登记为真实来源")
    if _active_material(db, category_id, key):
        raise AppError(409, "PROVENANCE_ALREADY_CONFIRMED", "该资料已有生效确认记录")
    row = MaterialProvenance(
        category_id=category_id,
        snapshot_id=snapshot.id,
        document_id=document.id if document else None,
        material_type=material_type,
        material_key=key,
        content_sha256=digest,
        data_classification="enterprise",
        declared_origin=_text(declared_origin, "来源说明", 3, 500),
        usage_scope=_text(usage_scope, "适用范围", 8, 1000),
        confirmation_reference=_text(confirmation_reference, "确认依据", 3, 200),
        confirmed_by=confirmed_by,
        status="confirmed",
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def revoke_material(db: Session, record_id, revoked_by):
    row = db.get(MaterialProvenance, record_id)
    if row is None:
        raise AppError(404, "PROVENANCE_NOT_FOUND", "来源确认记录不存在")
    if row.status != "confirmed":
        raise AppError(409, "PROVENANCE_NOT_ACTIVE", "来源确认记录已非生效状态")
    row.status = "revoked"
    row.revoked_at = now()
    row.revoked_by = revoked_by
    db.flush()
    return row


def provenance_view(record):
    return {
        "id": record.id,
        "category_id": record.category_id,
        "snapshot_id": record.snapshot_id,
        "document_id": record.document_id,
        "material_type": record.material_type,
        "content_sha256": record.content_sha256,
        "data_classification": record.data_classification,
        "declared_origin": record.declared_origin,
        "usage_scope": record.usage_scope,
        "confirmation_reference": record.confirmation_reference,
        "confirmed_by": record.confirmed_by,
        "status": record.status,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "revoked_at": record.revoked_at.isoformat() if record.revoked_at else None,
        "revoked_by": record.revoked_by,
    }


def category_provenance(db: Session, category_id):
    if db.get(Category, category_id) is None:
        raise AppError(404, "CATEGORY_NOT_FOUND", "品类不存在")
    rows = list(
        db.scalars(
            select(MaterialProvenance)
            .where(MaterialProvenance.category_id == category_id)
            .order_by(MaterialProvenance.created_at.desc(), MaterialProvenance.id.desc())
        )
    )
    return [provenance_view(row) for row in rows]


def derivation_chain(db: Session, snapshot_id):
    row = db.get(Snapshot, snapshot_id)
    if row is None:
        raise AppError(404, "SNAPSHOT_NOT_FOUND", "快照不存在")
    rows = []
    seen = set()
    error = None
    current = row
    while current is not None:
        if current.id in seen:
            error = "DERIVATION_CYCLE"
            break
        if len(rows) >= 64:
            error = "DERIVATION_TOO_DEEP"
            break
        if current.category_id != row.category_id:
            error = "DERIVATION_CATEGORY_MISMATCH"
            break
        seen.add(current.id)
        rows.append(current)
        parent_id = current.derived_from_snapshot_id
        if bool(parent_id) != bool(current.derivation_type):
            error = "DERIVATION_METADATA_INCOMPLETE"
            break
        if not parent_id:
            break
        current = db.get(Snapshot, parent_id)
        if current is None:
            error = "DERIVATION_PARENT_MISSING"
            break
    rows.reverse()
    return {
        "snapshot_id": row.id,
        "category_id": row.category_id,
        "root_snapshot_id": rows[0].id if rows else None,
        "valid": error is None,
        "error_code": error,
        "chain": [
            {
                "id": item.id,
                "category_id": item.category_id,
                "derived_from_snapshot_id": item.derived_from_snapshot_id,
                "derivation_type": item.derivation_type,
                "status": item.status,
                "index_status": item.index_status,
                "data_classification": item.data_classification,
                "source_sha256": item.source_sha256,
            }
            for item in rows
        ],
    }
