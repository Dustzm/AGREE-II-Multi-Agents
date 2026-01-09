"""
可视化证据生成器

将 model.json 中的内容块与 evidence_pool.json 中的语义块关联，
生成用于前端渲染的 visual_evidence.json。
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# 语义标签颜色配置（与 SemanticLabel 枚举和前端 EvidenceViewer 保持一致）
LABEL_COLORS = {
    "Overview": "#3B82F6",           # 蓝色
    "Executive Summary": "#6366F1",  # 靛蓝色
    "Method": "#8B5CF6",             # 紫色
    "Recommendation": "#10B981",     # 绿色
    "Conclusion": "#14B8A6",         # 青色
    "Others": "#F59E0B",             # 橙色
    "Appendix": "#78716C",           # 灰棕色
    "Irrelevant": "#9CA3AF"          # 灰色
}

# 需要跳过的内容类型
# - header/footer/page_number: 页眉页脚等
# - list: 列表容器，内部的 text item 已单独标记，避免重叠
SKIP_TYPES = {"header", "footer", "page_number", "list"}
def find_matching_chunk_by_id(block_id: str, chunks: List[Dict]) -> Optional[Dict]:
    """
    通过 block_id 精确匹配 evidence_pool chunk
    
    Args:
        block_id: block 的唯一 ID（格式：p{页码}_b{块序号}）
        chunks: evidence_pool 中的语义块列表（含 block_ids）
        
    Returns:
        匹配到的 chunk，或 None
    """
    if not block_id:
        return None
    
    for chunk in chunks:
        block_ids = chunk.get("block_ids", [])
        if block_id in block_ids:
            return chunk
    
    return None


# 表格相关类型（这些类型在 model.json 中有独立的 block_id，
# 但其 block_id 不在 evidence_pool 的 block_ids 中）
TABLE_TYPES = {"table", "table_caption", "table_footnote"}


def find_matching_chunk_by_content(
    block_content: str,
    block_type: str,
    chunks: List[Dict]
) -> Optional[Dict]:
    """
    通过内容匹配查找对应的 chunk（用于表格类型的备用匹配）
    
    表格在 content_list.json 中作为整体存在（没有 block_id），
    但在 model.json 中被拆分为 table_caption, table, table_footnote 等子块。
    此函数通过检查 block 内容是否包含在 chunk 的 content 中来匹配。
    
    Args:
        block_content: block 的内容文本
        block_type: block 的类型（table, table_caption, table_footnote）
        chunks: evidence_pool 中的语义块列表
        
    Returns:
        匹配到的 chunk，或 None
    """
    if not block_content or block_type not in TABLE_TYPES:
        return None
    
    # 对于表格相关内容，截取前100字符进行匹配（避免过长内容的性能问题）
    search_text = block_content[:100].strip()
    if not search_text:
        return None
    
    for chunk in chunks:
        chunk_content = chunk.get("content", "")
        # 检查 block 内容是否出现在 chunk 内容中
        if search_text in chunk_content:
            return chunk
    
    return None


def generate_visual_evidence(
    model_json_path: str,
    evidence_pool_path: str,
    output_path: str
) -> Dict[str, Any]:
    """
    生成可视化证据数据
    
    Args:
        model_json_path: model.json 文件路径
        evidence_pool_path: evidence_pool.json 文件路径
        output_path: 输出文件路径
        
    Returns:
        生成的可视化证据数据
    """
    logger.info(f"开始生成可视化证据: {output_path}")
    
    # 1. 加载数据
    with open(model_json_path, 'r', encoding='utf-8') as f:
        model_data = json.load(f)
    
    with open(evidence_pool_path, 'r', encoding='utf-8') as f:
        evidence_pool = json.load(f)
    
    chunks = evidence_pool.get("chunks", [])
    logger.info(f"加载了 {len(model_data)} 页数据和 {len(chunks)} 个语义块")
    
    # 2. 遍历每页生成标注区域
    pages = []
    stats = {label: 0 for label in LABEL_COLORS.keys()}
    
    for page_idx, page_blocks in enumerate(model_data):
        regions = []
        region_id = 0
        
        for block in page_blocks:
            block_type = block.get("type", "")
            
            # 跳过页眉页脚
            if block_type in SKIP_TYPES:
                continue
            
            block_content = block.get("content", "")
            block_bbox = block.get("bbox", [0, 0, 0, 0])
            block_id = block.get("block_id")  # 获取 block_id
            
            # 通过 block_id 精确匹配 chunk
            chunk = find_matching_chunk_by_id(block_id, chunks)
            
            # 如果 block_id 匹配失败，且是表格相关类型，尝试内容匹配
            if chunk is None and block_type in TABLE_TYPES:
                chunk = find_matching_chunk_by_content(block_content, block_type, chunks)
            
            if chunk:
                label = chunk.get("label", "Irrelevant")
                chunk_id = chunk.get("id")
                title = chunk.get("title", "")
                reasoning = chunk.get("reasoning", "")
            else:
                label = "Irrelevant"
                chunk_id = None
                title = ""
                reasoning = ""
            
            # 记录统计
            stats[label] = stats.get(label, 0) + 1
            
            regions.append({
                "id": f"r_{page_idx}_{region_id}",
                "chunk_id": chunk_id,
                "label": label,
                "bbox": block_bbox,
                "type": block_type,
                "title": title,
                "reasoning": reasoning,
                "text_preview": block_content[:100] if block_content else ""
            })
            region_id += 1
        
        pages.append({
            "page_idx": page_idx,
            "regions": regions
        })
    
    # 3. 构建结果
    result = {
        "version": "1.0",
        "created_at": datetime.now().isoformat(),
        "page_count": len(model_data),
        "label_colors": LABEL_COLORS,
        "stats": stats,
        "pages": pages
    }
    
    # 4. 保存结果
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    logger.info(f"可视化证据已保存: {output_path}")
    logger.info(f"统计: {stats}")
    
    return result
