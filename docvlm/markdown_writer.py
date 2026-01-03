# Markdown 生成模块
import os
from typing import List

from .schemas import Document, Block, Page


def generate_markdown(document: Document, images_dir: str = "images") -> str:
    """
    将文档转换为 Markdown 格式
    
    Args:
        document: 文档对象
        images_dir: 图片目录名（相对路径）
        
    Returns:
        Markdown 字符串
    """
    lines = []
    
    for page in document.pages:
        for block in page.blocks:
            md_content = block_to_markdown(block, images_dir)
            if md_content:
                lines.append(md_content)
                lines.append("")  # 空行分隔
    
    return "\n".join(lines)


def block_to_markdown(block: Block, images_dir: str) -> str:
    """
    将单个块转换为 Markdown
    
    Args:
        block: 内容块
        images_dir: 图片目录名
        
    Returns:
        Markdown 字符串
    """
    if block.type == "title":
        level = block.level or 1
        level = int(level) if isinstance(level, (int, str)) and str(level).isdigit() else 1
        level = min(max(level, 1), 6)  # 限制在 1-6
        prefix = "#" * level
        return f"{prefix} {block.content}"
    
    elif block.type == "text":
        return block.content
    
    elif block.type == "list":
        return block.content  # 假设已经是 Markdown 列表格式
    
    elif block.type == "table":
        return block.content  # 假设已经是 Markdown 表格格式
    
    elif block.type == "image":
        if block.image_path:
            return f"![{block.block_id}]({block.image_path})"
        return ""
    
    elif block.type == "equation":
        # LaTeX 公式
        return f"$$\n{block.content}\n$$"
    
    else:
        # 忽略 header, footer, page_number 等
        return ""


def save_markdown(document: Document, output_path: str) -> str:
    """
    保存文档为 Markdown 文件
    
    Args:
        document: 文档对象
        output_path: 输出文件路径
        
    Returns:
        保存的文件路径
    """
    md_content = generate_markdown(document)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    
    return output_path
