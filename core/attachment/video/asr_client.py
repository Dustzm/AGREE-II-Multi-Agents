"""
ASR 客户端模块

调用服务器端 ASR 模型进行语音转写。
兼容 OpenAI Whisper API 格式。
"""
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import httpx

from core.config import get_settings
from .audio_utils import ChunkInfo

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    """转写片段"""
    text: str           # 转写文本
    start: float        # 开始时间 (秒，相对于分片)
    end: float          # 结束时间 (秒，相对于分片)
    
    # 全局时间戳（相对于原始音频）
    global_start: float = 0.0
    global_end: float = 0.0


class ASRClient:
    """
    ASR 服务客户端
    
    兼容 OpenAI Whisper API 格式：
    POST /v1/audio/transcriptions
    Content-Type: multipart/form-data
    
    响应格式 (verbose_json):
    {"segments": [{"text": "...", "start": 0.0, "end": 2.5}, ...]}
    """
    
    def __init__(self):
        """初始化客户端，从配置加载参数"""
        settings = get_settings()
        self._settings = settings.asr
        
        self._base_url = self._settings.server_url.rstrip("/")
        self._model = self._settings.model_name
        self._api_key = self._settings.api_key
        self._timeout = self._settings.timeout
        self._max_concurrency = self._settings.max_concurrency
        
        logger.info(f"ASR 客户端初始化: {self._base_url}, model={self._model}")
    
    async def transcribe(
        self, 
        audio_path: Path,
        language: str = "zh"
    ) -> List[TranscriptSegment]:
        """
        转写单个音频文件
        
        Args:
            audio_path: 音频文件路径
            language: 语言代码 (zh/en/auto)
            
        Returns:
            转写片段列表
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")
        
        url = f"{self._base_url}/audio/transcriptions"
        
        # 构建请求头
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        
        # 构建表单数据
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path.name, f, "audio/wav")}
                data = {
                    "model": self._model,
                    "language": language,
                    "response_format": "verbose_json"
                }
                
                logger.debug(f"ASR 请求: {audio_path.name}")
                
                response = await client.post(
                    url,
                    files=files,
                    data=data,
                    headers=headers
                )
        
        if response.status_code != 200:
            error_text = response.text
            raise RuntimeError(f"ASR 请求失败 ({response.status_code}): {error_text}")
        
        result = response.json()
        
        # 解析响应
        segments = []
        
        # 处理 verbose_json 格式
        if "segments" in result:
            for seg in result["segments"]:
                segments.append(TranscriptSegment(
                    text=seg.get("text", "").strip(),
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0)
                ))
        # 处理简单 text 格式
        elif "text" in result:
            segments.append(TranscriptSegment(
                text=result["text"].strip(),
                start=0.0,
                end=0.0
            ))
        
        logger.debug(f"ASR 完成: {audio_path.name}, {len(segments)} 个片段")
        return segments
    
    async def transcribe_chunks(
        self, 
        chunks: List[ChunkInfo],
        language: str = "zh"
    ) -> List[TranscriptSegment]:
        """
        转写多个分片并合并结果
        
        按串行方式处理（受 max_concurrency 限制）。
        自动处理时间戳偏移和重叠去重。
        
        Args:
            chunks: 分片信息列表
            language: 语言代码
            
        Returns:
            合并后的转写片段列表（按全局时间排序）
        """
        if not chunks:
            return []
        
        all_segments: List[TranscriptSegment] = []
        
        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(self._max_concurrency)
        
        async def transcribe_with_semaphore(chunk: ChunkInfo):
            async with semaphore:
                return await self._transcribe_chunk(chunk, language)
        
        # 串行/并发转写所有分片
        logger.info(f"开始转写 {len(chunks)} 个分片 (并发={self._max_concurrency})")
        
        tasks = [transcribe_with_semaphore(chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 收集结果
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"分片 {i} 转写失败: {result}")
                continue
            all_segments.extend(result)
        
        # 按全局时间排序
        all_segments.sort(key=lambda x: x.global_start)
        
        # 去重（处理重叠区域）
        merged = self._merge_overlapping_segments(all_segments)
        
        logger.info(f"转写完成: 共 {len(merged)} 个片段")
        return merged
    
    async def _transcribe_chunk(
        self, 
        chunk: ChunkInfo,
        language: str
    ) -> List[TranscriptSegment]:
        """转写单个分片，添加全局时间戳"""
        segments = await self.transcribe(chunk.path, language)
        
        # 添加全局时间戳（相对于原始音频）
        for seg in segments:
            seg.global_start = chunk.start_time + seg.start
            seg.global_end = chunk.start_time + seg.end
        
        return segments
    
    def _merge_overlapping_segments(
        self, 
        segments: List[TranscriptSegment]
    ) -> List[TranscriptSegment]:
        """
        合并重叠区域的片段
        
        简单策略：如果两个片段时间重叠，保留较早的那个
        """
        if not segments:
            return []
        
        merged = [segments[0]]
        
        for seg in segments[1:]:
            last = merged[-1]
            
            # 如果当前片段与上一个重叠，跳过
            if seg.global_start < last.global_end:
                # 可以选择合并文本或直接跳过
                # 这里选择跳过重叠的片段
                continue
            
            merged.append(seg)
        
        return merged
    
    def segments_to_text(
        self, 
        segments: List[TranscriptSegment],
        with_timestamps: bool = False
    ) -> str:
        """
        将转写片段合并为完整文本
        
        Args:
            segments: 转写片段列表
            with_timestamps: 是否包含时间戳
            
        Returns:
            完整转写文本
        """
        if with_timestamps:
            lines = []
            for seg in segments:
                timestamp = f"[{seg.global_start:.1f}s - {seg.global_end:.1f}s]"
                lines.append(f"{timestamp} {seg.text}")
            return "\n".join(lines)
        else:
            return " ".join(seg.text for seg in segments)
