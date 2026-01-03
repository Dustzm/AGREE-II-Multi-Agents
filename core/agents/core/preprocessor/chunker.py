"""
文档分块器
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from .pojo import TitleContentBlock

logger = logging.getLogger(__name__)

class DocumentChunker:
    """
    文档分块器
    
    根据 content_list.json 中的 text_level 字段识别标题，
    将后续内容归属到对应标题下，构造标题-内容块列表。
    """
    
    def __init__(self):
        self.ignored_types = {"header", "footer", "page_number"}
    
    def parse(self, content_list_path: str, docindex_path: str = None) -> List[TitleContentBlock]:
        """
        解析 content_list.json，构造标题-内容块列表
        
        Args:
            content_list_path: content_list.json 文件路径
            docindex_path: docindex.json 文件路径（可选，提供更精确的 cluster_level）
            
        Returns:
            标题-内容块列表
        """
        content_list_path = Path(content_list_path)
        
        if not content_list_path.exists():
            raise FileNotFoundError(f"文件不存在: {content_list_path}")
        
        with open(content_list_path, "r", encoding="utf-8") as f:
            items = json.load(f)
        
        # 加载 docindex 层级映射（title -> cluster_level）
        docindex_levels = self._load_docindex_levels(docindex_path)
        
        # 第一遍：收集所有标题及其层级
        title_infos = []
        for idx, item in enumerate(items):
            if item.get("type") in self.ignored_types:
                continue
            if "text_level" in item:
                title_text = item.get("text", "")
                # 优先使用 docindex 的 cluster_level，否则回退到 text_level
                level = docindex_levels.get(title_text, item.get("text_level", 1))
                title_infos.append({
                    "idx": idx,
                    "title": title_text,
                    "level": level,
                    "page_idx": item.get("page_idx", 0),
                    "block_id": item.get("block_id"),
                })
        
        # 第二遍：计算每个标题的 section 路径
        sections = self._build_section_paths(title_infos)
        
        # 第三遍：构建 TitleContentBlock
        chunks = []
        current_title = None
        current_title_page = 0
        current_contents = []
        current_block_ids = []
        current_section = ""
        title_idx = 0
        
        for item in items:
            # 过滤页眉、页脚、页码
            if item.get("type") in self.ignored_types:
                continue
            
            # 检测标题（有 text_level 字段）
            if "text_level" in item:
                # 保存之前的内容块
                if current_title is not None:
                    raw_text = self._extract_raw_text(current_contents)
                    chunks.append(TitleContentBlock(
                        title=current_title,
                        title_page_idx=current_title_page,
                        contents=current_contents,
                        raw_text=raw_text,
                        block_ids=current_block_ids,
                        section=current_section
                    ))
                
                # 开始新的内容块
                current_title = item.get("text", "")
                current_title_page = item.get("page_idx", 0)
                current_contents = []
                # 新块的 block_ids 从标题的 block_id 开始
                title_block_id = item.get("block_id")
                current_block_ids = [title_block_id] if title_block_id else []
                # 获取当前标题的 section
                current_section = sections[title_idx] if title_idx < len(sections) else ""
                title_idx += 1
            else:
                # 归属到当前标题
                if current_title is not None:
                    current_contents.append(item)
                    # 收集 block_id
                    item_block_id = item.get("block_id")
                    if item_block_id:
                        current_block_ids.append(item_block_id)
                    # 如果是 list 类型，还要收集内部子 blocks 的 block_ids
                    list_block_ids = item.get("list_block_ids", [])
                    if list_block_ids:
                        current_block_ids.extend(list_block_ids)
        
        # 保存最后一个内容块
        if current_title is not None:
            raw_text = self._extract_raw_text(current_contents)
            chunks.append(TitleContentBlock(
                title=current_title,
                title_page_idx=current_title_page,
                contents=current_contents,
                raw_text=raw_text,
                block_ids=current_block_ids,
                section=current_section
            ))
        
        logger.info(f"解析完成：{len(chunks)} 个标题-内容块")
        return chunks
    
    def _build_section_paths(self, title_infos: List[Dict[str, Any]]) -> List[str]:
        """
        计算每个标题的祖先路径
        
        算法逻辑：
        对于位置 i、层级 L 的标题：
        1. 向前遍历 (i-1, i-2, ..., 0)
        2. 找到第一个 level < L 的标题 → 直接父节点
        3. 继续向前找 level < 父节点.level → 祖父节点
        4. 重复直到无更高层级 → 到达根
        5. 拼接路径
        
        Args:
            title_infos: 标题信息列表
            
        Returns:
            section 路径列表，与 title_infos 一一对应
        """
        sections = []
        
        for i, info in enumerate(title_infos):
            parent_path = []
            current_level = info["level"]
            
            # 向前遍历找祖先
            for j in range(i - 1, -1, -1):
                prev_info = title_infos[j]
                
                # 直接跳过无效标题（cluster_level == -1）
                if prev_info["level"] == -1:
                    continue

                if prev_info["level"] < current_level:
                    # 过滤掉文档标题（cluster_level == 1）
                    if prev_info["level"] != 1:
                        parent_path.insert(0, prev_info["title"])
                    current_level = prev_info["level"]
            
            # 构建 section 字符串
            section = " > ".join(parent_path) if parent_path else ""
            sections.append(section)
        
        return sections
    
    def _load_docindex_levels(self, docindex_path: str) -> Dict[str, int]:
        """
        加载 docindex.json 中的 cluster_level 映射
        
        Args:
            docindex_path: docindex.json 文件路径
            
        Returns:
            标题 -> cluster_level 的映射字典
        """
        if docindex_path is None:
            return {}
        
        docindex_path = Path(docindex_path)
        if not docindex_path.exists():
            logger.warning(f"docindex 文件不存在: {docindex_path}")
            return {}
        
        try:
            with open(docindex_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            levels = {}
            for item in data.get("title_list", []):
                title = item.get("title", "")
                level = item.get("cluster_level")
                if title and level is not None:
                    levels[title] = level
            
            logger.info(f"从 docindex 加载了 {len(levels)} 个标题层级")
            return levels
            
        except Exception as e:
            logger.warning(f"加载 docindex 失败: {e}")
            return {}
    
    def _extract_raw_text(self, contents: List[Dict[str, Any]]) -> str:
        """
        从内容项列表中提取纯文本
        
        Args:
            contents: 内容项列表
            
        Returns:
            拼接后的纯文本
        """
        texts = []
        
        for item in contents:
            item_type = item.get("type", "")
            
            if item_type == "text":
                texts.append(item.get("text", ""))
            elif item_type == "list":
                # 列表项
                list_items = item.get("list_items", [])
                for list_item in list_items:
                    if isinstance(list_item, str):
                        texts.append(f"- {list_item}")
                    elif isinstance(list_item, dict):
                        texts.append(f"- {list_item.get('text', '')}")
            elif item_type == "table":
                # 表格暂时忽略或简化处理
                texts.append("[TABLE]")
            elif item_type == "image":
                # 图片添加占位符
                caption = item.get("image_caption", [])
                if caption:
                    texts.append(f"[IMAGE: {' '.join(caption)}]")
                else:
                    texts.append("[IMAGE]")
        
        return "\n".join(texts)
