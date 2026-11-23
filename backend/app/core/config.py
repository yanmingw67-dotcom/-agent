import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://knowledge:knowledge@postgres/knowledge"
    redis_url: str = "redis://redis:6379/0"
    admin_token: SecretStr
    read_token: SecretStr
    # 允许独立部署的工作台前端跨域访问；多个来源用逗号分隔，留空即关闭 CORS。
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # 可选的普通用户 Token 映射：{"用户ID": "Token"}。为空时只保留共享 read 身份，
    # 不得据此声称已经实现真实多用户账号体系。
    read_user_tokens: SecretStr = SecretStr("")
    archive_root: Path = Path("/data/archives")
    ingestion_root: Path = Path("/data/ingestion")
    source_root: Path = Path("/data/sources")
    # 管理员受控源码 ZIP 单次上传上限：1 GiB。普通售前文档不使用该额度，
    # 仍由 request_max_body_bytes、document_max_bytes 与 tender_max_file_bytes 分别约束。
    max_upload_bytes: int = 1024 * 1024 * 1024
    # 非源码上传路径的请求体上限，保持 P3.1-B 之前的 100 MiB 业务边界。
    request_max_body_bytes: int = 100 * 1024 * 1024
    # multipart 边界、头部字段和表单开销；只加在源码上传路径上。
    multipart_overhead_bytes: int = 8 * 1024 * 1024
    max_entries: int = 20000
    max_file_bytes: int = 128 * 1024 * 1024
    max_total_bytes: int = 4 * 1024 * 1024 * 1024
    max_compression_ratio: float = 100
    max_depth: int = 20
    # 源码解压任务的有界超时；大文件解压不再受 300 秒预算限制，但仍有上限。
    job_timeout_seconds: int = 3600
    queue_name: str = "knowledge-ingestion"
    index_queue_name: str = "knowledge-index"
    worker_kind: str = "ingestion"
    service_mode: str = "api"
    documents_root: Path | None = None
    storage_control_root: Path | None = None
    # 验收与默认容量需同时容纳 1 GiB 原始 ZIP、4 GiB 解压源码和索引临时文件。
    storage_quota_bytes: int = 16 * 1024**3
    storage_min_free_bytes: int = 2 * 1024**3
    index_max_file_bytes: int = 2 * 1024**2
    document_max_bytes: int = 20 * 1024**2
    document_max_segments: int = 20000
    document_max_text_chars: int = 5_000_000
    index_job_timeout_seconds: int = 1800
    search_timeout_seconds: float = 3
    read_max_lines: int = 200
    retrieval_max_chars: int = 24000
    index_parser_version: str = "p12-2"
    builder_queue_name: str = "knowledge-builder"
    builder_job_timeout_seconds: int = 1800
    builder_module_timeout_seconds: int = 180
    builder_max_modules: int = 24
    builder_max_requests: int = 8
    builder_max_tool_calls: int = 30
    builder_max_tokens: int = 30000
    builder_max_context_chars: int = 80000
    builder_max_output_tokens: int = 4000
    builder_reasoning_effort: Literal["", "low", "high"] = ""
    builder_model: str = ""
    builder_base_url: str = ""
    builder_api_key: SecretStr = SecretStr("")
    builder_allow_synthetic: bool = False
    builder_allow_enterprise: bool = False
    # 仅用于授权运维登记模型外发审批；未配置时相关登记入口失败关闭。
    model_ops_token: SecretStr = SecretStr("")
    builder_input_usd_per_million: float | None = None
    builder_output_usd_per_million: float | None = None
    document_discovery_mode: Literal["structural", "model", "hybrid"] = "structural"
    document_discovery_max_documents: int = 20
    document_discovery_max_units: int = 240
    document_discovery_batch_units: int = 6
    document_discovery_max_batches: int = 20
    document_discovery_max_candidates: int = 40
    document_discovery_max_requests: int = 20
    document_discovery_max_tokens: int = 60000
    document_discovery_max_context_chars: int = 60000
    document_discovery_max_text_chars: int = 12000
    tender_root: Path | None = None
    tender_max_file_bytes: int = 20 * 1024 * 1024
    tender_max_text_chars: int = 2_000_000
    tender_max_pages: int = 200
    tender_max_sheets: int = 30
    tender_max_rows: int = 20000
    tender_max_parameters: int = 1000
    tender_max_active_jobs: int = 2
    tender_queue_name: str = "tender-extraction"
    tender_job_timeout_seconds: int = 900
    tender_queue_enabled: bool = False
    tender_model_mode: Literal["structural", "model", "hybrid"] = "structural"
    tender_model_classification: Literal["synthetic_public", "enterprise"] = "enterprise"
    tender_model_max_requests: int = 2
    tender_model_max_tokens: int = 30000
    tender_model_max_context_chars: int = 60000
    tender_model_batch_regions: int = 16
    evaluation_max_parameters: int = 1000
    evaluation_max_atomic_requirements: int = 5000
    evaluation_max_active_jobs: int = 2
    evaluation_retrieval_limit: int = 5
    evaluation_evidence_per_capability: int = 20
    evaluation_evidence_snippet_chars: int = 300
    evaluation_queue_name: str = "tender-evaluation"
    evaluation_job_timeout_seconds: int = 900
    evaluation_queue_enabled: bool = False
    evaluation_model_mode: Literal["structural", "hybrid"] = "hybrid"
    evaluation_model_classification: Literal["synthetic_public", "enterprise"] = "enterprise"
    evaluation_model_max_requests: int = 4
    evaluation_model_max_tokens: int = 40000
    evaluation_model_max_context_chars: int = 60000
    evaluation_model_batch_requirements: int = 12
    export_root: Path | None = None
    export_max_parameters: int = 1000
    export_max_evidence: int = 5000
    export_max_cell_chars: int = 20000
    export_max_file_bytes: int = 50 * 1024 * 1024
    export_max_active_jobs: int = 2
    # 下载凭证为短期凭证；过期后必须重新签发，不能继续访问历史导出文件。
    export_download_ttl_seconds: int = 3600
    export_job_timeout_seconds: int = 300
    export_queue_enabled: bool = False
    export_queue_name: str = "evaluation-export"

    @field_validator(
        "builder_input_usd_per_million", "builder_output_usd_per_million", mode="before"
    )
    @classmethod
    def nonnegative_price(cls, value):
        if value is None or value == "":
            return None
        number = float(value)
        import math

        if not math.isfinite(number) or number < 0:
            raise ValueError("模型计价必须为非负有限数")
        return number

    @property
    def document_store(self):
        return self.documents_root or self.archive_root.parent / "documents"

    @property
    def tender_store(self):
        return self.tender_root or self.archive_root.parent / "tender"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def export_store(self):
        return self.export_root or self.archive_root.parent / "exports"

    @property
    def control_store(self):
        return self.storage_control_root or self.archive_root.parent / ".control"

    @field_validator("read_user_tokens")
    @classmethod
    def valid_read_user_tokens(cls, value):
        raw = value.get_secret_value().strip()
        if not raw:
            return value
        try:
            mapping = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("读用户 Token 映射必须是 JSON 对象") from exc
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("读用户 Token 映射不能为空")
        for user_id, token in mapping.items():
            if not isinstance(user_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}", user_id
            ):
                raise ValueError("读用户 ID 只能使用字母、数字、点、下划线、@ 或连字符")
            if user_id in {"admin", "read", "read-shared"}:
                raise ValueError("读用户 ID 不能使用保留身份")
            if not isinstance(token, str) or len(token) < 32:
                raise ValueError("读用户 Token 至少 32 个字符")
        return value

    @field_validator("admin_token", "read_token")
    @classmethod
    def strong_token(cls, value):
        if len(value.get_secret_value()) < 32:
            raise ValueError("鉴权 Token 至少 32 个字符")
        return value

    @field_validator(
        "max_upload_bytes",
        "request_max_body_bytes",
        "multipart_overhead_bytes",
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
        "builder_job_timeout_seconds",
        "builder_module_timeout_seconds",
        "builder_max_modules",
        "builder_max_requests",
        "builder_max_tool_calls",
        "builder_max_tokens",
        "builder_max_context_chars",
        "builder_max_output_tokens",
        "document_discovery_max_documents",
        "document_discovery_max_units",
        "document_discovery_batch_units",
        "document_discovery_max_batches",
        "document_discovery_max_candidates",
        "document_discovery_max_requests",
        "document_discovery_max_tokens",
        "document_discovery_max_context_chars",
        "document_discovery_max_text_chars",
        "tender_max_file_bytes",
        "tender_max_text_chars",
        "tender_max_pages",
        "tender_max_sheets",
        "tender_max_rows",
        "tender_max_parameters",
        "tender_max_active_jobs",
        "tender_job_timeout_seconds",
        "tender_model_max_requests",
        "tender_model_max_tokens",
        "tender_model_max_context_chars",
        "tender_model_batch_regions",
        "evaluation_max_parameters",
        "evaluation_max_atomic_requirements",
        "evaluation_max_active_jobs",
        "evaluation_retrieval_limit",
        "evaluation_evidence_per_capability",
        "evaluation_evidence_snippet_chars",
        "evaluation_job_timeout_seconds",
        "evaluation_model_max_requests",
        "evaluation_model_max_tokens",
        "evaluation_model_max_context_chars",
        "evaluation_model_batch_requirements",
        "export_max_parameters",
        "export_max_evidence",
        "export_max_cell_chars",
        "export_max_file_bytes",
        "export_max_active_jobs",
        "export_download_ttl_seconds",
        "export_job_timeout_seconds",
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
        raw = self.read_user_tokens.get_secret_value().strip()
        if raw:
            mapping = json.loads(raw)
            values = [token for token in mapping.values()]
            if len(values) != len(set(values)):
                raise ValueError("读用户 Token 不能重复")
            reserved = {
                self.admin_token.get_secret_value(),
                self.read_token.get_secret_value(),
            }
            if reserved & set(values):
                raise ValueError("读用户 Token 不能与管理员或共享只读 Token 相同")
        return self


@lru_cache
def get_settings():
    return Settings()
