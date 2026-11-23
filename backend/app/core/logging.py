import json
import logging
import re
import time
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import bearer, principal
from app.core.metrics import observe
from app.core.security import AppError

logger = logging.getLogger("knowledge.audit")

# 只有管理员源码 ZIP 上传使用 1 GiB 额度；其余路径（含售前文档、招标文件）
# 继续使用 100 MiB 请求体上限，避免把大文件额度扩散到普通业务。
SOURCE_UPLOAD_PATH = re.compile(r"^/api/v1/categories/[^/]+/source/?$")


def database_diagnostic(exc, correlation_id):
    """只保留诊断标识，不记录SQL、参数、异常正文或驱动DETAIL。"""
    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)

    def identifier(value, limit=100):
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            return value[:limit]
        return None

    state = getattr(original, "sqlstate", None)
    context = getattr(diagnostic, "context", None) or ""
    function = re.search(r"PL/pgSQL function ([A-Za-z_][A-Za-z0-9_]*)\(", context)
    return {
        "event": "database_error",
        "correlation_id": correlation_id,
        "exception_type": identifier(type(exc).__name__),
        "driver_exception_type": identifier(type(original).__name__),
        "sqlstate": state
        if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state)
        else None,
        "constraint_name": identifier(getattr(diagnostic, "constraint_name", None)),
        "table_name": identifier(getattr(diagnostic, "table_name", None)),
        "column_name": identifier(getattr(diagnostic, "column_name", None)),
        "function_name": identifier(function.group(1)) if function else None,
    }


def log_database_error(exc, correlation_id):
    logger.error(json.dumps(database_diagnostic(exc, correlation_id), ensure_ascii=False))


class RequestGuard:
    def __init__(self, app, max_body_bytes: int, source_max_body_bytes: int | None = None):
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.source_max_body_bytes = source_max_body_bytes

    def _limit_for(self, scope) -> int:
        if (
            self.source_max_body_bytes is not None
            and scope.get("method") == "POST"
            and SOURCE_UPLOAD_PATH.fullmatch(scope.get("path", ""))
        ):
            return self.source_max_body_bytes
        return self.max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        correlation_id = str(uuid4())
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        started = time.monotonic()
        consumed = 0
        status = 500
        body_limit = self._limit_for(scope)

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > body_limit:
                raise HTTPException(413, "REQUEST_TOO_LARGE")
            return message

        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"].append((b"x-correlation-id", correlation_id.encode()))
            await send(message)

        try:
            # 先按 Content-Length 快速失败，避免为超限请求体分配磁盘和内存。
            for name, value in scope.get("headers", []):
                if name.lower() != b"content-length":
                    continue
                try:
                    declared = int(value)
                except ValueError:
                    break
                if declared > body_limit:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error_code": "REQUEST_TOO_LARGE",
                            "message": "请求超过限额",
                            "correlation_id": correlation_id,
                        },
                    )
                    return await response(scope, receive, tracked_send)
            if (
                scope["method"] == "POST"
                and scope["path"].startswith("/api/v1/categories/")
                and scope["path"].rstrip("/").endswith(("/source", "/documents"))
            ):
                try:
                    role = principal(await bearer(Request(scope)))
                    if role != "admin":
                        raise AppError(403, "FORBIDDEN", "仅管理员可上传源码")
                except AppError as exc:
                    response = JSONResponse(
                        status_code=exc.status_code,
                        content={
                            "error_code": exc.error_code,
                            "message": exc.message,
                            "correlation_id": correlation_id,
                        },
                        headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
                    )
                    return await response(scope, receive, tracked_send)
            await self.app(scope, bounded_receive, tracked_send)
        finally:
            observe(
                "retrieval_requests"
                if scope["path"].startswith("/api/v1/retrieval/")
                else "http_requests",
                (time.monotonic() - started) * 1000,
            )
            # 不记录 URL 参数、请求体、凭据或源码内容。
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "correlation_id": correlation_id,
                        "method": scope["method"],
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    }
                )
            )
