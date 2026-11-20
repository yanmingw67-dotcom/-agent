import json
import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.security import AppError

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Identity:
    """服务端认证得到的身份；user_id 不可由请求体或查询参数覆盖。"""

    user_id: str
    role: Literal["admin", "read"]
    source: Literal["admin_token", "read_token", "user_token"]


def _configured_users(settings) -> dict[str, str]:
    raw = settings.read_user_tokens.get_secret_value().strip()
    if not raw:
        return {}
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(500, "AUTH_CONFIG_INVALID", "用户 Token 配置不是合法 JSON") from exc
    if not isinstance(mapping, dict):
        raise AppError(500, "AUTH_CONFIG_INVALID", "用户 Token 配置必须是对象")
    return {str(user_id): str(token) for user_id, token in mapping.items()}


def current_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Identity:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise AppError(401, "UNAUTHENTICATED", "需要 Bearer Token")
    settings = get_settings()
    value = credentials.credentials.encode()
    if secrets.compare_digest(value, settings.admin_token.get_secret_value().encode()):
        return Identity("admin", "admin", "admin_token")
    if secrets.compare_digest(value, settings.read_token.get_secret_value().encode()):
        # 共享只读 Token 只有一个服务端身份，不能当成真实多用户隔离。
        return Identity("read-shared", "read", "read_token")
    for user_id, token in _configured_users(settings).items():
        if secrets.compare_digest(value, token.encode()):
            return Identity(user_id, "read", "user_token")
    raise AppError(401, "UNAUTHENTICATED", "鉴权失败")


def principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Literal["admin", "read"]:
    """P1.1～P1.6 兼容入口：继续返回角色字符串。"""
    return current_identity(credentials).role


def tender_principal(identity: Identity = Depends(current_identity)) -> str:
    """P2.1 使用可信用户 ID，而不是把角色字符串当作 principal_id。"""
    return identity.user_id


def admin(role: str = Depends(principal)):
    if role != "admin":
        raise AppError(403, "FORBIDDEN", "仅管理员可执行此操作")
    return role


def model_ops(
    identity: Identity = Depends(current_identity),
    token: str | None = Header(default=None, alias="X-Model-Ops-Token"),
):
    """模型外发授权登记需要独立运维令牌，管理员令牌本身不能完成审批。"""
    if identity.role != "admin":
        raise AppError(403, "FORBIDDEN", "仅管理员可执行此操作")
    expected = get_settings().model_ops_token.get_secret_value()
    if not expected:
        raise AppError(503, "MODEL_OPS_NOT_CONFIGURED", "未配置模型授权运维令牌")
    if not token or not secrets.compare_digest(token.encode(), expected.encode()):
        raise AppError(403, "MODEL_OPS_FORBIDDEN", "模型授权运维令牌无效")
    return identity
