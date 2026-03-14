"""
附件下载器

使用分层策略下载 URL 内容：httpx -> Playwright
"""

import asyncio
import httpx
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from playwright.async_api import async_playwright, Browser, Page

from .cache import URLCacheManager

logger = logging.getLogger(__name__)


class DownloadResult:
    """下载结果"""
    def __init__(
        self,
        success: bool,
        content: Optional[bytes] = None,
        file_type: str = 'html',
        content_type: Optional[str] = None,
        error: Optional[str] = None,
        method: str = 'unknown'
    ):
        self.success = success
        self.content = content
        self.file_type = file_type
        self.content_type = content_type
        self.error = error
        self.method = method


class AttachmentDownloader:
    """
    附件下载管理器
    
    分层下载策略：
    1. httpx（轻量快速）
    2. Playwright（处理 JavaScript 渲染）
    """
    
    def __init__(
        self,
        cache_manager: URLCacheManager,
        httpx_timeout: int = 5,
        playwright_timeout: int = 20,
        retry_attempts: int = 2,
        retry_delay: int = 1,
        max_concurrency: int = 3
    ):
        """
        初始化下载管理器
        
        Args:
            cache_manager: 缓存管理器
            httpx_timeout: httpx 超时（秒）
            playwright_timeout: Playwright 超时（秒）
            retry_attempts: 重试次数
            retry_delay: 重试间隔（秒）
            max_concurrency: 最大并发数
        """
        self.cache_manager = cache_manager
        self.httpx_timeout = httpx_timeout
        self.playwright_timeout = playwright_timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.max_concurrency = max_concurrency
        
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self._browser: Optional[Browser] = None
        
        logger.info(
            f"AttachmentDownloader 初始化: "
            f"httpx_timeout={httpx_timeout}s, playwright_timeout={playwright_timeout}s, "
            f"max_concurrency={max_concurrency}"
        )
    
    def _get_file_type(self, url: str, content_type: Optional[str] = None) -> str:
        """
        根据 URL 或 Content-Type 猜测文件类型
        
        Args:
            url: URL 地址
            content_type: HTTP Content-Type
            
        Returns:
            文件类型（html, pdf, docx, xlsx, other）
        """
        url_lower = url.lower()
        
        if content_type:
            content_type_lower = content_type.lower()
            if 'pdf' in content_type_lower:
                return 'pdf'
            elif 'word' in content_type_lower or 'document' in content_type_lower:
                return 'docx'
            elif 'sheet' in content_type_lower or 'excel' in content_type_lower:
                return 'xlsx'
            elif 'html' in content_type_lower:
                return 'html'
        
        if '.pdf' in url_lower:
            return 'pdf'
        elif any(ext in url_lower for ext in ['.doc', '.docx']):
            return 'docx'
        elif any(ext in url_lower for ext in ['.xls', '.xlsx']):
            return 'xlsx'
        else:
            return 'html'
    
    async def _download_with_httpx(self, url: str) -> DownloadResult:
        """
        使用 httpx 下载
        
        Args:
            url: URL 地址
            
        Returns:
            DownloadResult
        """
        logger.debug(f"httpx 尝试下载: {url}")
        try:
            async with httpx.AsyncClient(timeout=self.httpx_timeout) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                
                content = response.content
                content_type = response.headers.get('content-type', '')
                file_type = self._get_file_type(url, content_type)
                
                logger.info(f"httpx 下载成功: {url} ({len(content)} bytes, type={content_type})")
                return DownloadResult(
                    success=True,
                    content=content,
                    file_type=file_type,
                    content_type=content_type,
                    method='httpx'
                )
        except Exception as e:
            logger.debug(f"httpx 下载失败: {url}, {e}")
            return DownloadResult(
                success=False,
                error=str(e),
                method='httpx'
            )
    
    async def _download_with_playwright(self, url: str) -> DownloadResult:
        """
        使用 Playwright 下载
        
        Args:
            url: URL 地址
            
        Returns:
            DownloadResult
        """
        logger.info(f"Playwright 尝试下载: {url}")
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                
                logger.debug(f"Playwright 导航到: {url}")
                await page.goto(
                    url,
                    wait_until='networkidle',
                    timeout=self.playwright_timeout * 1000
                )
                
                html = await page.content()
                await browser.close()
                
                content_type = 'text/html'
                file_type = 'html'
                
                logger.info(f"Playwright 下载成功: {url} ({len(html)} bytes)")
                return DownloadResult(
                    success=True,
                    content=html.encode('utf-8'),
                    file_type=file_type,
                    content_type=content_type,
                    method='playwright'
                )
        except Exception as e:
            logger.warning(f"Playwright 下载失败: {url}, {e}")
            return DownloadResult(
                success=False,
                error=str(e),
                method='playwright'
            )
    
    async def _download_with_retry(self, url: str) -> DownloadResult:
        """
        带重试的下载
        
        Args:
            url: URL 地址
            
        Returns:
            DownloadResult
        """
        async with self.semaphore:
            for attempt in range(self.retry_attempts + 1):
                if attempt > 0:
                    logger.info(f"httpx 重试下载 {url} (尝试 {attempt}/{self.retry_attempts})")
                    await asyncio.sleep(self.retry_delay)
                
                result = await self._download_with_httpx(url)
                if result.success:
                    logger.debug(f"httpx 下载成功，无需使用 Playwright")
                    return result
                
                if attempt < self.retry_attempts:
                    logger.debug(f"httpx 第 {attempt + 1} 次失败: {result.error}")
                    continue
            
            logger.info(f"httpx 全部失败，切换到 Playwright")
            result = await self._download_with_playwright(url)
            return result
    
    async def download(self, url: str, use_cache: bool = True) -> DownloadResult:
        """
        下载 URL（分层策略）
        
        Args:
            url: URL 地址
            use_cache: 是否使用缓存
            
        Returns:
            DownloadResult
        """
        file_type = self._get_file_type(url)
        
        if use_cache:
            cached = self.cache_manager.get_cached_content(url, file_type)
            if cached is not None:
                logger.info(f"命中缓存: {url}")
                return DownloadResult(
                    success=True,
                    content=cached,
                    file_type=file_type,
                    method='cache'
                )
        
        logger.info(f"开始下载: {url}")
        result = await self._download_with_retry(url)
        
        if result.success and result.content:
            try:
                self.cache_manager.save_cache(
                    url=url,
                    content=result.content,
                    file_type=result.file_type,
                    content_type=result.content_type
                )
            except Exception as e:
                logger.warning(f"保存缓存失败: {url}, {e}")
        else:
            logger.warning(f"下载失败: {url}, 错误: {result.error}")
        
        return result
    
    async def download_batch(
        self,
        urls: List[str],
        use_cache: bool = True,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, DownloadResult]:
        """
        批量下载
        
        Args:
            urls: URL 列表
            use_cache: 是否使用缓存
            progress_callback: 进度回调（completed, total, url, result）
            
        Returns:
            {url: DownloadResult} 字典
        """
        results = {}
        total = len(urls)
        completed = 0
        
        async def download_and_update(url: str):
            nonlocal completed
            result = await self.download(url, use_cache)
            results[url] = result
            completed += 1
            
            if progress_callback:
                progress_callback(completed, total, url, result)
        
        await asyncio.gather(*[download_and_update(url) for url in urls])
        
        success_count = sum(1 for r in results.values() if r.success)
        cache_count = sum(1 for r in results.values() if r.method == 'cache')
        httpx_count = sum(1 for r in results.values() if r.method == 'httpx')
        playwright_count = sum(1 for r in results.values() if r.method == 'playwright')
        
        logger.info(
            f"批量下载完成: 总计={total}, 成功={success_count}, "
            f"缓存={cache_count}, httpx={httpx_count}, Playwright={playwright_count}"
        )
        return results
