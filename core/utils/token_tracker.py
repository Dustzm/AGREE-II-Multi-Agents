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
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 全局追踪器实例
_global_tracker: Optional["TokenUsageTracker"] = None


def set_global_tracker(tracker: Optional["TokenUsageTracker"]) -> None:
    """设置全局追踪器"""
    global _global_tracker
    _global_tracker = tracker
    if tracker:
        logger.debug(f"全局 Token 追踪器已设置: task_id={tracker.task_id}")


def get_global_tracker() -> Optional["TokenUsageTracker"]:
    """获取全局追踪器"""
    return _global_tracker


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
            prompt = getattr(usage, 'prompt_tokens', None) or 0
            completion = getattr(usage, 'completion_tokens', None) or 0
            total = getattr(usage, 'total_tokens', None) or 0
            
            tracker._prompt_tokens += prompt
            tracker._response_tokens += completion
            tracker._total_tokens += total
            
            if prompt > 0 or completion > 0:
                logger.debug(f"LiteLLM Token 累计: prompt={prompt}, completion={completion}, total={total}")
    except Exception as e:
        logger.warning(f"解析 LiteLLM usage 失败: {e}")



@dataclass
class TokenUsage:
    """Token 使用统计数据"""
    prompt_token_count: int = 0
    response_token_count: int = 0
    total_token_count: int = 0
    
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
    
    def add_usage(self, usage_metadata) -> None:
        """
        添加一次 LLM 调用的 token 使用统计
        
        Args:
            usage_metadata: google.genai.types.UsageMetadata 对象
        """
        if usage_metadata is None:
            return
        
        try:
            # 使用 getattr 获取字段值，默认 0
            prompt = getattr(usage_metadata, 'prompt_token_count', None) or 0
            response = getattr(usage_metadata, 'response_token_count', None) or 0
            total = getattr(usage_metadata, 'total_token_count', None) or 0
            
            # 累加 token 计数
            self._prompt_tokens += prompt
            self._response_tokens += response
            self._total_tokens += total
            
            if prompt > 0 or response > 0:
                logger.debug(f"Token 累计: prompt={prompt}, response={response}, total={total}")
        except Exception as e:
            logger.warning(f"解析 usage_metadata 失败: {e}")

    
    def get_summary(self) -> TokenUsage:
        """获取当前统计汇总"""
        return TokenUsage(
            prompt_token_count=self._prompt_tokens,
            response_token_count=self._response_tokens,
            total_token_count=self._total_tokens,
        )
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "task_id": self.task_id,
            "prompt_token_count": self._prompt_tokens,
            "response_token_count": self._response_tokens,
            "total_token_count": self._total_tokens,
            "updated_at": datetime.now().isoformat(),
        }
    
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
        self._prompt_tokens = 0
        self._response_tokens = 0
        self._total_tokens = 0
    
    @property
    def prompt_tokens(self) -> int:
        return self._prompt_tokens
    
    @property
    def response_tokens(self) -> int:
        return self._response_tokens
    
    @property
    def total_tokens(self) -> int:
        return self._total_tokens
