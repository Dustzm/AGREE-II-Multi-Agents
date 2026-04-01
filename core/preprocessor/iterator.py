"""
证据迭代器

按文章原始顺序遍历 chunks，提供迭代评价支持
"""
import logging
from typing import List, Iterator

from .pojo import SemanticChunk, SemanticLabel
from .evidence import DOMAIN_LABEL_MAPPING, order_chunks_for_domain

logger = logging.getLogger(__name__)


class EvidenceIterator:
    """
    证据迭代器 - 按文章原始顺序遍历 chunks（保持逻辑上下文）
    
    用于迭代评价：逐个遍历与指定领域相关的 chunks
    """
    
    def __init__(self, chunks: List[SemanticChunk], domain_id: int):
        """
        初始化迭代器
        
        Args:
            chunks: 语义块列表 (保持原始顺序)
            domain_id: 领域 ID (1-6)
        """
        # 获取该领域允许的语义标签
        relevant_labels = DOMAIN_LABEL_MAPPING.get(domain_id, [])
        
        # 调试：打印第一个 chunk 的 label 信息
        if chunks:
            first_chunk = chunks[0]
            logger.debug(
                f"调试 - 第一个 chunk: label={first_chunk.label!r}, "
                f"type={type(first_chunk.label).__name__}, "
                f"relevant_labels={relevant_labels}, "
                f"in_check={first_chunk.label in relevant_labels}"
            )
        
        # 过滤并按领域要求排序：
        # - 默认保持原始顺序
        # - 对定义了优先级的领域（如 domain 6）先按标签优先级，再保持同标签内原始顺序
        self.chunks = order_chunks_for_domain(chunks, domain_id)
        
        self.domain_id = domain_id
        self.current_index = 0
        
        logger.info(
            f"EvidenceIterator 初始化: domain={domain_id}, "
            f"total_chunks={len(chunks)}, relevant_chunks={len(self.chunks)}"
        )
    
    def __iter__(self) -> Iterator[SemanticChunk]:
        """返回迭代器自身"""
        return self
    
    def __next__(self) -> SemanticChunk:
        """
        返回下一个 chunk
        
        Returns:
            下一个语义块
            
        Raises:
            StopIteration: 遍历完成
        """
        if self.current_index >= len(self.chunks):
            raise StopIteration
        
        chunk = self.chunks[self.current_index]
        self.current_index += 1
        
        logger.debug(
            f"Iterator yielding chunk {self.current_index}/{len(self.chunks)}: "
            f"{chunk.chunk_id} ({chunk.label.value})"
        )
        
        return chunk
    
    def __len__(self) -> int:
        """返回 chunks 总数"""
        return len(self.chunks)
    
    def reset(self) -> None:
        """重置迭代器到初始位置"""
        self.current_index = 0
    
    @property
    def remaining(self) -> int:
        """返回剩余 chunks 数量"""
        return len(self.chunks) - self.current_index
    
    @property
    def progress(self) -> tuple:
        """返回进度 (current, total)"""
        return (self.current_index, len(self.chunks))


# ============================================================================
# Exports
# ============================================================================

__all__ = ["EvidenceIterator"]
