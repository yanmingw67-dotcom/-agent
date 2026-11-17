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
