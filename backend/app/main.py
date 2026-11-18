import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException

from app.api import (
    capabilities,
    categories,
    conflicts,
    indexing,
    investigations,
    jobs,
    knowledge_uploads,
    probe,
    retrieval,
)
from app.core.auth import admin
from app.core.config import get_settings
from app.core.logging import RequestGuard, log_database_error
from app.core.metrics import snapshot as metric_snapshot
from app.core.security import AppError
from app.db.session import get_engine
from app.jobs.queue import connection


def error_response(request, status, code, message):
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
    return JSONResponse(
        status_code=status,
        content={
            "error_code": code,
            "message": message,
            "correlation_id": request.state.correlation_id,
        },
        headers=headers,
    )


def create_app():
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app):
        os.umask(0o027)
        logging.basicConfig(level=logging.INFO)
        yield

    app = FastAPI(title="产品知识索引与检索 API", version="0.2.0", lifespan=lifespan)
    app.add_middleware(RequestGuard, max_body_bytes=settings.max_upload_bytes + 1024 * 1024)

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return error_response(request, exc.status_code, exc.error_code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc):
        return error_response(request, 422, "VALIDATION_ERROR", "请求字段不合法")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc):
        code = "REQUEST_TOO_LARGE" if exc.status_code == 413 else f"HTTP_{exc.status_code}"
        return error_response(
            request,
            exc.status_code,
            code,
            "请求超过限额" if exc.status_code == 413 else "请求不能处理",
        )

    @app.exception_handler(DBAPIError)
    async def database_error(request: Request, exc):
        log_database_error(exc, request.state.correlation_id)
        code = getattr(getattr(exc, "orig", None), "sqlstate", None)
        return error_response(
            request,
            504 if code in {"57014", "55P03"} else 503,
            "DATABASE_TIMEOUT" if code in {"57014", "55P03"} else "DATABASE_UNAVAILABLE",
            "数据库请求超时" if code in {"57014", "55P03"} else "数据库暂时不可用",
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc):
        return error_response(request, 500, "INTERNAL_ERROR", "服务内部错误")

    @app.get("/health/live", tags=["健康检查"])
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["健康检查"])
    def ready(request: Request):
        try:
            with get_engine().connect() as db:
                db.execute(text("SELECT id FROM product_categories LIMIT 1"))
            connection().ping()
        except Exception:
            return error_response(
                request, 503, "DEPENDENCY_UNAVAILABLE", "数据库、迁移或队列尚未就绪"
            )
        return {"status": "ready"}

    @app.get("/api/v1/admin/metrics")
    def metrics(role=Depends(admin)):
        return metric_snapshot()

    if settings.service_mode != "retrieval":
        app.include_router(categories.router)
        app.include_router(knowledge_uploads.router)
        app.include_router(jobs.router)
        app.include_router(indexing.router)
        app.include_router(investigations.router)
        app.include_router(capabilities.router)
        app.include_router(conflicts.router)
    app.include_router(retrieval.router)
    app.include_router(probe.router)
    return app
