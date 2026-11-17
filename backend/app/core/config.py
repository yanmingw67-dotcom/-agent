from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://knowledge:knowledge@postgres/knowledge"
    redis_url: str = "redis://redis:6379/0"
    admin_token: SecretStr
    read_token: SecretStr
    archive_root: Path = Path("/data/archives")
    ingestion_root: Path = Path("/data/ingestion")
    source_root: Path = Path("/data/sources")
    max_upload_bytes: int = 100 * 1024 * 1024
    max_entries: int = 20000
    max_file_bytes: int = 20 * 1024 * 1024
    max_total_bytes: int = 500 * 1024 * 1024
    max_compression_ratio: float = 100
    max_depth: int = 20
    job_timeout_seconds: int = 300
    queue_name: str = "knowledge-ingestion"
    index_queue_name: str = "knowledge-index"
    worker_kind: str = "ingestion"
    service_mode: str = "api"
    documents_root: Path | None = None
    storage_control_root: Path | None = None
    storage_quota_bytes: int = 10 * 1024**3
    storage_min_free_bytes: int = 1024**3
    index_max_file_bytes: int = 2 * 1024**2
    document_max_bytes: int = 20 * 1024**2
    document_max_segments: int = 20000
    document_max_text_chars: int = 5_000_000
    index_job_timeout_seconds: int = 1800
    search_timeout_seconds: float = 3
    read_max_lines: int = 200
    retrieval_max_chars: int = 24000
    index_parser_version: str = "p12-2"

    @property
    def document_store(self):
        return self.documents_root or self.archive_root.parent / "documents"

    @property
    def control_store(self):
        return self.storage_control_root or self.archive_root.parent / ".control"

    @field_validator("admin_token", "read_token")
    @classmethod
    def strong_token(cls, value):
        if len(value.get_secret_value()) < 32:
            raise ValueError("鉴权 Token 至少 32 个字符")
        return value

    @field_validator(
        "max_upload_bytes",
        "max_entries",
        "max_file_bytes",
        "max_total_bytes",
        "max_depth",
        "job_timeout_seconds",
        "max_compression_ratio",
        "storage_quota_bytes",
        "index_max_file_bytes",
        "document_max_bytes",
        "document_max_segments",
        "document_max_text_chars",
        "index_job_timeout_seconds",
        "search_timeout_seconds",
        "read_max_lines",
        "retrieval_max_chars",
    )
    @classmethod
    def positive_limit(cls, value):
        if value <= 0:
            raise ValueError("资源限额必须为正数")
        return value

    @model_validator(mode="after")
    def distinct_tokens(self):
        if self.admin_token == self.read_token:
            raise ValueError("管理员和只读 Token 必须不同")
        return self


@lru_cache
def get_settings():
    return Settings()
