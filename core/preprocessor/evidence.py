"""
证据池管理器
"""
import json
import logging
from pathlib import Path
from typing import List, Dict

from .pojo import SemanticLabel, SemanticChunk

logger = logging.getLogger(__name__)

# ============================================================================
# 领域到语义标签的映射
# ============================================================================

# AGREE-II 六个领域到语义标签的映射关系
DOMAIN_LABEL_MAPPING: Dict[int, List[SemanticLabel]] = {
    1: [SemanticLabel.OVERVIEW, SemanticLabel.EXECUTIVE_SUMMARY, SemanticLabel.METHOD],      # Scope and Purpose
    2: [SemanticLabel.OVERVIEW, SemanticLabel.EXECUTIVE_SUMMARY, SemanticLabel.METHOD, SemanticLabel.APPENDIX, SemanticLabel.OTHERS],      # Stakeholder Involvement
    3: [SemanticLabel.METHOD, SemanticLabel.RECOMMENDATION, SemanticLabel.APPENDIX],      # Rigour of Development
    4: [SemanticLabel.EXECUTIVE_SUMMARY, SemanticLabel.RECOMMENDATION],                                  # Clarity of Presentation
    5: [SemanticLabel.RECOMMENDATION, SemanticLabel.APPENDIX],                                  # Applicability
    6: [SemanticLabel.OTHERS, SemanticLabel.OVERVIEW, SemanticLabel.APPENDIX, SemanticLabel.CONCLUSION],                              # Editorial Independence
}


# 仅对需要重排的领域定义显式扫描优先级。
# 未出现在该表中的领域，保持文档原始顺序。
DOMAIN_LABEL_PRIORITY_OVERRIDE: Dict[int, List[SemanticLabel]] = {
    6: [SemanticLabel.OTHERS, SemanticLabel.OVERVIEW, SemanticLabel.APPENDIX, SemanticLabel.CONCLUSION],
}


def order_chunks_for_domain(chunks: List[SemanticChunk], domain_id: int) -> List[SemanticChunk]:
    """
    按领域要求返回有序 chunks。

    默认保持原始文档顺序；若领域定义了显式优先级，则先按标签优先级排序，
    同一标签内仍保持原始顺序。
    """
    relevant_labels = DOMAIN_LABEL_MAPPING.get(domain_id, [])
    filtered_chunks = [
        chunk for chunk in chunks
        if chunk.label in relevant_labels and chunk.label != SemanticLabel.IRRELEVANT
    ]

    priority_labels = DOMAIN_LABEL_PRIORITY_OVERRIDE.get(domain_id)
    if not priority_labels:
        return filtered_chunks

    label_rank = {label: idx for idx, label in enumerate(priority_labels)}
    return sorted(
        filtered_chunks,
        key=lambda chunk: label_rank.get(chunk.label, len(priority_labels)),
    )


class EvidencePool:
    """
    证据池管理器
    
    根据 AGREE-II 领域提供相关的语义块作为上下文
    """
    
    def __init__(self, chunks: List[SemanticChunk]):
        """
        初始化证据池
        
        Args:
            chunks: 语义块列表
        """
        self.chunks = chunks
        self.domain_index = self._build_domain_index()
    
    def _build_domain_index(self) -> Dict[int, List[str]]:
        """
        构建领域到块 ID 的索引
        
        Returns:
            领域 -> 块 ID 列表的映射
        """
        index = {
            domain: [chunk.chunk_id for chunk in order_chunks_for_domain(self.chunks, domain)]
            for domain in range(1, 7)
        }
        
        logger.debug(f"Domain index built: {[(k, len(v)) for k, v in index.items()]}")
        return index
    
    def get_evidence_for_domain(self, domain_id: int) -> str:
        """
        获取指定领域的证据上下文
        
        Args:
            domain_id: 领域 ID (1-6)
            
        Returns:
            拼接后的证据文本
        """
        if domain_id not in self.domain_index:
            logger.warning(f"无效的领域 ID: {domain_id}")
            return ""
        
        chunk_ids = self.domain_index[domain_id]
        
        if not chunk_ids:
            logger.warning(f"领域 {domain_id} 没有相关证据")
            return ""
        
        # 获取对应的块并拼接
        evidence_texts = []
        for chunk_id in chunk_ids:
            chunk = next((c for c in self.chunks if c.chunk_id == chunk_id), None)
            if chunk:
                evidence_texts.append(f"## {chunk.title}\n\n{chunk.content}")
        
        return "\n\n---\n\n".join(evidence_texts)
    
    def save(self, output_path: str) -> None:
        """
        保存证据池到 JSON 文件
        
        Args:
            output_path: 输出文件路径
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        from datetime import datetime
        
        data = {
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "chunks": [
                {
                    "id": chunk.chunk_id,
                    "title": chunk.title,
                    "content": chunk.content,
                    "label": chunk.label.value,
                    "reasoning": chunk.reasoning,
                    "start_page": chunk.start_page,
                    "end_page": chunk.end_page,
                    "block_ids": chunk.block_ids or [],
                    "section": chunk.section,  # 保存 section
                    "format_hints": chunk.format_hints,  # 保存格式元数据
                }
                for chunk in self.chunks
            ],
            "domain_index": {
                str(domain): chunk_ids
                for domain, chunk_ids in self.domain_index.items()
            }
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"证据池已保存: {output_path}")
    
    @classmethod
    def load(cls, prep_dir: str) -> "EvidencePool":
        """
        从 prep 目录加载证据池
        
        Args:
            prep_dir: prep 目录路径
            
        Returns:
            EvidencePool 实例
        """
        prep_dir = Path(prep_dir)
        
        # 查找 evidence_pool.json 文件
        evidence_files = list(prep_dir.glob("*_evidence_pool.json"))
        
        if not evidence_files:
            raise FileNotFoundError(f"未找到证据池文件: {prep_dir}")
        
        evidence_path = evidence_files[0]
        
        with open(evidence_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 重构 SemanticChunk 对象
        chunks = [
            SemanticChunk(
                chunk_id=chunk_data["id"],
                title=chunk_data["title"],
                content=chunk_data["content"],
                label=SemanticLabel(chunk_data["label"]),
                start_page=chunk_data["start_page"],
                end_page=chunk_data["end_page"],
                reasoning=chunk_data.get("reasoning", ""),
                block_ids=chunk_data.get("block_ids", []),
                section=chunk_data.get("section", ""),  # 读取 section
                format_hints=chunk_data.get("format_hints"),  # 读取格式元数据
            )
            for chunk_data in data["chunks"]
        ]
        
        logger.info(f"证据池已加载: {evidence_path} ({len(chunks)} 个块)")
        return cls(chunks)
