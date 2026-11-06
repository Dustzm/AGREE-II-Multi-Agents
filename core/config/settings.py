"""
KG-Weaver 配置管理模块

使用 pydantic-settings 从环境变量加载配置
"""
import logging
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 获取项目根目录的 .env 文件路径
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
logger = logging.getLogger(__name__)


_LOG_LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL", "TRACE": "DEBUG"}
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def normalize_log_level(value: object, default: str = "INFO") -> str:
    """把常见别名（WARN/FATAL/TRACE）归一化；无法识别的值回退默认级别，避免启动即崩。"""
    raw = str(value or default).strip().upper()
    normalized = _LOG_LEVEL_ALIASES.get(raw, raw)
    return normalized if normalized in LOG_LEVELS else default


class LLMSettings(BaseSettings):
    """大模型配置 (OpenAI 兼容格式)"""
    
    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # 提供商标识
    provider: str = Field(
        default="vllm",
        description="LLM 提供商: vllm / openai_compatible / openai / zhipu / dashscope / anthropic / deepseek / gemini"
    )
    
    # API 基础地址
    base_url: str = Field(
        default="",
        description="大模型 API 基础地址"
    )
    
    # API Key
    api_key: str = Field(
        default="",
        description="API Key (如果服务需要认证)"
    )
    
    # 默认模型名称
    model_name: str = Field(
        default="qwen2.5",
        description="默认使用的模型名称"
    )
    
    context_window: int | None = Field(default=None, gt=0, description="模型上下文窗口（tokens）")

    # 读取超时：请求以流式发送，超时约束的是两段数据之间的间隔，而不是整次请求的总耗时
    timeout: int = Field(
        default=120,
        description="读取超时时间 (秒)：流式响应中相邻两段数据的最长间隔"
    )
    
    # 模型是否支持视觉（多模态）能力
    is_vision_model: bool = Field(
        default=True,
        description="当前模型是否具备视觉能力（VLM），用于控制条目17是否注入图片"
    )

    # 结构化输出模式（.env 中的 LLM_STRUCTURED_OUTPUT）
    structured_output: str | None = Field(
        default=None,
        description="强制结构化输出模式: auto/json_schema/guided_json/json_object"
    )


