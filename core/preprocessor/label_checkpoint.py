"""语义分类断点：只复用同一输入、模型和提示词的成功标签。

断点是追加写入的 JSONL：首行为身份，其后每行一条成功标签。每条记录只追加一行，
不重写整个文件；中断时最后一行可能不完整，读取时跳过即可。
"""

import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from .pojo import SemanticChunk, SemanticLabel, TitleContentBlock

logger = logging.getLogger(__name__)


def digest_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _chunk_digest(chunk: TitleContentBlock) -> str:
    data = [chunk.title, chunk.raw_text, chunk.section, chunk.title_page_idx]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()


class LabelCheckpoint:
    """按内容哈希记录成功标签；身份不一致的旧断点保留后重新开始。"""

    def __init__(self, content_list_path: str, output_path: str, prompt_template: str, lang: str):
        from core.llm.context import current_llm_settings
        llm = current_llm_settings()
        self.source_digest = digest_file(content_list_path)
        self.identity = hashlib.sha256(json.dumps([
            self.source_digest, prompt_template, lang,
            llm.provider, llm.model_name, llm.base_url,
        ], ensure_ascii=False).encode("utf-8")).hexdigest()
        output = Path(output_path)
        self.path = output.with_name(f"{output.stem}_labels_{self.identity[:12]}.jsonl")
        self.labels: dict[str, dict] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        try:
            if json.loads(lines[0]).get("identity") != self.identity:
                raise ValueError("语义分类断点身份不一致")
        except (IndexError, ValueError, AttributeError):
            preserved = self.path.with_name(f"{self.path.stem}.invalid-{uuid4().hex[:8]}.jsonl")
            self.path.rename(preserved)
            logger.exception("无效语义分类断点已保留: %s", preserved)
            return
        for line in lines[1:]:
            try:
                record = json.loads(line)
                self.labels[str(record["index"])] = record
            except (ValueError, KeyError, TypeError):
                logger.warning("跳过不完整的语义分类断点记录: %s", self.path)

    def get(self, index: int, chunk: TitleContentBlock) -> tuple[SemanticLabel, str] | None:
        saved = self.labels.get(str(index))
        if not isinstance(saved, dict) or saved.get("chunk_digest") != _chunk_digest(chunk):
            return None
        reasoning = saved.get("reasoning", "")
        if not isinstance(reasoning, str):
            return None
        try:
            return SemanticLabel(saved["label"]), reasoning
        except (KeyError, ValueError, TypeError):
            return None

    def record(self, index: int, chunk: TitleContentBlock, result: SemanticChunk) -> None:
        record = {
            "index": index,
            "chunk_digest": _chunk_digest(chunk),
            "label": result.label.value,
            "reasoning": result.reasoning,
        }
        self.labels[str(index)] = record
        lines = []
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines.append(json.dumps({"identity": self.identity}))
        lines.append(json.dumps(record, ensure_ascii=False))
        with self.path.open("a", encoding="utf-8") as journal:
            journal.write("\n".join(lines) + "\n")
