"""
URL 缓存管理器

提供 URL 下载内容的本地缓存功能
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class URLCacheManager:
    """
    URL 缓存管理器
    
    基于 URL hash 存储下载内容，避免重复下载
    """
    
    def __init__(self, cache_dir: Path, cache_days: int = 7):
        """
        初始化缓存管理器
        
        Args:
            cache_dir: 缓存目录路径
            cache_days: 缓存保留天数
        """
        self.cache_dir = cache_dir
        self.cache_days = cache_days
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"URL 缓存目录: {self.cache_dir}, 保留天数: {cache_days}")
    
    def _get_url_hash(self, url: str) -> str:
        """
        计算 URL 的 hash 值
        
        Args:
            url: URL 地址
            
        Returns:
            URL 的 SHA256 hash（前 16 位）
        """
        return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
    
    def get_cache_path(self, url: str, file_type: str = 'html') -> Path:
        """
        获取缓存文件路径
        
        Args:
            url: URL 地址
            file_type: 文件类型（html, pdf, docx 等）
            
        Returns:
            缓存文件路径
        """
        url_hash = self._get_url_hash(url)
        return self.cache_dir / f"{url_hash}.{file_type}"
    
    def get_metadata_path(self, url: str) -> Path:
        """
        获取元数据文件路径
        
        Args:
            url: URL 地址
            
        Returns:
            元数据文件路径
        """
        url_hash = self._get_url_hash(url)
        return self.cache_dir / f"{url_hash}_metadata.json"
    
    def get_cached_content(self, url: str, file_type: str = 'html') -> Optional[bytes]:
        """
        获取缓存内容
        
        Args:
            url: URL 地址
            file_type: 文件类型
            
        Returns:
            缓存内容（bytes），如果不存在或已过期返回 None
        """
        cache_path = self.get_cache_path(url, file_type)
        metadata_path = self.get_metadata_path(url)
        
        if not cache_path.exists() or not metadata_path.exists():
            logger.debug(f"缓存不存在: {url} -> {cache_path.name}")
            return None
        
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            download_time = datetime.fromisoformat(metadata['download_time'])
            expired = datetime.now() - download_time > timedelta(days=self.cache_days)
            
            if expired:
                logger.debug(f"缓存已过期: {url} (下载于 {download_time})")
                return None
            
            logger.debug(f"命中缓存: {url} -> {cache_path} ({cache_path.stat().st_size} bytes)")
            return cache_path.read_bytes()
        except Exception as e:
            logger.warning(f"读取缓存失败: {url}, {e}")
            return None
    
    def save_cache(
        self,
        url: str,
        content: bytes,
        file_type: str = 'html',
        content_type: Optional[str] = None
    ) -> Path:
        """
        保存内容到缓存
        
        Args:
            url: URL 地址
            content: 内容（bytes）
            file_type: 文件类型
            content_type: HTTP Content-Type（可选）
            
        Returns:
            缓存文件路径
        """
        cache_path = self.get_cache_path(url, file_type)
        metadata_path = self.get_metadata_path(url)
        
        try:
            cache_path.write_bytes(content)
            
            metadata = {
                'url': url,
                'file_type': file_type,
                'content_type': content_type,
                'download_time': datetime.now().isoformat(),
                'size': len(content),
            }
            
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
            
            logger.info(f"保存缓存: {url} -> {cache_path} ({len(content)} bytes)")
            return cache_path
        except Exception as e:
            logger.error(f"保存缓存失败: {url}, {e}")
            raise
    
    def is_cached(self, url: str, file_type: str = 'html') -> bool:
        """
        检查 URL 是否已缓存且未过期
        
        Args:
            url: URL 地址
            file_type: 文件类型
            
        Returns:
            是否已缓存
        """
        return self.get_cached_content(url, file_type) is not None
    
    def clear_cache(self, older_than_days: Optional[int] = None):
        """
        清理缓存
        
        Args:
            older_than_days: 清理超过指定天数的缓存（None 则全部清理）
        """
        logger.info(f"开始清理缓存: {self.cache_dir}")
        
        cleared_count = 0
        cutoff_time = datetime.now() - timedelta(days=older_than_days) if older_than_days else None
        
        for file in self.cache_dir.glob('*'):
            try:
                if file.is_file():
                    if cutoff_time is None:
                        file.unlink()
                        cleared_count += 1
                    else:
                        metadata_path = self.cache_dir / f"{file.stem}_metadata.json"
                        if metadata_path.exists():
                            with open(metadata_path, 'r', encoding='utf-8') as f:
                                metadata = json.load(f)
                            download_time = datetime.fromisoformat(metadata['download_time'])
                            if download_time < cutoff_time:
                                file.unlink()
                                metadata_path.unlink()
                                cleared_count += 1
            except Exception as e:
                logger.warning(f"清理缓存文件失败: {file}, {e}")
        
        logger.info(f"清理完成: 删除 {cleared_count} 个文件")
    
    def get_cache_info(self) -> Dict[str, Any]:
        """
        获取缓存统计信息
        
        Returns:
            缓存统计信息
        """
        total_files = 0
        total_size = 0
        file_types = {}
        
        for file in self.cache_dir.glob('*'):
            if file.is_file() and not file.name.endswith('_metadata.json'):
                total_files += 1
                size = file.stat().st_size
                total_size += size
                
                file_type = file.suffix[1:] if file.suffix else 'unknown'
                file_types[file_type] = file_types.get(file_type, 0) + 1
        
        info = {
            'total_files': total_files,
            'total_size': total_size,
            'total_size_mb': total_size / (1024 * 1024),
            'file_types': file_types,
            'cache_dir': str(self.cache_dir),
            'cache_days': self.cache_days,
        }
        
        logger.info(
            f"缓存统计: {total_files} 个文件, "
            f"总大小 {total_size / (1024 * 1024):.2f} MB, "
            f"类型分布 {file_types}"
        )
        
        return info
