"""
Token 使用统计工具

提供 TokenUsageTracker 类用于统计 LLM 调用的 token 消耗：
- 累计 prompt_token_count、response_token_count、total_token_count
- 保存到 JSON 文件
- 转换为字典格式

全局追踪器支持：
- set_global_tracker() / get_global_tracker() 用于跨模块共享
- add_litellm_usage() 用于从 LiteLLM response 添加统计
"""
import json
import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 每个异步任务独立追踪，线程池通过 contextvars 上下文传播。
_global_tracker: ContextVar[Optional["TokenUsageTracker"]] = ContextVar(
    "token_usage_tracker",
    default=None,
)


def set_global_tracker(tracker: Optional["TokenUsageTracker"]) -> None:
    """设置全局追踪器"""
    _global_tracker.set(tracker)
    if tracker:
        logger.debug(f"全局 Token 追踪器已设置: task_id={tracker.task_id}")


def get_global_tracker() -> Optional["TokenUsageTracker"]:
    """获取全局追踪器"""
    return _global_tracker.get()


def _usage_value(source, key: str, default=None):
    """从对象或字典中读取 usage 字段。"""
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _non_negative_int(value, default: int = 0) -> int:
    """把 token 字段规范化为非负整数。"""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return default


def _optional_non_negative_int(value) -> Optional[int]:
    """把可选 token 字段规范化为非负整数或 None。"""
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def add_litellm_usage(response) -> None:
    """
    从 LiteLLM response 添加 token 统计到全局追踪器
    
    Args:
        response: LiteLLM ModelResponse 对象
    """
    tracker = get_global_tracker()
    if tracker is None:
        return
    
    try:
        usage = getattr(response, 'usage', None)
        if usage:
            tracker.add_openai_usage(usage)
    except Exception as e:
        logger.warning(f"解析 LiteLLM usage 失败: {e}")



@dataclass
class TokenUsage:
    """Token 使用统计数据"""
    prompt_token_count: int = 0
    response_token_count: int = 0
    total_token_count: int = 0
    cached_input_token_count: Optional[int] = None
    uncached_input_token_count: Optional[int] = None
    elapsed_seconds: float = 0.0
    
    def to_dict(self) -> dict:
        return asdict(self)


