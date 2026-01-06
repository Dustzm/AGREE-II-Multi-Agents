"""
迭代评价器

使用 LiteLLM 逐个遍历 chunks，累积证据并动态评分
"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

import litellm

from core.config import get_settings
from core.skills import load_skill_config
from core.prompts.iterative_prompts import (
    render_extraction_prompt,
    render_scoring_prompt,
    render_final_report_prompt,
)
from .core.preprocessor import EvidencePool
from .core.preprocessor.iterator import EvidenceIterator
from .core.preprocessor.pojo import SemanticChunk
from .tools.public_tools import get_task_paths

logger = logging.getLogger(__name__)


class IterativeEvaluator:
    """
    迭代评价器 - 遍历 chunks，累积证据，动态评分
    
    流程：
    1. 按文章原始顺序遍历 chunks
    2. 对每个 chunk 调用 LLM 提取证据
    3. 累积证据后调用 LLM 动态评分
    4. 满分 (7分) 时早停
    5. 最终生成完整 CoT 报告
    """
    
    def __init__(self, task_id: str, skill_id: str, lang: str = "en"):
        """
        初始化评价器
        
        Args:
            task_id: 任务 ID
            skill_id: 条目 ID (如 "item_7")
            lang: 提示词语言 (默认 en)
        """
        self.task_id = task_id
        self.skill_id = skill_id
        self.lang = lang
        
        # 累积状态
        self.evidence_cache: List[str] = []      # 累积的证据
        self.evidence_sources: List[str] = []    # 证据来源 (chunk_id 列表)
        self.current_score: int = 1              # 当前评分
        
        # 加载配置
        self.skill_config = load_skill_config(skill_id, check_enabled=True, lang=lang)
        self.domain_id = self.skill_config.get("domain", 1)
        
        # LLM 配置
        settings = get_settings()
        self.model = f"openai/{settings.llm.model_name}"
        self.llm_base_url = settings.llm.base_url
        self.llm_api_key = settings.llm.api_key or "dummy-key"
        
        logger.info(f"IterativeEvaluator 初始化: task={task_id}, skill={skill_id}, domain={self.domain_id}")
    
    def _clean_thinking_content(self, content: str) -> str:
        """
        移除内容中的 <think>...</think> 标签及其内容
        
        Args:
            content: 原始内容
            
        Returns:
            清理后的内容
        """
        import re
        # 使用 DOTALL 模式匹配跨行的 think 标签
        return re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    
    def evaluate(self) -> Dict[str, Any]:
        """
        执行迭代评价
        
        Returns:
            评价结果字典
        """
        # 1. 加载证据池
        paths = get_task_paths(self.task_id)
        if not paths:
            return self._error_result(f"任务不存在: {self.task_id}")
        
        prep_dir = paths["prep_dir"]
        
        try:
            pool = EvidencePool.load(prep_dir)
        except FileNotFoundError as e:
            return self._error_result(f"证据池加载失败: {e}")
        
        # 2. 创建迭代器
        iterator = EvidenceIterator(pool.chunks, self.domain_id)
        
        if len(iterator) == 0:
            logger.warning(f"领域 {self.domain_id} 无相关 chunks，自动评分为 1")
            return self._no_evidence_result()
        
        logger.info(f"开始迭代评价: {len(iterator)} 个 chunks")
        
        # 3. 迭代遍历
        # 3. 迭代遍历 (使用 batch 加速)
        # batch_size=15 可在 100K 上下文内安全处理，减少约 66% 的 LLM 调用
        batch_size = 15
        iterator_list = list(iterator)
        
        for i in range(0, len(iterator_list), batch_size):
            batch_chunks = iterator_list[i:i+batch_size]
            logger.info(f"处理 batch {i//batch_size + 1}/{(len(iterator_list)+batch_size-1)//batch_size}: 包含 {len(batch_chunks)} 个 chunks")
            
            # 3.1 提取证据 (Batch)
            new_evidence = self._extract_evidence_batch(batch_chunks)
            
            if new_evidence:
                self.evidence_cache.extend(new_evidence)
                for chunk in batch_chunks:
                    self.evidence_sources.append(chunk.chunk_id)
                logger.info(f"Batch 提取到 {len(new_evidence)} 条证据")
            
            # 3.2 动态评分 (仅在有新证据时)
            if self.evidence_cache:
                self.current_score = self._score_current()
                logger.info(f"当前评分: {self.current_score}")
            
            # 3.3 早停检查
            if self.current_score == 7:
                logger.info(f"达到满分 7 分，提前结束")
                break
        
        # 4. 生成最终报告
        return self._synthesize_report()
    
    def _extract_evidence_batch(self, chunks: List[SemanticChunk]) -> List[str]:
        """
        从 batch chunks 提取与评价标准相关的证据
        
        Args:
            chunks: 语义块列表
        
        Returns:
            提取的证据列表
        """
        # 拼接 chunk 内容
        combined_content = []
        for c in chunks:
            combined_content.append(f"## {c.title}\n{c.content}")
        
        full_text = "\n\n".join(combined_content)

        # 格式化评分表格 (作为提取依据)
        scoring_rows = []
        for sp in self.skill_config.get("scoring_principle", []):
            condition = sp.get("condition", sp.get("condition_zh", ""))
            scoring_rows.append(f"| {sp['range']} | {condition} |")
        scoring_table = "\n".join(scoring_rows)

        prompt = render_extraction_prompt(
            item_id=self.skill_id,
            title=self.skill_config.get("title", ""),
            scoring_table=scoring_table,
            chunk_content=full_text,
        )
        
        # 定义 Pydantic Schema 用于约束输出
        from pydantic import BaseModel, Field
        
        class EvidenceOutput(BaseModel):
            evidence: List[str] = Field(description="A list of evidence strings extracted from the text")

        try:
            # 使用 response_format={ "type": "json_object" } 配合 Pydantic Schema 约束
            
            # 临时修改 prompt 结尾以匹配 JSON Object 格式要求
            system_instruction = "You are a strict extraction system. Output JSON only."
            api_prompt = prompt + "\n\nIMPORTANT: Return a JSON object with a single key 'evidence' containing the list of strings. Example: {\"evidence\": [\"text1\", \"text2\"]}"

            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": api_prompt}
                ],
                api_base=self.llm_base_url,
                api_key=self.llm_api_key,
                temperature=0.3,  # 提高温度，增加灵活性，避免过度严格
                max_tokens=8192, # Batch 模式下允许提取更多
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content.strip()
            content = self._clean_thinking_content(content)
            
            # 解析 JSON
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    evidence_list = data.get("evidence", [])
                elif isinstance(data, list):
                    evidence_list = data
                else:
                    evidence_list = []
            except json.JSONDecodeError:
                import re
                match = re.search(r'\[.*\]', content, re.DOTALL)
                if match:
                    evidence_list = json.loads(match.group(0))
                else:
                    return []
            
            if isinstance(evidence_list, list):
                return [str(e) for e in evidence_list if e]
            return []
            
        except Exception as e:
            logger.warning(f"Batch 证据提取失败: {e}")
            return []
    
    def _score_current(self) -> int:
        """
        根据当前累积证据评分
        
        Returns:
            当前评分 (1-7)
        """
        # 格式化评分表格
        scoring_rows = []
        for sp in self.skill_config.get("scoring_principle", []):
            condition = sp.get("condition", sp.get("condition_zh", ""))
            scoring_rows.append(f"| {sp['range']} | {condition} |")
        scoring_table = "\n".join(scoring_rows)
        
        prompt = render_scoring_prompt(
            item_id=self.skill_id,
            title=self.skill_config.get("title", ""),
            criteria=self.skill_config.get("criteria", []),
            evidence_cache=self.evidence_cache,
            scoring_table=scoring_table,
            n_sources=len(self.evidence_sources),
        )
        
        # 修改 prompt 以输出 JSON 格式
        system_instruction = "You are a scoring assistant. Output JSON only."
        api_prompt = prompt + "\n\nIMPORTANT: Return a JSON object with exactly two keys:\n1. 'analysis': brief explanation of which criteria are met\n2. 'score': integer 1-7\n\nExample: {\"analysis\": \"The evidence covers...\", \"score\": 5}"
        
        try:
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": api_prompt}
                ],
                api_base=self.llm_base_url,
                api_key=self.llm_api_key,
                temperature=0.1,
                max_tokens=4096,  # 给评分分析留足空间
                response_format={"type": "json_object"}  # 强制 JSON 输出
            )
            
            content = response.choices[0].message.content.strip()
            content = self._clean_thinking_content(content)
            logger.debug(f"动态评分 JSON 输出: {content[:200]}...")
            
            # 解析 JSON
            data = json.loads(content)
            score = data.get("score", self.current_score)
            
            # 处理可能的范围格式如 "3-4"
            if isinstance(score, str):
                if "-" in score:
                    # 取范围的较高值
                    parts = score.split("-")
                    score = int(parts[-1].strip())
                else:
                    score = int(score)
            
            if 1 <= score <= 7:
                logger.info(f"从 JSON 提取到评分: {score}")
                return score
            else:
                logger.warning(f"评分 {score} 超出范围，使用当前值: {self.current_score}")
                return self.current_score
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}, 尝试正则回退")
            # 回退到正则匹配
            import re
            match = re.search(r'"score"\s*:\s*(\d)', content)
            if match:
                score = int(match.group(1))
                if 1 <= score <= 7:
                    return score
            return self.current_score
            
        except Exception as e:
            logger.warning(f"评分失败: {e}")
            return self.current_score
    
    def _synthesize_report(self) -> Dict[str, Any]:
        """
        综合所有证据生成最终报告
        
        Returns:
            完整评价结果
        """
        # 格式化评分表格
        scoring_rows = []
        for sp in self.skill_config.get("scoring_principle", []):
            condition = sp.get("condition", sp.get("condition_zh", ""))
            scoring_rows.append(f"| {sp['range']} | {condition} |")
        scoring_table = "\n".join(scoring_rows)
        
        prompt = render_final_report_prompt(
            item_id=self.skill_id,
            domain_name=self.skill_config.get("domain_name", ""),
            title=self.skill_config.get("title", ""),
            criteria=self.skill_config.get("criteria", []),
            scoring_table=scoring_table,
            evidence_cache=self.evidence_cache,
            evidence_sources=self.evidence_sources,
            final_score=self.current_score,
        )
        
        try:
            response = litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                api_base=self.llm_base_url,
                api_key=self.llm_api_key,
                temperature=0.3,  # 稍高温度以获得更丰富的推理
                max_tokens=16384, # 最终报告需要生成详细的 CoT，证据多时需要更大空间
            )
            
            content = response.choices[0].message.content.strip()
            content = self._clean_thinking_content(content)
            logger.debug(f"LLM 原始输出: {content[:500]}...")
            
            # 解析 JSON - 增强容错性
            # 移除 markdown 代码块
            if "```json" in content:
                content = content.split("```json")[1]
                if "```" in content:
                    content = content.split("```")[0]
            elif content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            content = content.strip()
            
            # 尝试提取 JSON 对象
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                content = json_match.group(0)
            
            try:
                cot = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"JSON 解析失败: {e}, 原始内容: {content}...")
                # 尝试从文本中提取 reasoning
                reasoning_match = re.search(r'step3_reasoning["\s:]+([^"]*"[^"]+"|[^\n,}]+)', content)
                if reasoning_match:
                    cot = {
                        "step1_locate": f"证据来自以下章节: {', '.join(self.evidence_sources)}",
                        "step3_reasoning": reasoning_match.group(1).strip().strip('"')
                    }
                else:
                    raise e
            
            return {
                "status": "success",
                "skill_id": self.skill_id,
                "domain": self.domain_id,
                "title": self.skill_config.get("title", ""),
                "chain_of_thought": {
                    "step1_locate": cot.get("step1_locate", f"证据来自以下章节: {', '.join(self.evidence_sources)}"),
                    "step2_evidence": self.evidence_cache,
                    "step3_reasoning": cot.get("step3_reasoning", ""),
                    "score": self.current_score,
                },
                "evidence_sources": self.evidence_sources,
                "iteration_stats": {
                    "total_chunks": len(self.evidence_sources) if self.evidence_sources else 0,
                    "evidence_count": len(self.evidence_cache),
                    "early_stopped": self.current_score == 7,
                },
            }
            
        except Exception as e:
            logger.warning(f"报告生成失败: {e}, 使用简化报告")
            return self._simple_report()
    
    def _simple_report(self) -> Dict[str, Any]:
        """生成简化报告（LLM 调用失败时的回退）"""
        # 尝试从评分标准中找到对应的条件描述
        scoring_principles = self.skill_config.get("scoring_principle", [])
        matched_condition = ""
        for sp in scoring_principles:
            score_range = sp.get("range", "")
            # 处理范围格式如 "5-6" 或单个值 "7"
            if "-" in str(score_range):
                low, high = map(int, str(score_range).split("-"))
                if low <= self.current_score <= high:
                    matched_condition = sp.get("condition", sp.get("condition_zh", ""))
                    break
            elif str(score_range) == str(self.current_score):
                matched_condition = sp.get("condition", sp.get("condition_zh", ""))
                break
        
        reasoning = f"基于 {len(self.evidence_cache)} 条证据进行评价。"
        if matched_condition:
            reasoning += f" 根据评分标准，符合【{matched_condition}】条件，评分为 {self.current_score}。"
        else:
            reasoning += f" 评分为 {self.current_score}。"
        
        return {
            "status": "success",
            "skill_id": self.skill_id,
            "domain": self.domain_id,
            "title": self.skill_config.get("title", ""),
            "chain_of_thought": {
                "step1_locate": f"证据来自以下章节: {', '.join(self.evidence_sources)}",
                "step2_evidence": self.evidence_cache,
                "step3_reasoning": reasoning,
                "score": self.current_score,
            },
            "evidence_sources": self.evidence_sources,
            "iteration_stats": {
                "total_chunks": len(self.evidence_sources),
                "evidence_count": len(self.evidence_cache),
                "early_stopped": self.current_score == 7,
            },
        }
    
    def _no_evidence_result(self) -> Dict[str, Any]:
        """无证据时的结果"""
        return {
            "status": "no_evidence",
            "skill_id": self.skill_id,
            "domain": self.domain_id,
            "title": self.skill_config.get("title", ""),
            "chain_of_thought": {
                "step1_locate": "无法定位：证据池中无该领域相关内容",
                "step2_evidence": [],
                "step3_reasoning": "由于证据池中不存在与该评价条目领域相关的语义块，无法提取有效证据进行评价。根据 AGREE-II 评分原则，信息完全缺失时应评为最低分。",
                "score": 1,
            },
            "evidence_sources": [],
            "iteration_stats": {
                "total_chunks": 0,
                "evidence_count": 0,
                "early_stopped": False,
            },
        }
    
    def _error_result(self, error: str) -> Dict[str, Any]:
        """错误结果"""
        return {
            "status": "error",
            "skill_id": self.skill_id,
            "error": error,
        }


def run_iterative_evaluation(task_id: str, skill_id: str, lang: str = "en") -> Dict[str, Any]:
    """
    执行迭代评价的快捷函数
    
    Args:
        task_id: 任务 ID
        skill_id: 条目 ID
        lang: 提示词语言
    
    Returns:
        评价结果
    """
    evaluator = IterativeEvaluator(task_id, skill_id, lang)
    return evaluator.evaluate()


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "IterativeEvaluator",
    "run_iterative_evaluation",
]
