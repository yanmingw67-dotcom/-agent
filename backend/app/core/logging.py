import json
import logging
import time
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import bearer, principal
from app.core.security import AppError

logger = logging.getLogger("knowledge.audit")


class RequestGuard:
    def __init__(self, app, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        correlation_id = str(uuid4())
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        started = time.monotonic()
        consumed = 0
        status = 500

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > self.max_body_bytes:
                raise HTTPException(413, "REQUEST_TOO_LARGE")
            return message

        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"].append((b"x-correlation-id", correlation_id.encode()))
            await send(message)

        try:
            if (
                scope["method"] == "POST"
                and scope["path"].startswith("/api/v1/categories/")
                and scope["path"].endswith("/source")
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