class TokenUsageTracker:
    """
    Token 使用统计追踪器
    
    在 ADK 事件循环中捕获 usage_metadata，累计统计 token 消耗。
    支持保存到 JSON 文件和转换为字典格式。
    """
    
    def __init__(self, task_id: Optional[str] = None):
        """
        初始化追踪器
        
        Args:
            task_id: 可选的任务ID，用于保存时标识
        """
        self.task_id = task_id
        self._prompt_tokens = 0
        self._response_tokens = 0
        self._total_tokens = 0
        self._cached_input_tokens = 0
        self._has_cache_usage = False
        self._has_unknown_cache_usage = False
        self._started_at = time.monotonic()
        self._elapsed_seconds: Optional[float] = None
        self._lock = threading.RLock()

    def _add_token_counts(
        self,
        *,
        prompt_tokens: int,
        response_tokens: int,
        total_tokens: int,
        cached_input_tokens: Optional[int],
    ) -> None:
        """累加一次已归一化的 token 统计。"""
        with self._lock:
            self._prompt_tokens += prompt_tokens
            self._response_tokens += response_tokens
            self._total_tokens += total_tokens

            if cached_input_tokens is None:
                self._has_unknown_cache_usage = True
            else:
                self._has_cache_usage = True
                self._cached_input_tokens += cached_input_tokens

    def add_openai_usage(self, usage) -> None:
        """
        添加 OpenAI-compatible/LiteLLM/vLLM usage 统计。

        Args:
            usage: 包含 prompt_tokens、completion_tokens 等字段的对象或字典。
        """
        prompt = _non_negative_int(_usage_value(usage, "prompt_tokens"))
        completion = _non_negative_int(_usage_value(usage, "completion_tokens"))
        total = _non_negative_int(_usage_value(usage, "total_tokens"))
        prompt_details = _usage_value(usage, "prompt_tokens_details")
        cached = _optional_non_negative_int(_usage_value(prompt_details, "cached_tokens"))

        self._add_token_counts(
            prompt_tokens=prompt,
            response_tokens=completion,
            total_tokens=total,
            cached_input_tokens=cached,
        )

        if prompt > 0 or completion > 0:
            logger.debug(
                "OpenAI-compatible Token 累计: prompt=%s, completion=%s, total=%s, cached=%s",
                prompt,
                completion,
                total,
                cached,
            )
    
    def add_usage(self, usage_metadata) -> None:
        """
        添加一次 LLM 调用的 token 使用统计
        
        Args:
            usage_metadata: google.genai.types.UsageMetadata 对象
        """
        if usage_metadata is None:
            return
        
        try:
            prompt = _non_negative_int(_usage_value(usage_metadata, 'prompt_token_count'))
            legacy_response = _usage_value(usage_metadata, 'response_token_count')
            candidates = _usage_value(usage_metadata, 'candidates_token_count')
            thoughts = _usage_value(usage_metadata, 'thoughts_token_count')
            if candidates is not None or thoughts is not None:
                response = _non_negative_int(candidates) + _non_negative_int(thoughts)
            else:
                response = _non_negative_int(legacy_response)
            total = _non_negative_int(_usage_value(usage_metadata, 'total_token_count'))
            cached = _optional_non_negative_int(
                _usage_value(usage_metadata, 'cached_content_token_count')
            )

            self._add_token_counts(
                prompt_tokens=prompt,
                response_tokens=response,
                total_tokens=total,
                cached_input_tokens=cached,
            )

            if prompt > 0 or response > 0:
                logger.debug(
                    "Token 累计: prompt=%s, response=%s, total=%s, cached=%s",
                    prompt,
                    response,
                    total,
                    cached,
                )
        except Exception as e:
            logger.warning(f"解析 usage_metadata 失败: {e}")

    
    def get_summary(self) -> TokenUsage:
        """获取当前统计汇总"""
        with self._lock:
            return TokenUsage(
                prompt_token_count=self._prompt_tokens,
                response_token_count=self._response_tokens,
                total_token_count=self._total_tokens,
                cached_input_token_count=self.cached_input_tokens,
                uncached_input_token_count=self.uncached_input_tokens,
                elapsed_seconds=self.elapsed_seconds,
            )
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        with self._lock:
            return {
                "task_id": self.task_id,
                "prompt_token_count": self._prompt_tokens,
                "response_token_count": self._response_tokens,
                "total_token_count": self._total_tokens,
                "cached_input_token_count": self.cached_input_tokens,
                "uncached_input_token_count": self.uncached_input_tokens,
                "elapsed_seconds": self.elapsed_seconds,
                "updated_at": datetime.now().isoformat(),
            }

    def set_elapsed_seconds(self, elapsed_seconds: float) -> None:
        """设置外部计算出的任务耗时秒数。"""
        with self._lock:
            self._elapsed_seconds = max(0.0, round(float(elapsed_seconds), 3))
    
    def save(self, path: Path) -> bool:
        """
        保存统计结果到 JSON 文件
        
        Args:
            path: 保存的文件路径
            
        Returns:
            是否保存成功
        """
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            
            logger.info(f"Token 统计已保存: {path} (total: {self._total_tokens})")
            return True
        except Exception as e:
            logger.error(f"保存 Token 统计失败: {e}")
            return False
    
    def reset(self) -> None:
        """重置统计数据"""
        with self._lock:
            self._prompt_tokens = 0
            self._response_tokens = 0
            self._total_tokens = 0
            self._cached_input_tokens = 0
            self._has_cache_usage = False
            self._has_unknown_cache_usage = False
            self._started_at = time.monotonic()
            self._elapsed_seconds = None
    
    @property
    def prompt_tokens(self) -> int:
        with self._lock:
            return self._prompt_tokens
    
    @property
    def response_tokens(self) -> int:
        with self._lock:
            return self._response_tokens
    
    @property
    def total_tokens(self) -> int:
        with self._lock:
            return self._total_tokens

    @property
    def cached_input_tokens(self) -> Optional[int]:
        with self._lock:
            if self._has_unknown_cache_usage or not self._has_cache_usage:
                return None
            return self._cached_input_tokens

    @property
    def uncached_input_tokens(self) -> Optional[int]:
        with self._lock:
            cached = self.cached_input_tokens
            if cached is None:
                return None
            return max(0, self._prompt_tokens - cached)

    @property
    def elapsed_seconds(self) -> float:
        with self._lock:
            if self._elapsed_seconds is not None:
                return self._elapsed_seconds
            return round(max(0.0, time.monotonic() - self._started_at), 3)
