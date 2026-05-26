"""
MinerU VLM 解析器

使用 MinerU vlm-http-client 模式通过远程 VLM 服务解析 PDF
"""
import json
import logging
import asyncio
from pathlib import Path
from typing import Optional

import httpx

from core.config import get_settings

# 导入 MinerU 相关模块
from mineru.cli.common import convert_pdf_bytes_to_bytes_by_pypdfium2, prepare_env, read_fn
from mineru.data.data_reader_writer import FileBasedDataWriter
from mineru.backend.vlm.vlm_analyze import aio_doc_analyze
from mineru.backend.vlm.vlm_middle_json_mkcontent import union_make as vlm_union_make
from mineru.utils.enum_class import MakeMode

logger = logging.getLogger(__name__)


class MinerUParser:
    """
    MinerU VLM PDF 解析器
    
    使用 vlm-http-client 模式通过远程 VLM 服务将 PDF 转换为 Markdown
    """
    
    def __init__(
        self,
        server_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        http_timeout: Optional[int] = None,
        max_concurrency: Optional[int] = None,
        max_retries: Optional[int] = None
    ):
        """
        初始化解析器
        
        Args:
            server_url: VLM 服务地址 (可选，默认从配置读取)
            api_key: API Key (可选，默认从配置读取)
            http_timeout: HTTP 超时时间 (可选，默认从配置读取)
            max_concurrency: 最大并发数 (可选，默认从配置读取)
            max_retries: 最大重试次数 (可选，默认10次)
        """
        settings = get_settings()
        self.server_url = server_url or settings.mineru.server_url
        self.api_key = api_key or settings.mineru.api_key
        self.model_name = model_name or settings.mineru.model_name
        self.http_timeout = http_timeout or settings.mineru.http_timeout
        self.max_concurrency = max_concurrency or settings.mineru.max_concurrency
        self.max_retries = max_retries or 10  # 默认10次重试
        
        # 构建 server_headers 用于 Authorization Bearer 认证
        self.server_headers = None
        if self.api_key:
            self.server_headers = {"Authorization": f"Bearer {self.api_key}"}
    
    async def parse_pdf(
        self,
        pdf_path: str,
        output_dir: Optional[str] = None,
        start_page_id: int = 0,
        end_page_id: Optional[int] = None
    ) -> dict:
        """
        解析 PDF 文件
        
        Args:
            pdf_path: PDF 文件路径
            output_dir: 输出目录 (MinerU 会在此生成多个文件)
            start_page_id: 起始页码 (从0开始)
            end_page_id: 结束页码 (None 表示到最后)
            
        Returns:
            dict: {
                "status": "success/error",
                "content": str,      # Markdown 内容
                "pages": int,        # 页数
                "output_path": str,  # 输出目录路径
                "error": str
            }
        """
        pdf_file = Path(pdf_path)
        
        if not pdf_file.exists():
            return {
                "status": "error",
                "content": "",
                "pages": 0,
                "output_path": "",
                "error": f"PDF 文件不存在: {pdf_path}"
            }
        
        try:
            # 读取 PDF 文件
            pdf_bytes = read_fn(str(pdf_file))
            pdf_name = pdf_file.stem
            
            # 创建输出目录
            if output_dir:
                output_path = Path(output_dir)
            else:
                output_path = pdf_file.parent / "output"
            
            output_path.mkdir(parents=True, exist_ok=True)
            
            # 处理页面范围
            if start_page_id > 0 or end_page_id is not None:
                pdf_bytes = convert_pdf_bytes_to_bytes_by_pypdfium2(
                    pdf_bytes, start_page_id, end_page_id
                )
            
            # 准备输出环境
            local_image_dir, local_md_dir = prepare_env(
                str(output_path), pdf_name, "vlm"
            )
            image_writer = FileBasedDataWriter(local_image_dir)
            md_writer = FileBasedDataWriter(local_md_dir)
            
            
            middle_json, infer_result = await aio_doc_analyze(
                pdf_bytes,
                image_writer=image_writer,
                backend="http-client",
                server_url=self.server_url,
                model_name=self.model_name or None,
                server_headers=self.server_headers,
                http_timeout=self.http_timeout,
                max_concurrency=self.max_concurrency,
                max_retries=self.max_retries
            )
            
            pdf_info = middle_json["pdf_info"]
            pages_count = len(pdf_info)
            
            # 生成 Markdown 内容
            image_dir_name = Path(local_image_dir).name
            md_content = vlm_union_make(pdf_info, MakeMode.MM_MD, image_dir_name)
            
            # 保存 Markdown 文件
            md_writer.write_string(f"{pdf_name}.md", md_content)
            
            # 保存 content_list JSON
            content_list = vlm_union_make(pdf_info, MakeMode.CONTENT_LIST, image_dir_name)
            md_writer.write_string(
                f"{pdf_name}_content_list.json",
                json.dumps(content_list, ensure_ascii=False, indent=4)
            )
            
            # 保存 middle_json
            md_writer.write_string(
                f"{pdf_name}_middle.json",
                json.dumps(middle_json, ensure_ascii=False, indent=4)
            )
            
            # 保存 model_output
            md_writer.write_string(
                f"{pdf_name}_model.json",
                json.dumps(infer_result, ensure_ascii=False, indent=4)
            )
            
            logger.info(f"PDF 解析完成: {pdf_path} -> {local_md_dir}")
            
            return {
                "status": "success",
                "content": md_content,
                "pages": pages_count,
                "output_path": local_md_dir,
                "error": ""
            }
            
        except Exception as e:
            error_msg = f"PDF 解析异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "status": "error",
                "content": "",
                "pages": 0,
                "output_path": "",
                "error": error_msg
            }
    
    async def parse_pdf_bytes(
        self,
        pdf_bytes: bytes,
        filename: str = "document.pdf",
        output_dir: Optional[str] = None
    ) -> dict:
        """
        解析 PDF 字节流
        
        Args:
            pdf_bytes: PDF 文件字节
            filename: 文件名
            output_dir: 输出目录
            
        Returns:
            解析结果
        """
        try:
            pdf_name = Path(filename).stem
            
            # 创建输出目录
            if output_dir:
                output_path = Path(output_dir)
            else:
                output_path = Path("./output")
            
            output_path.mkdir(parents=True, exist_ok=True)
            
            # 准备输出环境
            local_image_dir, local_md_dir = prepare_env(
                str(output_path), pdf_name, "vlm"
            )
            image_writer = FileBasedDataWriter(local_image_dir)
            md_writer = FileBasedDataWriter(local_md_dir)
            
            # 调用 VLM 解析
            middle_json, infer_result = await aio_doc_analyze(
                pdf_bytes,
                image_writer=image_writer,
                backend="http-client",
                server_url=self.server_url,
                model_name=self.model_name or None,
                server_headers=self.server_headers,
                http_timeout=self.http_timeout,
                max_concurrency=self.max_concurrency,
                max_retries=self.max_retries
            )
            
            pdf_info = middle_json["pdf_info"]
            pages_count = len(pdf_info)
            
            # 生成 Markdown 内容
            image_dir_name = Path(local_image_dir).name
            md_content = vlm_union_make(pdf_info, MakeMode.MM_MD, image_dir_name)
            
            # 保存文件
            md_writer.write_string(f"{pdf_name}.md", md_content)
            
            return {
                "status": "success",
                "content": md_content,
                "pages": pages_count,
                "output_path": local_md_dir,
                "error": ""
            }
            
        except Exception as e:
            return {
                "status": "error",
                "content": "",
                "pages": 0,
                "output_path": "",
                "error": str(e)
            }
    
    async def health_check(self) -> bool:
        """
        检查 VLM 服务是否可用
        
        Returns:
            bool: 服务是否可用
        """
        health_url = f"{self.server_url.rstrip('/')}/v1/models"
        max_attempts = 3
        retry_delay_seconds = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(
                        health_url,
                        headers=self.server_headers,
                    )
                    if response.status_code == 200:
                        return True
                    logger.error(
                        "MinerU 健康检查失败: url=%s attempt=%s/%s status=%s response=%s",
                        health_url,
                        attempt,
                        max_attempts,
                        response.status_code,
                        response.text[:1000],
                    )
            except Exception as e:
                logger.exception(
                    "MinerU 健康检查异常: url=%s attempt=%s/%s error=%s",
                    health_url,
                    attempt,
                    max_attempts,
                    e,
                )

            if attempt < max_attempts:
                await asyncio.sleep(retry_delay_seconds * attempt)

        logger.error("MinerU 健康检查最终失败: url=%s attempts=%s", health_url, max_attempts)
        return False
    
    async def parse_pdf_to_file(
        self,
        pdf_path: str,
        source_path: str,
        prep_dir: Optional[str] = None,
        start_page_id: int = 0,
        end_page_id: Optional[int] = None
    ) -> dict:
        """
        解析 PDF 并输出到指定路径
        
        与 PyPDFParser.parse_pdf_to_file 接口兼容，用于 CLI 集成。
        
        Args:
            pdf_path: PDF 文件路径
            source_path: 输出的 source.md 文件路径
            prep_dir: 可选，存放中间 JSON 文件的目录 (如 content_list.json, middle.json)
            start_page_id: 起始页码 (从0开始)
            end_page_id: 结束页码 (None 表示到最后)
            
        Returns:
            dict: {
                "status": "success/error",
                "pages": int,
                "output_path": str,  # source.md 路径
                "prep_path": str,    # prep 目录路径
                "error": str
            }
        """
        pdf_file = Path(pdf_path)
        source_file = Path(source_path)
        
        if not pdf_file.exists():
            return {
                "status": "error",
                "pages": 0,
                "output_path": "",
                "prep_path": "",
                "error": f"PDF 文件不存在: {pdf_path}"
            }
        
        try:
            # 读取 PDF 文件
            pdf_bytes = read_fn(str(pdf_file))
            pdf_name = pdf_file.stem
            
            # 确保输出目录存在
            source_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 确定 prep 目录
            if prep_dir:
                prep_path = Path(prep_dir)
            else:
                prep_path = source_file.parent / "prep"
            prep_path.mkdir(parents=True, exist_ok=True)
            
            # 创建图片目录 (在 prep 目录下)
            images_dir = prep_path / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            
            # 处理页面范围
            if start_page_id > 0 or end_page_id is not None:
                pdf_bytes = convert_pdf_bytes_to_bytes_by_pypdfium2(
                    pdf_bytes, start_page_id, end_page_id
                )
            
            # 创建数据写入器
            image_writer = FileBasedDataWriter(str(images_dir))
            
            # 调用 VLM 解析
            middle_json, infer_result = await aio_doc_analyze(
                pdf_bytes,
                image_writer=image_writer,
                backend="http-client",
                server_url=self.server_url,
                model_name=self.model_name or None,
                server_headers=self.server_headers,
                http_timeout=self.http_timeout,
                max_concurrency=self.max_concurrency,
                max_retries=self.max_retries
            )
            
            pdf_info = middle_json["pdf_info"]
            pages_count = len(pdf_info)
            
            # 生成 Markdown 内容
            image_dir_name = images_dir.name
            md_content = vlm_union_make(pdf_info, MakeMode.MM_MD, image_dir_name)
            
            # 保存 source.md 到指定路径
            source_file.write_text(md_content, encoding="utf-8")
            
            # 保存中间文件到 prep 目录
            # content_list JSON
            content_list = vlm_union_make(pdf_info, MakeMode.CONTENT_LIST, image_dir_name)
            content_list_path = prep_path / f"{pdf_name}_content_list.json"
            content_list_path.write_text(
                json.dumps(content_list, ensure_ascii=False, indent=4),
                encoding="utf-8"
            )
            
            # middle_json
            middle_json_path = prep_path / f"{pdf_name}_middle.json"
            middle_json_path.write_text(
                json.dumps(middle_json, ensure_ascii=False, indent=4),
                encoding="utf-8"
            )
            
            # model_output
            (prep_path / f"{pdf_name}_model.json").write_text(
                json.dumps(infer_result, ensure_ascii=False, indent=4),
                encoding="utf-8"
            )
            
            
            logger.info(f"PDF 解析完成: {pdf_path} -> {source_path}")
            
            return {
                "status": "success",
                "pages": pages_count,
                "output_path": str(source_file),
                "prep_path": str(prep_path),
                "middle_json": str(middle_json_path),
                "content_list": str(content_list_path),
                "output_dir": str(prep_path),
                "error": ""
            }

            
        except Exception as e:
            error_msg = f"PDF 解析异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "status": "error",
                "pages": 0,
                "output_path": "",
                "prep_path": "",
                "error": error_msg
            }
