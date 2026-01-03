# 核心解析器模块
import os
import json
import asyncio
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image
from loguru import logger

from .config import Config
from .schemas import Block, Page, Document, ParseResult
from .pdf_utils import pdf_to_images
from .vlm_client import VLMClient, VLMTimeoutError, VLMParseError
from .image_utils import process_images_in_blocks
from .markdown_writer import save_markdown
from .prompts.document import DOCUMENT_PARSE_PROMPT, SKIP_BLOCK_TYPES

try:
    from tqdm.asyncio import tqdm_asyncio
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


class DocVLMParser:
    """DocVLM PDF 解析器"""
    
    def __init__(self, config: Optional[Config] = None):
        """
        初始化解析器
        
        Args:
            config: 配置对象，如果为 None 则从环境变量加载
        """
        self.config = config or Config.from_env()
        self.config.validate()
        self.vlm_client = VLMClient(self.config)
        self.failed_pages: List[int] = []  # 记录解析失败的页面索引
        self.blank_pages: List[int] = []  # 记录检测到的空白页
        self.usage_stats: List[dict] = []  # 记录每页的 token 用量
    
    async def parse(self, pdf_path: str, output_dir: str) -> ParseResult:
        """
        解析 PDF 文件
        
        Args:
            pdf_path: PDF 文件路径
            output_dir: 输出目录
            
        Returns:
            ParseResult 包含输出文件路径
        """
        logger.info(f"开始解析: {pdf_path}")
        logger.info(f"输出目录: {output_dir}")
        
        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        images_dir = os.path.join(output_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        
        # Step 1: PDF 转图片
        logger.info("Step 1: PDF 转图片")
        images, page_sizes = pdf_to_images(pdf_path, dpi=self.config.dpi)
        
        # Step 2: VLM 识别每页
        logger.info(f"Step 2: VLM 识别 ({len(images)} 页)")
        pages = await self._parse_pages(images, page_sizes)
        
        # Step 3: 后处理（裁剪图片）
        logger.info("Step 3: 后处理")
        for page, image, page_size in zip(pages, images, page_sizes):
            process_images_in_blocks(image, page.blocks, page.page_idx, images_dir, page_size)
        
        # 构建文档对象
        document = Document(
            title=self._extract_title(pages),
            total_pages=len(pages),
            pages=pages
        )
        
        # Step 4: 输出生成
        logger.info("Step 4: 生成输出文件")
        
        # 保存 JSON
        json_path = os.path.join(output_dir, "docvlm_output.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(document.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"已保存: {json_path}")
        
        # 保存 Markdown
        md_path = os.path.join(output_dir, "output.md")
        save_markdown(document, md_path)
        logger.info(f"已保存: {md_path}")
        
        # 保存 usage 统计
        usage_path = os.path.join(output_dir, "usage.json")
        # 按页码排序
        self.usage_stats.sort(key=lambda x: x["page"])
        # 计算总计
        total_prompt_tokens = sum(u["usage"].get("prompt_tokens", 0) for u in self.usage_stats)
        total_completion_tokens = sum(u["usage"].get("completion_tokens", 0) for u in self.usage_stats)
        total_tokens = sum(u["usage"].get("total_tokens", 0) for u in self.usage_stats)
        
        usage_data = {
            "total": {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_tokens
            },
            "pages": self.usage_stats
        }
        with open(usage_path, "w", encoding="utf-8") as f:
            json.dump(usage_data, f, ensure_ascii=False, indent=2)
        logger.info(f"已保存: {usage_path}")
        
        result = ParseResult(
            json_path=json_path,
            md_path=md_path,
            images_dir=images_dir,
            document=document
        )
        
        logger.info(f"解析完成! 共 {len(pages)} 页, {sum(len(p.blocks) for p in pages)} 个块")
        
        # 输出空白页信息
        if self.blank_pages:
            self.blank_pages.sort()
            logger.info(f"检测到空白页 (共 {len(self.blank_pages)} 页): {self.blank_pages}")
        
        # 输出失败页面信息（排除空白页）
        if self.failed_pages:
            self.failed_pages.sort()
            logger.warning(f"以下页面解析失败或输出异常 (共 {len(self.failed_pages)} 页): {self.failed_pages}")
        
        return result
    
    async def _parse_pages(
        self, 
        images: List[Image.Image], 
        page_sizes: List[Tuple[int, int]]
    ) -> List[Page]:
        """解析所有页面（使用 asyncio.wait + FIRST_COMPLETED 模式，带超时重试）"""
        # 控制并发
        semaphore = asyncio.Semaphore(self.config.max_concurrency)
        timeout_pages: List[int] = []  # 记录超时的页面索引
        
        async def parse_page_with_semaphore(idx: int, image: Image.Image, page_size: Tuple[int, int]) -> Tuple[int, Page]:
            async with semaphore:
                page = await self._parse_single_page(idx, image, page_size)
                return (idx, page)
        
        # 创建所有任务
        pending: set = set()
        task_to_idx: dict = {}  # 任务到页面索引的映射
        for idx, (image, page_size) in enumerate(zip(images, page_sizes)):
            task = asyncio.create_task(parse_page_with_semaphore(idx, image, page_size))
            pending.add(task)
            task_to_idx[task] = idx
        
        # 收集结果
        results: List[Tuple[int, Page]] = []
        
        # 创建进度条
        if HAS_TQDM:
            from tqdm import tqdm
            pbar = tqdm(total=len(images), desc="VLM 解析", unit="页")
        else:
            pbar = None
        
        try:
            while pending:
                # 等待任意一个任务完成
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                
                for task in done:
                    idx = task_to_idx.get(task)
                    try:
                        result = task.result()
                        results.append(result)
                    except (VLMTimeoutError, VLMParseError) as e:
                        # VLM 超时或解析失败，记录到失败列表
                        if idx is not None:
                            timeout_pages.append(idx + 1)
                            logger.warning(f"第 {idx + 1} 页失败 ({type(e).__name__})，将在第二轮重试")
                    except Exception as e:
                        # 其他未知错误也记录
                        if idx is not None:
                            timeout_pages.append(idx + 1)
                        logger.warning(f"页面 {idx + 1 if idx else '?'} 解析异常: {e}，将在第二轮重试")
                    
                    if pbar:
                        pbar.update(1)
        finally:
            if pbar:
                pbar.close()
        
        # 第二轮：重试失败的页面
        if timeout_pages:
            logger.info(f"=== 开始第二轮重试 ({len(timeout_pages)} 页失败): {sorted(timeout_pages)} ===")
            
            retry_pending: set = set()
            retry_task_to_idx: dict = {}
            
            for page_num in timeout_pages:
                idx = page_num - 1
                task = asyncio.create_task(parse_page_with_semaphore(idx, images[idx], page_sizes[idx]))
                retry_pending.add(task)
                retry_task_to_idx[task] = idx
            
            if HAS_TQDM:
                from tqdm import tqdm
                retry_pbar = tqdm(total=len(timeout_pages), desc="重试超时页面", unit="页")
            else:
                retry_pbar = None
            
            try:
                while retry_pending:
                    done, retry_pending = await asyncio.wait(retry_pending, return_when=asyncio.FIRST_COMPLETED)
                    
                    for task in done:
                        idx = retry_task_to_idx.get(task)
                        try:
                            result = task.result()
                            results.append(result)
                            logger.info(f"第 {idx + 1} 页重试成功")
                        except Exception as e:
                            logger.error(f"第 {idx + 1} 页重试失败: {e}")
                            self.failed_pages.append(idx + 1)
                        
                        if retry_pbar:
                            retry_pbar.update(1)
            finally:
                if retry_pbar:
                    retry_pbar.close()
            
            logger.info(f"=== 第二轮重试完成 ===")
        
        # 按页码排序
        results.sort(key=lambda x: x[0])
        return [page for _, page in results]
    
    def _is_blank_image(self, image: Image.Image, threshold: float = 0.99) -> bool:
        """
        通过分析图像像素判断是否为空白页
        
        Args:
            image: PIL 图像对象
            threshold: 白色像素占比阈值，超过此值认为是空白页
            
        Returns:
            True 表示是空白页
        """
        # 转为灰度图
        gray = image.convert('L')
        arr = np.array(gray)
        
        # 计算接近白色的像素比例 (像素值 > 250)
        white_pixels = np.sum(arr > 250)
        total_pixels = arr.size
        white_ratio = white_pixels / total_pixels
        
        return white_ratio > threshold
    
    async def _parse_single_page(
        self, 
        page_idx: int, 
        image: Image.Image, 
        page_size: Tuple[int, int]
    ) -> Page:
        """解析单个页面"""
        # 先检测是否为空白页
        if self._is_blank_image(image):
            logger.debug(f"第 {page_idx + 1} 页检测为空白页，跳过 VLM 解析")
            self.blank_pages.append(page_idx + 1)
            return Page(
                page_idx=page_idx,
                page_size=list(page_size),
                blocks=[]
            )
        
        try:
            # 调用 VLM
            response = await self.vlm_client.analyze_page(image, DOCUMENT_PARSE_PROMPT)
            result = response.get("result", {})
            usage = response.get("usage", {})
            
            # 记录 usage
            self.usage_stats.append({
                "page": page_idx + 1,
                "usage": usage
            })
        except Exception as e:
            logger.error(f"第 {page_idx + 1} 页解析失败: {e}")
            raise
        
        # 解析 blocks
        blocks = []
        raw_blocks = result.get("blocks", [])
        
        # 检查是否为空结果（可能是 JSON 解析失败，非空白页但无内容）
        if not raw_blocks:
            self.failed_pages.append(page_idx + 1)  # 记录失败页面（从1开始）
        
        # 页面宽高，用于 bbox 归一化
        page_width, page_height = page_size
        
        block_idx = 0
        for raw_block in raw_blocks:
            # 跳过非字典类型的块
            if not isinstance(raw_block, dict):
                logger.warning(f"第 {page_idx + 1} 页包含非法块格式: {type(raw_block)}")
                if (page_idx + 1) not in self.failed_pages:
                    self.failed_pages.append(page_idx + 1)
                continue
                
            block_type = raw_block.get("type", "text")
            
            # 跳过页眉页脚等
            if block_type in SKIP_BLOCK_TYPES:
                continue
            
            # 处理 bbox 归一化
            raw_bbox = raw_block.get("bbox", [])
            if raw_bbox and len(raw_bbox) == 4:
                # 将像素坐标归一化到 0-1 范围
                normalized_bbox = [
                    round(raw_bbox[0] / page_width, 3),   # x1
                    round(raw_bbox[1] / page_height, 3),  # y1
                    round(raw_bbox[2] / page_width, 3),   # x2
                    round(raw_bbox[3] / page_height, 3)   # y2
                ]
            else:
                normalized_bbox = raw_bbox
            
            block = Block(
                block_id=f"p{page_idx}_b{block_idx}",
                type=block_type,
                content=raw_block.get("content", ""),
                bbox=normalized_bbox,
                level=raw_block.get("level") if block_type == "title" else None,
            )
            blocks.append(block)
            block_idx += 1
        
        return Page(
            page_idx=page_idx,
            page_size=list(page_size),
            blocks=blocks
        )
    
    def _extract_title(self, pages: List[Page]) -> str:
        """从第一页提取文档标题"""
        if not pages or not pages[0].blocks:
            return ""
        
        # 查找第一个 level=1 的 title
        for block in pages[0].blocks:
            if block.type == "title" and block.level == 1:
                return block.content
        
        # 如果没有 level=1，取第一个 title
        for block in pages[0].blocks:
            if block.type == "title":
                return block.content
        
        return ""
    
    def parse_sync(self, pdf_path: str, output_dir: str) -> ParseResult:
        """同步版本的解析方法"""
        return asyncio.run(self.parse(pdf_path, output_dir))