class MinerUSettings(BaseSettings):
    """MinerU 远程服务配置 (VLM HTTP Client)"""
    
    model_config = SettingsConfigDict(
        env_prefix="MINERU_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # VLM 服务地址 (OpenAI 兼容格式)
    server_url: str = Field(
        default="http://127.0.0.1:30000",
        description="VLM 服务地址"
    )

    # 模型名称 (统一模型平台可能需要显式指定)
    model_name: str = Field(
        default="",
        description="VLM 模型名称 (可选)"
    )
    
    # API Key (用于 Authorization Bearer 认证)
    api_key: str = Field(
        default="",
        description="VLM 服务 API Key"
    )
    
    # HTTP 超时时间
    http_timeout: int = Field(
        default=600,
        description="HTTP 请求超时时间 (秒)"
    )
    
    # 最大并发请求数
    max_concurrency: int = Field(
        default=100,
        description="最大并发请求数"
    )

    # 解析重试次数
    parse_retry_attempts: int = Field(
        default=3,
        description="PDF 解析失败重试次数"
    )

    # 重试间隔（秒）
    parse_retry_delay: int = Field(
        default=5,
        description="重试间隔时间（秒）"
    )


class ASRSettings(BaseSettings):
    """ASR 语音识别服务配置 (OpenAI Whisper 兼容格式)"""
    
    model_config = SettingsConfigDict(
        env_prefix="ASR_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # ASR 服务地址 (包含 /v1)
    server_url: str = Field(
        default="http://127.0.0.1:8000/v1",
        description="ASR 服务地址"
    )
    
    # 模型名称
    model_name: str = Field(
        default="whisper-large-v3",
        description="ASR 模型名称"
    )
    
    # API Key
    api_key: str = Field(
        default="",
        description="ASR 服务 API Key (可选)"
    )
    
    # 请求超时时间
    timeout: int = Field(
        default=80,
        description="请求超时时间 (秒)"
    )
    
    # 音频分片时长 (秒)
    chunk_duration: int = Field(
        default=60,
        description="音频分片时长 (秒)"
    )
    
    # 分片重叠时长 (秒)
    chunk_overlap: int = Field(
        default=5,
        description="分片重叠时长 (秒)"
    )
    
    # 最大并发请求数
    max_concurrency: int = Field(
        default=1,
        description="ASR 最大并发请求数"
    )


class URLAttachmentSettings(BaseSettings):
    """URL 附件处理配置"""
    
    model_config = SettingsConfigDict(
        env_prefix="URL_ATTACHMENT_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # 是否启用 URL 附件处理
    enabled: bool = Field(
        default=True,
        description="是否启用 URL 附件处理"
    )
    
    # 下载超时时间（秒）
    timeout: int = Field(
        default=30,
        description="下载超时时间（秒）"
    )
    
    # 最大并发数
    max_concurrency: int = Field(
        default=3,
        description="最大下载数"
    )
    
    # 缓存保留天数
    cache_days: int = Field(
        default=7,
        description="缓存保留天数"
    )
    
    # 最大文件大小（字节）
    max_size: int = Field(
        default=10 * 1024 * 1024,
        description="最大文件大小（字节）"
    )
    
    # httpx 超时（秒）
    httpx_timeout: int = Field(
        default=5,
        description="httpx 超时时间（秒）"
    )
    
    # 重试次数
    retry_attempts: int = Field(
        default=2,
        description="重试次数"
    )
    
    # 重试间隔（秒）
    retry_delay: int = Field(
        default=1,
        description="重试间隔（秒）"
    )
    
    # 最大内容长度（字符）
    max_content_length: int = Field(
        default=50000,
        description="最大内容长度（字符）"
    )
    
    # 是否启用 LLM 分类
    enable_llm_classification: bool = Field(
        default=True,
        description="是否启用 LLM 分类"
    )


class AppSettings(BaseSettings):
    """应用配置"""
    
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # 运行环境
    env: Literal["development", "production"] = Field(
        default="development",
        alias="APP_ENV",
        description="运行环境"
    )
    
    # 日志级别（接受 LOG_LEVEL 与历史变量 AMA_LOG_LEVEL）
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "AMA_LOG_LEVEL"),
        description="日志级别（WARN/FATAL/TRACE 等常见别名会归一化）"
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> str:
        return normalize_log_level(value)
    
    # 输入目录 (PDF 文件输入)
    dir_input: str = Field(
        default="./input",
        alias="DIR_INPUT",
        description="输入目录 (存放待处理的 PDF 文件)"
    )
    
    # 输出目录 (任务结果输出)
    dir_output: str = Field(
        default="./output",
        alias="DIR_OUTPUT",
        description="输出目录 (任务结果存放位置)"
    )
    
    # 评价并发配置
    appraisal_max_concurrency: int = Field(
        default=5,
        alias="APPRAISAL_MAX_CONCURRENCY",
        description="单个指南任务的条目评价最大并发数",
        ge=1,
        le=20
    )

    appraisal_pool_max_concurrency: int = Field(
        default=10,
        alias="APPRAISAL_POOL_MAX_CONCURRENCY",
        description="Appraiser 全局线程池最大并发数",
        ge=1,
        le=100
    )

    llm_global_max_concurrency: int = Field(
        default=60,
        alias="LLM_GLOBAL_MAX_CONCURRENCY",
        description="进程内所有 LLM 调用共享的最大并发数",
        ge=1,
        le=500,
    )

    batch_reporter_timeout: int = Field(
        default=1500,
        alias="BATCH_REPORTER_TIMEOUT",
        description="批量任务 Reporter 单次尝试超时时间（秒）",
        ge=30,
        le=7200,
    )

    batch_mineru_health_timeout: int = Field(
        default=15,
        alias="BATCH_MINERU_HEALTH_TIMEOUT",
        description="批量任务 MinerU 健康检查超时时间（秒）",
        ge=1,
        le=300,
    )

    batch_mineru_parse_timeout: int = Field(
        default=600,
        alias="BATCH_MINERU_PARSE_TIMEOUT",
        description="批量任务 MinerU 单次解析超时时间（秒）",
        ge=30,
        le=7200,
    )

    batch_mineru_max_concurrency: int = Field(
        default=2,
        alias="BATCH_MINERU_MAX_CONCURRENCY",
        description="进程内并行解析的 PDF 文档数",
        ge=1,
        le=20,
    )

    batch_reporter_max_concurrency: int = Field(
        default=2,
        alias="BATCH_REPORTER_MAX_CONCURRENCY",
        description="进程内并行生成报告的任务数",
        ge=1,
        le=20,
    )

    batch_blocking_max_concurrency: int = Field(
        default=4,
        alias="BATCH_BLOCKING_MAX_CONCURRENCY",
        description="文件复制、缓存恢复和文档预处理的共享线程数",
        ge=1,
        le=32,
    )

    batch_item_llm_timeout: int = Field(
        default=3600,
        alias="BATCH_ITEM_LLM_TIMEOUT",
        description="批量任务单个条目评价硬超时时间（秒）",
        ge=30,
        le=16000,
    )

    batch_worker_lease_seconds: int = Field(
        default=120,
        alias="BATCH_WORKER_LEASE_SECONDS",
        description="批量 Worker 租约有效期（秒）",
        ge=30,
        le=3600,
    )

    batch_worker_heartbeat_seconds: int = Field(
        default=10,
        alias="BATCH_WORKER_HEARTBEAT_SECONDS",
        description="批量 Worker 租约续期间隔（秒）",
        ge=1,
        le=300,
    )

    batch_worker_recovery_grace_seconds: int = Field(
        default=60,
        alias="BATCH_WORKER_RECOVERY_GRACE_SECONDS",
        description="批量 Worker 租约过期后的回收宽限期（秒）",
        ge=0,
        le=3600,
    )

    analysis_engine: Literal["llm", "rule", "hybrid"] = Field(
        default="hybrid",
        alias="ANALYSIS_ENGINE",
        description="评价引擎：llm / rule / hybrid",
    )

    rule_engine_fallback_on_error: bool = Field(
        default=True,
        alias="RULE_ENGINE_FALLBACK_ON_ERROR",
        description="规则引擎评价失败时是否回退到原 appraiser 流程",
    )

    rule_engine_batch_size: int = Field(
        default=5,
        alias="RULE_ENGINE_BATCH_SIZE",
        description="规则引擎抽取阶段 batch chunk 数量",
        ge=1,
        le=50,
    )

    rule_strict_items: str = Field(
        default="item_11,item_12,item_18,item_19,item_21",
        alias="RULE_STRICT_ITEMS",
        description="规则引擎严格条目（逗号分隔），仅 legacy 回退策略下禁止回退",
    )

    rule_fallback_policy: Literal["legacy", "degraded"] = Field(
        default="degraded",
        alias="RULE_FALLBACK_POLICY",
        description="规则引擎失败处理策略：legacy=严格条目直接失败；degraded=允许回退并标记 degraded",
    )

    rule_routing_excluded_labels: str = Field(
        default="Irrelevant",
        alias="RULE_ROUTING_EXCLUDED_LABELS",
        description="默认从规则引擎路由中排除的语义标签（逗号分隔）；附录不默认排除，由领域标签映射决定是否参与证据",
    )

    rule_appendix_score_factor: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        alias="RULE_APPENDIX_SCORE_FACTOR",
        description="附录 chunk 在加权路由中的得分折扣（1=不折扣），避免附录摘要挤占正文证据",
    )

    rule_appendix_max_chunks: int = Field(
        default=0,
        ge=0,
        alias="RULE_APPENDIX_MAX_CHUNKS",
        description="单条目最多入选的附录 chunk 数；0=自动（min(3, max(2, max_chunks//4))）",
    )

    rule_report_failures: bool = Field(
        default=True,
        alias="RULE_REPORT_FAILURES",
        description="条目评价结束后写出 failure_report.json",
    )

    llm_retry_quota_transport: int = Field(
        default=3,
        alias="LLM_RETRY_QUOTA_TRANSPORT",
        description="单条目传输层重试配额",
        ge=0,
    )

    llm_retry_quota_truncation: int = Field(
        default=3,
        alias="LLM_RETRY_QUOTA_TRUNCATION",
        description="单条目截断拆分配额",
        ge=0,
    )

    llm_retry_quota_repair: int = Field(
        default=6,
        alias="LLM_RETRY_QUOTA_REPAIR",
        description="单条目校验修复总配额",
        ge=0,
    )

    llm_retry_quota_repair_stage: int = Field(
        default=2,
        alias="LLM_RETRY_QUOTA_REPAIR_STAGE",
        description="单条目单个修复阶段（抽取/校验/解释）的修复配额，避免抽取修复耗尽后续阶段",
        ge=0,
    )

    llm_retry_quota_total: int = Field(
        default=8,
        alias="LLM_RETRY_QUOTA_TOTAL",
        description="单条目恢复总配额",
        ge=0,
    )

    rule_extraction_policy: Literal["strict", "keep_valid", "quarantine"] = Field(
        default="keep_valid",
        alias="RULE_EXTRACTION_POLICY",
        description=(
            "单批抽取对象校验失败策略：strict=整批失败, "
            "keep_valid=保留通过对象并丢弃非法对象, quarantine=丢弃并记录非法对象"
        ),
    )

    rule_extraction_repair_attempts: int = Field(
        default=1,
        alias="RULE_EXTRACTION_REPAIR_ATTEMPTS",
        description="抽取对象校验失败后允许的修复请求次数（0 表示只丢弃不修复）",
        ge=0,
        le=5,
    )
    
    # 标签分类并发配置
    labeler_max_concurrency: int = Field(
        default=8,
        alias="LABELER_MAX_CONCURRENCY",
        description="标签分类(语义池构建)最大并发数",
        ge=1,
        le=100
    )
    
    # 数据存储根目录 (向后兼容，实际使用 dir_output)
    @property
    def data_root(self) -> str:
        """向后兼容的数据根目录属性 (绝对路径)"""
        return str(self.path_output)

    @property
    def path_input(self) -> Path:
        """解析后的输入目录 (绝对路径)"""
        path = Path(self.dir_input)
        if path.is_absolute():
            return path
        return _PROJECT_ROOT / path

    @property
    def path_output(self) -> Path:
        """解析后的输出目录 (绝对路径)"""
        path = Path(self.dir_output)
        if path.is_absolute():
            return path
        return _PROJECT_ROOT / path


class AuthSettings(BaseSettings):
    """Web 认证配置"""

    model_config = SettingsConfigDict(
        env_prefix="AMA_AUTH_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    enabled: bool = Field(
        default=True,
        description="是否启用 Web 登录认证"
    )

    secret: str = Field(
        default="ama-default-auth-secret",
        description="会话签名密钥"
    )

    session_ttl_hours: int = Field(
        default=12,
        description="登录会话有效期（小时）",
        ge=1,
        le=1200
    )

    users_file: str = Field(
        default="users.json",
        description="账号文件路径，默认使用项目根目录下的 users.json"
    )


class Settings(BaseSettings):
    """全局配置聚合"""
    
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    llm: LLMSettings = Field(default_factory=LLMSettings)
    mineru: MinerUSettings = Field(default_factory=MinerUSettings)
    asr: ASRSettings = Field(default_factory=ASRSettings)
    app: AppSettings = Field(default_factory=AppSettings)
    url_attachment: URLAttachmentSettings = Field(default_factory=URLAttachmentSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    llm_config_secret: str = Field(
        default="",
        alias="LLM_CONFIG_SECRET",
        repr=False,
        description="运行时 LLM 配置加密密钥",
    )


# 可刷新的配置单例
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置（可刷新的单例）"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
        _apply_runtime_llm_overrides(_settings_instance)
    return _settings_instance


def refresh_settings() -> Settings:
    """强制刷新配置，重新从 .env 文件加载"""
    global _settings_instance
    _settings_instance = None
    return get_settings()


def env_llm_context_window() -> int | None:
    """`LLM_CONTEXT_WINDOW` 的 .env 基线值，忽略管理台覆盖层。"""
    return LLMSettings().context_window


def _apply_runtime_llm_overrides(settings: Settings) -> None:
    """将 SQLite 中的运行时 LLM 配置覆盖到当前 settings。"""
    from .runtime_store import get_runtime_llm_config

    runtime_config = get_runtime_llm_config(settings.llm_config_secret)
    if not runtime_config:
        return

    for key in ("provider", "base_url", "api_key", "model_name", "timeout", "is_vision_model", "context_window"):
        value = runtime_config.get(key)
        # 文本项的空字符串代表“管理台没有填写”，不应覆盖 .env 里的有效配置。
        if key in ("provider", "base_url", "api_key", "model_name") and isinstance(value, str) and not value.strip():
            continue
        if value is not None or (key == "context_window" and key in runtime_config):
            setattr(settings.llm, key, value)


def update_env_file(updates: dict[str, str]) -> None:
    """
    更新 .env 文件中的配置项
    
    Args:
        updates: 要更新的键值对，如 {"LLM_BASE_URL": "http://..."}
    """
    env_path = _ENV_FILE
    
    if not env_path.exists():
        # 文件不存在则直接写入
        lines = [f"{k}={v}\n" for k, v in updates.items()]
        env_path.write_text("".join(lines), encoding="utf-8")
        return
    
    # 读取现有内容
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    
    updated_keys = set()
    new_lines = []
    
    for line in lines:
        stripped = line.strip()
        # 跳过注释和空行
        if stripped.startswith("#") or not stripped:
            new_lines.append(line)
            continue
        
        # 匹配 KEY=VALUE 格式
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
                continue
        
        new_lines.append(line)
    
    # 追加未找到的新键
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")
    
    env_path.write_text("".join(new_lines), encoding="utf-8")
