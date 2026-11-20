"""R01 企业模型外发授权的服务端事实与门禁。

环境变量只表示“运行时开关”和“端点配置”，不能单独构成审批。真实模型构造前
必须命中当前模型/端点/数据分类对应的有效数据库授权记录。
"""

from datetime import UTC
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import AppError
from app.db.governance_models import ModelAuthorization
from app.db.models import now

CLASSIFICATIONS = {"synthetic_public", "enterprise"}


def configured_target():
    settings = get_settings()
    if not (
        settings.builder_model
        and settings.builder_base_url
        and settings.builder_api_key.get_secret_value()
    ):
        return None
    return settings.builder_model, normalize_endpoint(settings.builder_base_url)


def normalize_endpoint(value):
    """只接受无凭据、无查询参数的 HTTP(S) 基地址，并绑定完整路径。"""
    parsed = urlparse((value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AppError(422, "MODEL_ENDPOINT_INVALID", "模型端点必须为无凭据的HTTP(S)地址")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{port}{path}"


def env_allowed(classification):
    settings = get_settings()
    return (
        settings.builder_allow_synthetic
        if classification == "synthetic_public"
        else settings.builder_allow_enterprise
    )


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _active(record, at=None):
    if record is None or record.status != "active":
        return False
    expires = _utc(record.expires_at)
    return expires is None or expires > (at or now())


def _candidates(db, classification, model_name=None, endpoint=None):
    query = select(ModelAuthorization).where(
        ModelAuthorization.data_classification == classification,
        ModelAuthorization.status == "active",
    )
    if model_name:
        query = query.where(ModelAuthorization.model_name == model_name)
    if endpoint:
        query = query.where(ModelAuthorization.endpoint == endpoint)
    return list(
        db.scalars(
            query.order_by(ModelAuthorization.created_at.desc(), ModelAuthorization.id.desc())
        )
    )


def active_authorization(db: Session, classification, at=None):
    for record in _candidates(db, classification):
        if _active(record, at):
            settings = get_settings()
            target = None
            try:
                if settings.builder_base_url:
                    target = normalize_endpoint(settings.builder_base_url)
            except AppError:
                target = None
            if (
                target
                and settings.builder_model
                and record.model_name == settings.builder_model
                and record.endpoint == target
            ):
                return record
    return None


def authorization_state(db: Session, classification="enterprise"):
    settings = get_settings()
    configured = bool(
        settings.builder_model
        and settings.builder_base_url
        and settings.builder_api_key.get_secret_value()
    )
    allowed = env_allowed(classification)
    record = active_authorization(db, classification) if configured and allowed else None
    if not configured:
        status, reason = "UNAVAILABLE", "MODEL_NOT_CONFIGURED"
    elif not allowed:
        status, reason = "ENV_DISABLED", "MODEL_DATA_NOT_AUTHORIZED"
    elif record is None:
        status, reason = "NOT_AUTHORIZED", "MODEL_AUTHORIZATION_RECORD_REQUIRED"
    else:
        status, reason = "AUTHORIZED", None
    return {
        "status": status,
        "reason_code": reason,
        "configured": configured,
        "env_allowed": allowed,
        "authorization_source": "database_record",
        "env_flags_are_approval": False,
        "active_authorization": authorization_view(record) if record else None,
    }


def require_model_authorization(db: Session, classification):
    if classification not in CLASSIFICATIONS:
        raise AppError(422, "MODEL_CLASSIFICATION_INVALID", "数据分类不合法")
    settings = get_settings()
    if not (
        settings.builder_model
        and settings.builder_base_url
        and settings.builder_api_key.get_secret_value()
    ):
        raise AppError(503, "MODEL_NOT_CONFIGURED", "未配置企业批准模型")
    endpoint = normalize_endpoint(settings.builder_base_url)
    if not env_allowed(classification):
        raise AppError(403, "MODEL_DATA_NOT_AUTHORIZED", "此类数据尚未批准发往配置模型端点")
    record = active_authorization(db, classification)
    if record is None:
        raise AppError(
            403,
            "MODEL_AUTHORIZATION_RECORD_REQUIRED",
            "缺少与当前模型、端点及数据分类匹配的正式授权记录",
        )
    if record.model_name != settings.builder_model or record.endpoint != endpoint:
        raise AppError(
            403,
            "MODEL_AUTHORIZATION_RECORD_MISMATCH",
            "授权记录与当前模型端点不一致，必须重新审批",
        )
    return record


def create_authorization(
    db: Session,
    *,
    classification,
    approval_reference,
    approved_scope,
    approved_by,
    expires_at=None,
):
    if classification not in CLASSIFICATIONS:
        raise AppError(422, "MODEL_CLASSIFICATION_INVALID", "数据分类不合法")
    settings = get_settings()
    if not (
        settings.builder_model
        and settings.builder_base_url
        and settings.builder_api_key.get_secret_value()
    ):
        raise AppError(503, "MODEL_NOT_CONFIGURED", "配置模型端点后才能登记外发授权")
    endpoint = normalize_endpoint(settings.builder_base_url)
    expiry = _utc(expires_at)
    current = now()
    if expiry is not None and expiry <= current:
        raise AppError(422, "MODEL_AUTHORIZATION_EXPIRY_INVALID", "授权到期时间必须晚于当前时间")
    for row in _candidates(db, classification, settings.builder_model, endpoint):
        if _active(row, current):
            raise AppError(409, "MODEL_AUTHORIZATION_EXISTS", "当前模型端点已有有效授权")
        row.status = "expired"
    row = ModelAuthorization(
        data_classification=classification,
        model_name=settings.builder_model,
        endpoint=endpoint,
        approval_reference=approval_reference.strip(),
        approved_scope=approved_scope.strip(),
        approved_by=approved_by,
        status="active",
        expires_at=expiry,
        created_at=current,
    )
    db.add(row)
    db.flush()
    return row


def revoke_authorization(db: Session, record_id, revoked_by):
    row = db.get(ModelAuthorization, record_id)
    if row is None:
        raise AppError(404, "MODEL_AUTHORIZATION_NOT_FOUND", "模型授权记录不存在")
    if row.status != "active":
        raise AppError(409, "MODEL_AUTHORIZATION_NOT_ACTIVE", "模型授权记录已非生效状态")
    row.status = "revoked"
    row.revoked_at = now()
    row.revoked_by = revoked_by
    db.flush()
    return row


def authorization_view(record):
    if record is None:
        return None
    return {
        "id": record.id,
        "data_classification": record.data_classification,
        "model_name": record.model_name,
        "endpoint": record.endpoint,
        "approval_reference": record.approval_reference,
        "approved_scope": record.approved_scope,
        "approved_by": record.approved_by,
        "status": record.status,
        "active": _active(record),
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "revoked_at": record.revoked_at.isoformat() if record.revoked_at else None,
        "revoked_by": record.revoked_by,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
