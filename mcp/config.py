"""AMA MCP 环境配置。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MCPSettings(BaseSettings):
    """从 ``AMA_MCP_*`` 环境变量加载 MCP 配置。"""

    model_config = SettingsConfigDict(
        env_prefix="AMA_MCP_",
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = False
    api_key: str = Field(default="", repr=False)
    max_request_bytes: int = Field(default=15 * 1024 * 1024, ge=1024)
    max_inflight_requests: int = Field(default=8, ge=1, le=128)
    max_pdf_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    max_pdf_pages: int = Field(default=1000, ge=1)
    max_concurrent_jobs: int = Field(default=4, ge=1, le=4)
    max_queued_jobs: int = Field(default=32, ge=1, le=1000)
    lease_seconds: int = Field(default=120, ge=30, le=3600)
    heartbeat_seconds: int = Field(default=10, ge=1, le=300)
    recovery_limit: int = Field(default=1, ge=0, le=10)
    poll_interval_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    min_free_disk_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=0)
    input_retention_hours: int = Field(default=24, ge=1)
    incomplete_retention_days: int = Field(default=7, ge=1)
    result_retention_days: int = Field(default=30, ge=1)
    cleanup_interval_seconds: int = Field(default=3600, ge=60)

    def validate_runtime(self) -> None:
        """校验启用 MCP 时必须满足的运行条件。"""
        if self.enabled and not self.api_key.strip():
            raise RuntimeError("AMA_MCP_ENABLED=true 时必须配置 AMA_MCP_API_KEY")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise RuntimeError("AMA_MCP_HEARTBEAT_SECONDS 必须小于 AMA_MCP_LEASE_SECONDS")


@lru_cache(maxsize=1)
def get_mcp_settings() -> MCPSettings:
    """返回进程内缓存的 MCP 配置。"""
    return MCPSettings()
