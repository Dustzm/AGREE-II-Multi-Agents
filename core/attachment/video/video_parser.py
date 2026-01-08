"""
视频附件解析器

将视频文件通过 ASR 转写为文本内容。
"""
import logging
import shutil
from pathlib import Path
from typing import List

from core.config import get_settings
from ..parser import AttachmentParser, ParsedContent
from .audio_utils import AudioExtractor, AudioChunker
from .asr_client import ASRClient

logger = logging.getLogger(__name__)


class VideoAttachmentParser(AttachmentParser):
    """
    视频附件解析器
    
    处理流程：
    1. 使用 ffmpeg 从视频提取音频 (WAV)
    2. 将长音频分片（带重叠避免切断句子）
    3. 调用 ASR 服务转写每个分片
    4. 合并转写结果，返回 ParsedContent 列表
    
    支持格式：mp4, mov, avi, webm, mkv
    """
    
    def __init__(self):
        """初始化解析器"""
        settings = get_settings()
        self._asr_settings = settings.asr
        
        # 从配置读取分片参数
        self._chunk_duration = self._asr_settings.chunk_duration
        self._chunk_overlap = self._asr_settings.chunk_overlap
    
    @property
    def supported_extensions(self) -> List[str]:
        return ["mp4", "mov", "avi", "webm", "mkv"]
    
    async def parse(
        self, 
        file_path: Path, 
        prep_dir: Path = None, 
        **kwargs
    ) -> List[ParsedContent]:
        """
        解析视频附件
        
        Args:
            file_path: 视频文件路径
            prep_dir: 预处理输出目录
            **kwargs: 额外参数
            
        Returns:
            解析后的内容列表
        """
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"视频文件不存在: {file_path}")
            return []
        
        # 确定输出目录
        if prep_dir is None:
            prep_dir = file_path.parent / f"{file_path.stem}_prep"
        prep_dir = Path(prep_dir)
        prep_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"开始解析视频附件: {file_path.name}")
        
        try:
            # 1. 提取音频
            audio_path = prep_dir / f"{file_path.stem}.wav"
            await AudioExtractor.extract_audio(file_path, audio_path)
            
            # 2. 音频分片
            chunker = AudioChunker(
                chunk_duration=self._chunk_duration,
                overlap=self._chunk_overlap
            )
            chunks_dir = prep_dir / "chunks"
            chunks = await chunker.split(audio_path, chunks_dir)
            
            # 3. ASR 转写
            asr_client = ASRClient()
            segments = await asr_client.transcribe_chunks(chunks)
            
            # 4. 转换为 ParsedContent
            full_text = asr_client.segments_to_text(segments, with_timestamps=False)
            timestamped_text = asr_client.segments_to_text(segments, with_timestamps=True)
            
            # 保存转写结果到文件
            transcript_path = prep_dir / f"{file_path.stem}_transcript.txt"
            with open(transcript_path, "w", encoding="utf-8") as f:
                f.write(timestamped_text)
            logger.info(f"转写结果已保存: {transcript_path}")
            
            # 构建 ParsedContent
            contents = [
                ParsedContent(
                    title=f"[视频附件] {file_path.stem}",
                    content=full_text,
                    page_idx=0,
                    content_type="video_transcript",
                    metadata={
                        "source_file": str(file_path),
                        "transcript_file": str(transcript_path),
                        "duration_seconds": segments[-1].global_end if segments else 0,
                        "segment_count": len(segments)
                    }
                )
            ]
            
            # 清理临时分片文件
            if chunks_dir.exists():
                shutil.rmtree(chunks_dir)
            
            logger.info(f"视频附件解析完成: {file_path.name}, 转写长度: {len(full_text)} 字符")
            return contents
            
        except Exception as e:
            logger.error(f"视频附件解析失败: {e}", exc_info=True)
            return []
