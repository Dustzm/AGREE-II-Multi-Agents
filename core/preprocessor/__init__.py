"""
文档预处理模块

负责将 MinerU 解析的 content_list.json 转换为语义证据池
"""
import logging
from pathlib import Path
from typing import Dict, Any

from .pojo import ContentItem, TitleContentBlock, SemanticLabel, SemanticChunk
from .chunker import DocumentChunker
from .labeler import SemanticLabeler
from .evidence import EvidencePool, DOMAIN_LABEL_MAPPING
from .visual_evidence import generate_visual_evidence
from .iterator import EvidenceIterator

logger = logging.getLogger(__name__)

# ============================================================================
# 主流程函数
# ============================================================================

async def process_content_list(
    content_list_path: str,
    output_path: str,
    pdf_path: str = None,
    model=None,
    lang: str = "zh",
    include_attachments: bool = True
) -> Dict[str, Any]:
    """
    处理 content_list.json，生成语义证据池
    
    Args:
        content_list_path: content_list.json 文件路径
        output_path: 输出的 evidence_pool.json 文件路径
        pdf_path: 原始 PDF 路径（用于 docindex 字体分析和附件检测）
        model: LLM 模型实例（可选）
        lang: Prompt 语言（zh/en）
        include_attachments: 是否处理附件（默认 True）
        
    Returns:
        处理结果字典
    """
    try:
        prep_dir = Path(output_path).parent
        content_list_name = Path(content_list_path).stem  # xxx_content_list
        base_name = content_list_name.replace("_content_list", "")
        
        # Step 0: 构建 docindex（如果提供了 PDF 路径）
        docindex_path = None
        if pdf_path and Path(pdf_path).exists():
            try:
                from docindex.pipeline import build_and_save_docindex
                docindex_path = str(prep_dir / f"{base_name}_docindex.json")
                build_and_save_docindex(
                    json_path=content_list_path,
                    pdf_path=pdf_path,
                    output_path=docindex_path
                )
                logger.info(f"docindex 已生成: {docindex_path}")
            except Exception as de:
                logger.warning(f"docindex 生成失败（回退到 text_level）: {de}")
                docindex_path = None
        else:
            logger.info("未提供 PDF 路径，跳过 docindex 构建")
        
        # Step 1: 构造标题-内容块（包含附件处理）
        chunker = DocumentChunker()
        title_content_blocks = await chunker.parse(
            content_list_path, 
            docindex_path=docindex_path,
            source_pdf_path=pdf_path,
            include_attachments=include_attachments
        )
        
        # Step 2: LLM 语义标签分类
        labeler = SemanticLabeler(model=model, lang=lang)
        semantic_chunks = await labeler.label_chunks(title_content_blocks)
        
        # Step 3: 创建并保存证据池
        pool = EvidencePool(semantic_chunks)
        pool.save(output_path)
        
        # Step 4: 生成可视化证据（用于前端渲染）
        try:
            model_json_path = prep_dir / f"{base_name}_model.json"
            visual_evidence_path = prep_dir / f"{base_name}_visual_evidence.json"
            
            if model_json_path.exists():
                generate_visual_evidence(
                    model_json_path=str(model_json_path),
                    evidence_pool_path=output_path,
                    output_path=str(visual_evidence_path)
                )
                logger.info(f"可视化证据已生成: {visual_evidence_path}")
            else:
                logger.warning(f"model.json 不存在，跳过可视化证据生成: {model_json_path}")
        except Exception as ve:
            logger.warning(f"可视化证据生成失败（不影响主流程）: {ve}")
        
        return {
            "status": "success",
            "chunks_count": len(semantic_chunks),
            "output_path": str(output_path),
            "error": ""
        }
    
    except Exception as e:
        logger.error(f"文档预处理失败: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "chunks_count": 0,
            "output_path": "",
            "error": str(e)
        }


def load_evidence_for_skill(task_id: str, skill_id: str) -> Dict[str, Any]:
    """
    从证据池加载与条目相关的上下文
    
    Args:
        task_id: 任务 ID
        skill_id: 条目 ID（如 "item_1"）
        
    Returns:
        {"status": "success/error", "content": str, "char_count": int, "domain": int, "error": str}
    """
    try:
        from core.skills import load_skill_config
        from core.agents.tools.public_tools import get_task_paths
        
        # 获取条目所属领域
        config = load_skill_config(skill_id, check_enabled=False)
        domain = config.get("domain", 0)
        
        if domain == 0:
            return {
                "status": "error",
                "content": "",
                "char_count": 0,
                "domain": 0,
                "error": f"条目 {skill_id} 没有有效的领域信息"
            }
        
        # 获取任务路径
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "content": "",
                "char_count": 0,
                "domain": domain,
                "error": f"任务不存在: {task_id}"
            }
        
        # 加载证据池
        # EvidencePool.load 需要传入 prep_dir
        pool = EvidencePool.load(paths["prep_dir"])
        
        # 获取领域相关上下文
        content = pool.get_evidence_for_domain(domain)
        
        if not content:
            return {
                "status": "error",
                "content": "",
                "char_count": 0,
                "domain": domain,
                "error": f"领域 {domain} 没有可用的证据"
            }
        
        logger.info(f"已加载证据: skill_id={skill_id}, domain={domain}, 字符数={len(content)}")
        
        return {
            "status": "success",
            "content": content,
            "char_count": len(content),
            "domain": domain,
            "error": ""
        }
    
    except Exception as e:
        logger.error(f"加载证据失败: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "content": "",
            "char_count": 0,
            "domain": 0,
            "error": str(e)
        }
