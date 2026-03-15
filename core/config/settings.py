"""
KG-Weaver 配置管理模块

使用 pydantic-settings 从环境变量加载配置
"""
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 获取项目根目录的 .env 文件路径
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


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
        description="LLM 提供商: vllm / openai_compatible / openai / zhipu / anthropic / deepseek / gemini"
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
    
    # 请求超时时间
    timeout: int = Field(
        default=120,
        description="请求超时时间 (秒)"
    )
    
    # 模型是否支持视觉（多模态）能力
    is_vision_model: bool = Field(
        default=True,
        description="当前模型是否具备视觉能力（VLM），用于控制条目17是否注入图片"
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
    
    # Playwright 超时（秒）
    playwright_timeout: int = Field(
        default=20,
        description="Playwright 超时时间（秒）"
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
    
    # 日志级别
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="日志级别"
    )
    
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
        description="条目评价最大并发数",
        ge=1,
        le=20
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


# 可刷新的配置单例
_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置（可刷新的单例）"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def refresh_settings() -> Settings:
    """强制刷新配置，重新从 .env 文件加载"""
    global _settings_instance
    _settings_instance = None
    return get_settings()


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
