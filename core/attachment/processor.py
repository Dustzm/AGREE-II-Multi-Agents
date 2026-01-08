"""
附件处理器

整合检测器和解析器，提供统一的附件处理接口。
"""
import logging
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass, field

from .detector import AttachmentDetector, DetectedAttachment, SiblingFileDetector
from .parser import AttachmentParser, ParsedContent, PDFAttachmentParser
from .video import VideoAttachmentParser

logger = logging.getLogger(__name__)


@dataclass
class AttachmentInfo:
    """附件处理结果"""
    source_file: Path                        # 源附件文件
    detector: str                            # 使用的检测器
    index: int = 0                           # 附件编号（从 1 开始）
    output_dir: Path = None                  # 解析输出目录
    contents: List[ParsedContent] = field(default_factory=list)  # 解析后的内容
    error: str = ""                          # 错误信息


class AttachmentProcessor:
    """
    附件处理器
    
    使用配置的检测器和解析器处理附件。
    支持链式添加检测器和解析器。
    
    使用示例：
        processor = AttachmentProcessor()
        results = processor.process(Path("input/guideline.pdf"))
        for result in results:
            print(f"附件: {result.source_file}, 内容块数: {len(result.contents)}")
    """
    
    def __init__(self, auto_setup: bool = True):
        """
        初始化处理器
        
        Args:
            auto_setup: 是否自动配置默认检测器和解析器
        """
        self._detectors: List[AttachmentDetector] = []
        self._parsers: Dict[str, AttachmentParser] = {}  # ext -> parser
        
        if auto_setup:
            self._setup_defaults()
    
    def _setup_defaults(self):
        """设置默认检测器和解析器"""
        self.add_detector(SiblingFileDetector())
        self.add_parser(PDFAttachmentParser())
        self.add_parser(VideoAttachmentParser())
    
    def add_detector(self, detector: AttachmentDetector) -> "AttachmentProcessor":
        """添加检测器（支持链式调用）"""
        self._detectors.append(detector)
        logger.debug(f"添加检测器: {detector.name}")
        return self
    
    def add_parser(self, parser: AttachmentParser) -> "AttachmentProcessor":
        """添加解析器（支持链式调用）"""
        for ext in parser.supported_extensions:
            self._parsers[ext.lower()] = parser
            logger.debug(f"添加解析器: {ext} -> {parser.__class__.__name__}")
        return self
    
    async def process(
        self, 
        source_path: Path, 
        prep_dir: Path = None,
        **kwargs
    ) -> List[AttachmentInfo]:
        """
        处理附件
        
        Args:
            source_path: 源文件路径（主指南 PDF）
            prep_dir: 预处理输出目录（如 output/{task_id}/.ama）
            **kwargs: 传递给检测器和解析器的额外参数
            
        Returns:
            附件处理结果列表
        """
        source_path = Path(source_path)
        results = []
        
        # 1. 检测附件
        all_attachments: List[DetectedAttachment] = []
        for detector in self._detectors:
            try:
                detected = detector.detect(source_path, **kwargs)
                all_attachments.extend(detected)
            except Exception as e:
                logger.error(f"检测器 {detector.name} 异常: {e}")
        
        if not all_attachments:
            logger.info(f"未检测到附件: {source_path}")
            return results
        
        logger.info(f"检测到 {len(all_attachments)} 个附件")
        
        # 2. 为附件分配编号，创建编号目录
        attachment_base_dir = None
        if prep_dir:
            attachment_base_dir = Path(prep_dir) / "attachment"
            attachment_base_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"附件输出根目录: {attachment_base_dir}")
        
        for idx, attachment in enumerate(all_attachments, start=1):
            # 分配编号
            attachment.index = idx
            
            # 创建编号目录：attachment/{id}/
            # parser 会在此目录下创建以附件名命名的子目录
            if attachment_base_dir:
                attachment.output_dir = attachment_base_dir / str(idx)
                attachment.output_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"附件 {idx}: {attachment.path.name} -> {attachment.output_dir}")
        
        # 3. 解析附件
        for attachment in all_attachments:
            ext = attachment.type.lower()
            parser = self._parsers.get(ext)
            
            if parser is None:
                logger.warning(f"无可用解析器: {attachment.path} (type={ext})")
                results.append(AttachmentInfo(
                    source_file=attachment.path,
                    detector=attachment.source,
                    index=attachment.index,
                    output_dir=attachment.output_dir,
                    error=f"不支持的文件类型: {ext}"
                ))
                continue
            
            try:
                # 解析附件（异步，使用编号目录作为输出）
                parsed_contents = await parser.parse(
                    attachment.path, 
                    prep_dir=attachment.output_dir,
                    **kwargs
                )
                
                results.append(AttachmentInfo(
                    source_file=attachment.path,
                    detector=attachment.source,
                    index=attachment.index,
                    output_dir=attachment.output_dir,
                    contents=parsed_contents,
                ))
                
                logger.info(f"附件 {attachment.index} 处理完成: {attachment.path.name}, {len(parsed_contents)} 个内容块")
                
            except Exception as e:
                logger.error(f"解析附件失败: {attachment.path}, error={e}", exc_info=True)
                results.append(AttachmentInfo(
                    source_file=attachment.path,
                    detector=attachment.source,
                    index=attachment.index,
                    output_dir=attachment.output_dir,
                    error=str(e)
                ))
        
        return results

