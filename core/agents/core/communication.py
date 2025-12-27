"""
智能体通信管理模块

提供智能体间显式通信的结构化消息类
支持消息创建、序列化和通信历史记录（接口预留）
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """
    智能体间通信消息
    
    用于规范化智能体之间的任务指令传递
    
    Attributes:
        from_agent: 来源智能体名称
        to_agent: 目标智能体名称
        instruction: 指令内容
        task_id: 本次任务ID
        context_buffer: 本次指令涉及的记忆文件列表
        timestamp: 消息创建时间
        message_id: 消息唯一标识
        metadata: 额外元数据
    """
    from_agent: str
    to_agent: str
    instruction: str
    task_id: str
    context_buffer: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    message_id: str = field(default_factory=lambda: f"msg-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "from": self.from_agent,
            "to": self.to_agent,
            "instruction": self.instruction,
            "task_id": self.task_id,
            "context_buffer": self.context_buffer,
            "timestamp": self.timestamp,
            "message_id": self.message_id,
            "metadata": self.metadata,
        }
    
    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentMessage":
        """从字典创建消息"""
        return cls(
            from_agent=data.get("from", ""),
            to_agent=data.get("to", ""),
            instruction=data.get("instruction", ""),
            task_id=data.get("task_id", ""),
            context_buffer=data.get("context_buffer", []),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            message_id=data.get("message_id", ""),
            metadata=data.get("metadata", {}),
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "AgentMessage":
        """从 JSON 字符串创建消息"""
        return cls.from_dict(json.loads(json_str))
    
    def __str__(self) -> str:
        """友好的字符串表示"""
        return f"[{self.from_agent} -> {self.to_agent}] {self.instruction[:50]}..."


class AgentCommunicationManager:
    """
    智能体通信管理器
    
    负责：
    1. 创建和发送结构化消息
    2. 管理通信历史（TODO: 后续实现持久化）
    3. 提供消息查询接口
    """
    
    def __init__(self, task_id: str, weaver_dir: Optional[str] = None):
        """
        初始化通信管理器
        
        Args:
            task_id: 任务ID
            weaver_dir: .ama 目录路径（用于存储通信历史）
        """
        self.task_id = task_id
        self.ama_dir = Path(weaver_dir) if weaver_dir else None
        self._message_history: list[AgentMessage] = []
        
        # 如果指定了 weaver_dir，创建通信历史目录
        if self.ama_dir:
            self.history_dir = self.ama_dir / "communications"
            self.history_dir.mkdir(parents=True, exist_ok=True)
    
    def create_message(
        self,
        from_agent: str,
        to_agent: str,
        instruction: str,
        context_buffer: Optional[list[str]] = None,
        metadata: Optional[dict] = None
    ) -> AgentMessage:
        """
        创建新的通信消息
        
        Args:
            from_agent: 来源智能体
            to_agent: 目标智能体
            instruction: 指令内容
            context_buffer: 相关的记忆文件列表
            metadata: 额外元数据
            
        Returns:
            AgentMessage 实例
        """
        message = AgentMessage(
            from_agent=from_agent,
            to_agent=to_agent,
            instruction=instruction,
            task_id=self.task_id,
            context_buffer=context_buffer or [],
            metadata=metadata or {},
        )
        
        # 记录到历史
        self._message_history.append(message)
        
        logger.info(f"创建消息: {message}")
        return message
    
    def get_history(self) -> list[AgentMessage]:
        """获取通信历史"""
        return self._message_history.copy()
    
    def get_messages_from(self, agent_name: str) -> list[AgentMessage]:
        """获取来自指定智能体的所有消息"""
        return [m for m in self._message_history if m.from_agent == agent_name]
    
    def get_messages_to(self, agent_name: str) -> list[AgentMessage]:
        """获取发送给指定智能体的所有消息"""
        return [m for m in self._message_history if m.to_agent == agent_name]
    
    def get_last_message(self) -> Optional[AgentMessage]:
        """获取最后一条消息"""
        return self._message_history[-1] if self._message_history else None
    
    # ============ TODO: 通信历史持久化接口 ============
    
    def save_history(self) -> str:
        """
        保存通信历史到文件
        
        TODO: 后续实现
        - 将通信历史保存到 .ama/communications/ 目录
        - 支持 JSONL 格式存储
        - 支持增量追加
        
        Returns:
            保存的文件路径
        """
        # TODO: 实现持久化逻辑
        logger.warning("save_history: 功能待实现")
        return ""
    
    def load_history(self) -> list[AgentMessage]:
        """
        从文件加载通信历史
        
        TODO: 后续实现
        - 从 .ama/communications/ 目录读取
        - 支持按时间范围过滤
        - 支持按智能体过滤
        
        Returns:
            消息列表
        """
        # TODO: 实现加载逻辑
        logger.warning("load_history: 功能待实现")
        return []
    
    def export_history(self, format: str = "json") -> str:
        """
        导出通信历史
        
        TODO: 后续实现
        - 支持 JSON、Markdown、HTML 格式
        - 用于调试和可视化
        
        Args:
            format: 导出格式 ("json" | "markdown" | "html")
            
        Returns:
            导出的内容字符串
        """
        # TODO: 实现导出逻辑
        logger.warning("export_history: 功能待实现")
        return ""


# ============ 工具函数：供 Agent 调用 ============

# 全局通信管理器缓存
_communication_managers: dict[str, AgentCommunicationManager] = {}


def get_communication_manager(task_id: str, weaver_dir: Optional[str] = None) -> AgentCommunicationManager:
    """
    获取或创建通信管理器
    
    Args:
        task_id: 任务ID
        weaver_dir: .ama 目录路径
        
    Returns:
        AgentCommunicationManager 实例
    """
    if task_id not in _communication_managers:
        _communication_managers[task_id] = AgentCommunicationManager(task_id, weaver_dir)
    return _communication_managers[task_id]


def send_agent_message(
    task_id: str,
    from_agent: str,
    to_agent: str,
    instruction: str,
    context_buffer: Optional[list[str]] = None
) -> dict:
    """
    发送智能体消息（供工具调用）
    
    Args:
        task_id: 任务ID
        from_agent: 来源智能体
        to_agent: 目标智能体
        instruction: 指令内容
        context_buffer: 相关的记忆文件
        
    Returns:
        dict: {"status": "success/error", "message": AgentMessage.to_dict(), "error": str}
    """
    try:
        manager = get_communication_manager(task_id)
        message = manager.create_message(
            from_agent=from_agent,
            to_agent=to_agent,
            instruction=instruction,
            context_buffer=context_buffer,
        )
        
        return {
            "status": "success",
            "message": message.to_dict(),
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "message": {},
            "error": str(e)
        }


def get_agent_messages(task_id: str, agent_name: Optional[str] = None, direction: str = "all") -> dict:
    """
    获取智能体消息历史（供工具调用）
    
    Args:
        task_id: 任务ID
        agent_name: 智能体名称（可选）
        direction: 方向 ("from" | "to" | "all")
        
    Returns:
        dict: {"status": "success/error", "messages": list[dict], "error": str}
    """
    try:
        manager = get_communication_manager(task_id)
        
        if agent_name:
            if direction == "from":
                messages = manager.get_messages_from(agent_name)
            elif direction == "to":
                messages = manager.get_messages_to(agent_name)
            else:
                messages = [m for m in manager.get_history() 
                           if m.from_agent == agent_name or m.to_agent == agent_name]
        else:
            messages = manager.get_history()
        
        return {
            "status": "success",
            "messages": [m.to_dict() for m in messages],
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "messages": [],
            "error": str(e)
        }
