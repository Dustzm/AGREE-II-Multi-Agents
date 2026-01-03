#!/usr/bin/env python
# DocVLM 命令行接口
"""
DocVLM - VLM 驱动的 PDF 解析工具

使用方法:
    python -m docvlm.cli <pdf_path> [output_dir]
    
示例:
    python -m docvlm.cli input/test.pdf output/test_result
"""
import sys
import argparse
from pathlib import Path

from loguru import logger

# 配置日志
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    level="INFO"
)


def main():
    parser = argparse.ArgumentParser(
        description="DocVLM - VLM 驱动的 PDF 解析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python -m docvlm.cli input/test.pdf
    python -m docvlm.cli input/test.pdf output/result
    python -m docvlm.cli input/test.pdf -d 300
        """
    )
    
    parser.add_argument(
        "pdf_path",
        type=str,
        help="PDF 文件路径"
    )
    
    parser.add_argument(
        "output_dir",
        type=str,
        nargs="?",
        default=None,
        help="输出目录（默认: 与 PDF 同目录下的 <pdf_name>_docvlm/）"
    )
    
    parser.add_argument(
        "-d", "--dpi",
        type=int,
        default=200,
        help="PDF 转图片的 DPI（默认: 200）"
    )
    
    parser.add_argument(
        "-c", "--concurrency",
        type=int,
        default=28,
        help="最大并发页数（默认: 28）"
    )
    
    parser.add_argument(
        "-p", "--page",
        type=int,
        default=None,
        help="只解析指定页（1-indexed，用于调试）"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细日志"
    )
    
    parser.add_argument(
        "-s", "--stream",
        action="store_true",
        help="单页模式下使用流式输出"
    )
    
    args = parser.parse_args()
    
    # 调整日志级别
    if args.verbose:
        logger.remove()
        logger.add(
            sys.stderr,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level="DEBUG"
        )
    
    # 验证 PDF 文件
    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        logger.error(f"PDF 文件不存在: {pdf_path}")
        sys.exit(1)
    
    if not pdf_path.suffix.lower() == ".pdf":
        logger.warning(f"文件可能不是 PDF: {pdf_path}")
    
    # 确定输出目录
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = pdf_path.parent / f"{pdf_path.stem}_docvlm"
    
    logger.info(f"=" * 50)
    logger.info(f"DocVLM PDF 解析")
    logger.info(f"=" * 50)
    logger.info(f"输入: {pdf_path}")
    logger.info(f"输出: {output_dir}")
    logger.info(f"DPI: {args.dpi}")
    logger.info(f"并发: {args.concurrency}")
    logger.info(f"=" * 50)
    
    # 加载环境变量
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    
    # 设置配置
    import os
    os.environ.setdefault("DOCVLM_DPI", str(args.dpi))
    os.environ.setdefault("DOCVLM_MAX_CONCURRENCY", str(args.concurrency))
    
    # 执行解析
    try:
        from .config import Config
        from .parser import DocVLMParser
        from .pdf_utils import pdf_to_images
        
        config = Config.from_env()
        parser = DocVLMParser(config)
        
        # 如果指定了单页模式
        if args.page:
            page_num = args.page
            logger.info(f"单页模式: 只解析第 {page_num} 页")
            
            images, page_sizes = pdf_to_images(str(pdf_path), dpi=args.dpi)
            if page_num < 1 or page_num > len(images):
                logger.error(f"页码超出范围: 1-{len(images)}")
                sys.exit(1)
            
            # 单独解析这一页
            import asyncio
            from .prompts.document import DOCUMENT_PARSE_PROMPT
            page_idx = page_num - 1
            
            if args.stream:
                # 流式输出模式
                result = asyncio.run(parser.vlm_client.analyze_page_stream(images[page_idx], DOCUMENT_PARSE_PROMPT))
                from .schemas import Page, Block
                blocks = []
                for i, raw_block in enumerate(result.get("blocks", [])):
                    if isinstance(raw_block, dict):
                        blocks.append(Block(
                            block_id=f"p{page_idx}_b{i}",
                            type=raw_block.get("type", "text"),
                            content=raw_block.get("content", ""),
                            bbox=raw_block.get("bbox", []),
                            level=raw_block.get("level")
                        ))
                page = Page(page_idx=page_idx, page_size=list(page_sizes[page_idx]), blocks=blocks)
            else:
                # 普通模式
                page = asyncio.run(parser._parse_single_page(page_idx, images[page_idx], page_sizes[page_idx]))
            
            logger.info(f"=\" * 50")
            logger.info(f"第 {page_num} 页解析完成!")
            logger.info(f"识别到 {len(page.blocks)} 个块")
            for block in page.blocks:
                logger.info(f"  [{block.type}] {block.content[:50]}..." if len(block.content) > 50 else f"  [{block.type}] {block.content}")
        else:
            # 正常全文解析
            result = parser.parse_sync(str(pdf_path), str(output_dir))
            
            logger.info(f"=\" * 50")
            logger.info(f"解析完成!")
            logger.info(f"=\" * 50")
            logger.info(f"JSON: {result.json_path}")
            logger.info(f"MD:   {result.md_path}")
            logger.info(f"图片: {result.images_dir}")
            logger.info(f"共 {result.document.total_pages} 页, {sum(len(p.blocks) for p in result.document.pages)} 个块")
        
    except Exception as e:
        logger.error(f"解析失败: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
