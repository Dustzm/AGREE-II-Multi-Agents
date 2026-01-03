# VLM 客户端模块 (httpx 版本 - 每次请求创建新连接)
import io
import json
import base64
import asyncio
from typing import Dict, Any

import httpx
from PIL import Image
from loguru import logger

from .config import Config
from .pdf_utils import preprocess_image_for_vlm



class VLMTimeoutError(Exception):
    """自定义超时异常，用于让 parser 识别并重试"""
    pass


class VLMParseError(Exception):
    """VLM 返回内容解析失败异常"""
    pass


class VLMClient:
    """VLM API 客户端（httpx 直接调用，带重试机制）"""
    
    def __init__(self, config: Config):
        self.config = config
        self.model = config.vlm_model_name
        # 直接拼接 /chat/completions，假设 base_url 已包含版本号
        base_url = config.vlm_api_base.rstrip('/')
        self.api_url = f"{base_url}/chat/completions"
        self.api_key = config.vlm_api_key or "dummy"
        
        # HTTP 配置
        self.max_retries = 3
        self.retry_backoff = 1.0
        self.timeout = httpx.Timeout(
            connect=10.0,
            read=config.vlm_timeout,
            write=60.0,
            pool=None
        )
    
    def _image_to_base64(self, image: Image.Image) -> str:
        """将 PIL Image 转换为 base64 字符串（带预处理和 JPEG 压缩）"""
        # 预处理：缩放到 max_edge
        image = preprocess_image_for_vlm(
            image, 
            max_edge=self.config.max_image_edge,
            quality=self.config.image_quality
        )
        
        # JPEG 压缩
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG", quality=self.config.image_quality)
        
        size_kb = len(buffered.getvalue()) / 1024
        logger.debug(f"图片编码: {image.size}, JPEG quality={self.config.image_quality}, 大小={size_kb:.1f}KB")
        
        return base64.b64encode(buffered.getvalue()).decode("utf-8")
    
    def _build_request_body(self, prompt: str, img_base64: str) -> Dict[str, Any]:
        """构建请求体"""
        return {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_base64}"
                        }
                    }
                ]
            }],
            "response_format": {"type": "json_object"},
            "temperature": self.config.vlm_temperature,
            "top_p": self.config.vlm_top_p,
            "max_tokens": self.config.vlm_max_tokens,
            "frequency_penalty": self.config.vlm_frequency_penalty,  # 惩罚重复 token
            "skip_special_tokens": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "thinking": {"type": "disabled"}
        }
    
    async def analyze_page(self, image: Image.Image, prompt: str) -> Dict[str, Any]:
        """
        使用 VLM 分析页面图片（带重试机制，每次请求独立连接）
        
        Args:
            image: 页面图片
            prompt: 分析提示词
            
        Returns:
            VLM 返回的 JSON 结果
        """
        img_base64 = self._image_to_base64(image)
        request_body = self._build_request_body(prompt, img_base64)
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # 单次请求，不内部重试（由 Parser 层统一处理重试）
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    self.api_url,
                    json=request_body,
                    headers=headers
                )
                
                if response.status_code != 200:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}: {response.text[:200]}",
                        request=response.request,
                        response=response
                    )
                
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                usage = data.get("usage", {})  # 提取 usage 信息
                
                if not content:
                    raise ValueError("响应内容为空")
                
                # 返回解析后的 JSON 和 usage 信息
                parsed_content = json.loads(content)
                return {
                    "result": parsed_content,
                    "usage": usage
                }
                
            except json.JSONDecodeError as e:
                logger.error(f"VLM 返回非法 JSON: {e}")
                logger.error(f"原始内容前500字符: {content[:500] if content else 'None'}")
                # 将完整错误内容写入 error.log
                import datetime
                with open("error.log", "a", encoding="utf-8") as f:
                    f.write(f"\n{'='*80}\n")
                    f.write(f"时间: {datetime.datetime.now().isoformat()}\n")
                    f.write(f"错误: {e}\n")
                    f.write(f"完整内容:\n{content}\n")
                logger.info("完整错误内容已写入 error.log")
                # 抛出自定义异常，让 Parser 层捕获
                raise VLMParseError(f"JSON 解析失败: {e}") from e
                
            except (httpx.ReadTimeout, httpx.TimeoutException) as e:
                # 超时直接抛出
                raise VLMTimeoutError(f"请求超时 ({self.config.vlm_timeout}s)") from e
    
    async def analyze_page_stream(self, image: Image.Image, prompt: str) -> Dict[str, Any]:
        """
        使用 VLM 分析页面图片（流式输出，用于单页调试）
        
        Args:
            image: 页面图片
            prompt: 分析提示词
            
        Returns:
            VLM 返回的 JSON 结果
        """
        import sys
        
        img_base64 = self._image_to_base64(image)
        request_body = self._build_request_body(prompt, img_base64)
        request_body["stream"] = True  # 启用流式输出
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        full_content = ""
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    self.api_url,
                    json=request_body,
                    headers=headers
                ) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        raise httpx.HTTPStatusError(
                            f"HTTP {response.status_code}: {error_text[:200]}",
                            request=response.request,
                            response=response
                        )
                    
                    print("\n--- VLM 流式输出 ---\n", flush=True)
                    
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                print(content, end="", flush=True)
                                full_content += content
                        except json.JSONDecodeError:
                            pass
                    
                    print("\n\n--- 流式输出结束 ---\n", flush=True)
                
                # 解析完整内容
                return json.loads(full_content)
                
            except json.JSONDecodeError as e:
                logger.error(f"VLM 返回非法 JSON: {e}")
                logger.error(f"原始内容前500字符: {full_content[:500] if full_content else 'None'}")
                return {"blocks": []}
                
            except Exception as e:
                logger.error(f"流式请求失败: {e}")
                raise

