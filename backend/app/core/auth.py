import secrets
from typing import Literal

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.security import AppError

bearer = HTTPBearer(auto_error=False)


def principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Literal["admin", "read"]:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise AppError(401, "UNAUTHENTICATED", "需要 Bearer Token")
    settings = get_settings()
    value = credentials.credentials.encode()
    admin_match = secrets.compare_digest(value, settings.admin_token.get_secret_value().encode())
    read_match = secrets.compare_digest(value, settings.read_token.get_secret_value().encode())
    if admin_match:
        return "admin"
    if read_match:
        return "read"
    raise AppError(401, "UNAUTHENTICATED", "鉴权失败")


def admin(role: str = Depends(principal)):
    if role != "admin":
        raise AppError(403, "FORBIDDEN", "仅管理员可执行此操作")
    return role
