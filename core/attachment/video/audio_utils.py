"""
音频处理工具模块

提供从视频提取音频、音频分片等功能。
使用 ffmpeg 作为底层工具。
"""
import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChunkInfo:
    """音频分片信息"""
    path: Path          # 分片文件路径
    start_time: float   # 开始时间 (秒)
    end_time: float     # 结束时间 (秒)
    index: int          # 分片索引 (从 0 开始)


class AudioExtractor:
    """
    从视频文件提取音频
    
    使用 ffmpeg 将视频转换为 WAV 格式音频。
    输出格式：16kHz, 单声道, PCM 16-bit
    """
    
    @staticmethod
    async def extract_audio(
        video_path: Path, 
        output_path: Path,
        sample_rate: int = 16000
    ) -> Path:
        """
        从视频提取音频
        
        Args:
            video_path: 视频文件路径
            output_path: 输出音频文件路径 (.wav)
            sample_rate: 采样率 (默认 16000Hz，适合 ASR)
            
        Returns:
            输出音频文件路径
            
        Raises:
            RuntimeError: ffmpeg 执行失败
        """
        video_path = Path(video_path)
        output_path = Path(output_path)
        
        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        
        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # ffmpeg 命令：提取音频并转换格式
        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vn",                    # 不处理视频
            "-acodec", "pcm_s16le",   # PCM 16-bit 编码
            "-ar", str(sample_rate),  # 采样率
            "-ac", "1",               # 单声道
            "-y",                     # 覆盖已存在文件
            str(output_path)
        ]
        
        logger.info(f"提取音频: {video_path.name} -> {output_path.name}")
        
        # 异步执行 ffmpeg
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        _, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(f"ffmpeg 提取音频失败: {error_msg}")
        
        logger.info(f"音频提取完成: {output_path}")
        return output_path
    
    @staticmethod
    async def get_duration(audio_path: Path) -> float:
        """
        获取音频文件时长 (秒)
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            音频时长 (秒)
        """
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, _ = await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"无法获取音频时长: {audio_path}")
        
        return float(stdout.decode().strip())


class AudioChunker:
    """
    音频分片器
    
    将长音频按固定时长切分为多个片段，支持重叠。
    重叠可以避免在分片边界处切断句子。
    """
    
    def __init__(
        self, 
        chunk_duration: int = 60, 
        overlap: int = 5
    ):
        """
        初始化分片器
        
        Args:
            chunk_duration: 分片时长 (秒)
            overlap: 重叠时长 (秒)
        """
        self.chunk_duration = chunk_duration
        self.overlap = overlap
    
    async def split(
        self, 
        audio_path: Path, 
        output_dir: Path
    ) -> List[ChunkInfo]:
        """
        将音频分片
        
        Args:
            audio_path: 源音频文件路径
            output_dir: 分片输出目录
            
        Returns:
            分片信息列表
        """
        audio_path = Path(audio_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 获取音频总时长
        duration = await AudioExtractor.get_duration(audio_path)
        logger.info(f"音频总时长: {duration:.1f}s, 分片参数: {self.chunk_duration}s (重叠 {self.overlap}s)")
        
        # 如果音频短于一个分片，直接返回原文件
        if duration <= self.chunk_duration:
            return [ChunkInfo(
                path=audio_path,
                start_time=0,
                end_time=duration,
                index=0
            )]
        
        # 计算分片起止点
        chunks = []
        step = self.chunk_duration - self.overlap  # 步长 = 分片时长 - 重叠
        start = 0.0
        index = 0
        
        while start < duration:
            end = min(start + self.chunk_duration, duration)
            
            # 生成分片文件名
            chunk_name = f"chunk_{index:03d}.wav"
            chunk_path = output_dir / chunk_name
            
            # 使用 ffmpeg 切分
            await self._extract_segment(
                audio_path, chunk_path, start, end - start
            )
            
            chunks.append(ChunkInfo(
                path=chunk_path,
                start_time=start,
                end_time=end,
                index=index
            ))
            
            start += step
            index += 1
        
        logger.info(f"音频分片完成: 共 {len(chunks)} 个分片")
        return chunks
    
    async def _extract_segment(
        self, 
        audio_path: Path, 
        output_path: Path, 
        start: float, 
        duration: float
    ):
        """使用 ffmpeg 提取音频片段"""
        cmd = [
            "ffmpeg",
            "-i", str(audio_path),
            "-ss", str(start),        # 开始时间
            "-t", str(duration),      # 持续时间
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-y",
            str(output_path)
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        _, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(f"ffmpeg 分片失败: {error_msg}")
