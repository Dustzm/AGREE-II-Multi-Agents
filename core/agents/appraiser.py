"""
迭代评价器

使用 LiteLLM 逐个遍历 chunks，累积证据并动态评分
"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

import litellm
from pydantic import BaseModel, Field

from core.utils.token_tracker import add_litellm_usage

from core.config import get_settings
from core.skills import load_skill_config
from core.prompts.iterative_prompts import (
    render_extraction_prompt,
    render_scoring_prompt,
    render_final_report_prompt,
)
from core.preprocessor import EvidencePool
from core.preprocessor.iterator import EvidenceIterator
from core.preprocessor.pojo import SemanticChunk
from .tools.public_tools import get_task_paths

logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Schema 定义 - 强制 LLM 输出结构
# ============================================================================

class ReasonItem(BaseModel):
    """单个评分理由"""
    point: str = Field(description="评分要点名称")
    explanation: str = Field(description="详细解释")
    evidence_chunks: List[str] = Field(default_factory=list, description="相关的 chunk_id 列表")


class StructuredReasoning(BaseModel):
    """结构化推理"""
    summary: str = Field(description="一句话总结")
    reasons: List[ReasonItem] = Field(description="分条理由列表")
    score_justification: str = Field(description="评分判定理由")


class FinalReportOutput(BaseModel):
    """最终报告输出 Schema"""
    step1_locate: str = Field(description="证据来源位置描述")
    step3_reasoning: StructuredReasoning = Field(description="结构化推理")




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
        self.evidence_cache: List[str] = []      # 累积的证据文本
        self.evidence_chunk_map: List[str] = []  # 每条证据对应的 chunk_id（与 evidence_cache 一一对应）
        self.evidence_sources: List[str] = []    # 所有处理过的 chunk_id 列表
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
    
    def _extract_content_from_response(self, response) -> tuple[str, str | None]:
        """
        从 LLM 响应中提取内容和思考内容
        
        Args:
            response: LiteLLM 响应对象
            
        Returns:
            (content, reasoning_content) 元组
        """
        message = response.choices[0].message
        content = message.content.strip() if message.content else ""
        
        # vLLM + reasoning-parser 模式下，reasoning 内容会在单独字段
        reasoning_content = getattr(message, 'reasoning_content', None)
        
        # 清理可能残留的 think 标签
        content = self._clean_thinking_content(content)
        
        return content, reasoning_content
    
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
        batch_size = 5
        iterator_list = list(iterator)
        
        for i in range(0, len(iterator_list), batch_size):
            batch_chunks = iterator_list[i:i+batch_size]
            logger.info(f"处理 batch {i//batch_size + 1}/{(len(iterator_list)+batch_size-1)//batch_size}: 包含 {len(batch_chunks)} 个 chunks")
            
            # 3.1 提取证据 (Batch) - 返回 (evidence_texts, chunk_ids)
            new_evidence, new_chunk_ids = self._extract_evidence_batch(batch_chunks)
            
            if new_evidence:
                self.evidence_cache.extend(new_evidence)
                self.evidence_chunk_map.extend(new_chunk_ids)
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
    
    def _extract_evidence_batch(self, chunks: List[SemanticChunk]) -> tuple[List[str], List[str]]:
        """
        从 batch chunks 提取与评价标准相关的证据
        
        Args:
            chunks: 语义块列表
        
        Returns:
            (evidence_texts, chunk_ids) 元组：证据文本列表和对应的 chunk_id 列表
        """
        # 记录处理的 chunk 内容
        for c in chunks:
            logger.info(f"Evaluating Chunk: {c.title}")
            logger.info(f"Chunk Content: {c.content}")

        # 拼接 chunk 内容（带 chunk_id 标记，便于 LLM 识别来源）
        combined_content = []
        for c in chunks:
            combined_content.append(f"[{c.chunk_id}] ## {c.title}\n{c.content}")
        
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
        
        # 定义 Pydantic Schema 用于约束输出（新格式：包含 chunk_id）
        from pydantic import BaseModel, Field
        
        class EvidenceItem(BaseModel):
            chunk_id: str = Field(description="The chunk_id where the evidence was found")
            evidence: str = Field(description="The extracted evidence text")
        
        class EvidenceOutput(BaseModel):
            evidence: List[EvidenceItem] = Field(description="List of evidence items with chunk_id")

        try:
            # 使用 response_format={ "type": "json_object" } 配合 Pydantic Schema 约束
            system_instruction = "You are a strict extraction system. Output JSON only."
            # 提示词已在 iterative_prompts.py 中修改为新格式

            logger.info("=== [Evidence Extraction] Input Prompt ===")
            logger.info(prompt)
            logger.info("==========================================")

            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                api_base=self.llm_base_url,
                api_key=self.llm_api_key,
                temperature=0.1,  # 提高温度，增加灵活性，避免过度严格
                top_p=0.9,
                max_tokens=8192, # Batch 模式下允许提取更多
                response_format={"type": "json_object"},  # vLLM 不支持 Pydantic Schema，使用 json_object
                extra_body={"chat_template_kwargs": {"enable_thinking": True}},  # vLLM Qwen3 启用思考模式
            )
            
            # 统计 Token 使用
            add_litellm_usage(response)
            
            # 提取内容和思考内容
            content, reasoning_content = self._extract_content_from_response(response)
            
            # 记录思考内容 (如果存在)
            if reasoning_content:
                logger.info("=== [Evidence Extraction] Reasoning Content ===")
                logger.info(reasoning_content[:500] + "..." if len(reasoning_content) > 500 else reasoning_content)
                logger.info("===============================================")
            
            logger.info("=== [Evidence Extraction] Output Content ===")
            logger.info(content)
            logger.info("============================================")
            
            # 解析 JSON（新格式：包含 chunk_id）
            evidence_texts: List[str] = []
            chunk_ids: List[str] = []
            
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    evidence_list = data.get("evidence", [])
                elif isinstance(data, list):
                    evidence_list = data
                else:
                    evidence_list = []
                
                # 解析新格式 [{chunk_id, evidence}]
                for item in evidence_list:
                    if isinstance(item, dict):
                        # 新格式：{"chunk_id": "xxx", "evidence": "text"}
                        chunk_id = item.get("chunk_id", "unknown")
                        evidence_text = item.get("evidence", "")
                        if evidence_text:
                            evidence_texts.append(str(evidence_text))
                            chunk_ids.append(str(chunk_id))
                    elif isinstance(item, str) and item:
                        # 兼容旧格式：纯字符串数组（回退到第一个 chunk）
                        evidence_texts.append(str(item))
                        chunk_ids.append(chunks[0].chunk_id if chunks else "unknown")
                        
            except json.JSONDecodeError:
                import re
                # 尝试匹配 JSON 数组
                match = re.search(r'\[.*\]', content, re.DOTALL)
                if match:
                    try:
                        evidence_list = json.loads(match.group(0))
                        for item in evidence_list:
                            if isinstance(item, dict):
                                chunk_id = item.get("chunk_id", "unknown")
                                evidence_text = item.get("evidence", "")
                                if evidence_text:
                                    evidence_texts.append(str(evidence_text))
                                    chunk_ids.append(str(chunk_id))
                            elif isinstance(item, str) and item:
                                evidence_texts.append(str(item))
                                chunk_ids.append(chunks[0].chunk_id if chunks else "unknown")
                    except json.JSONDecodeError:
                        pass
            
            return evidence_texts, chunk_ids
            
        except Exception as e:
            logger.warning(f"Batch 证据提取失败: {e}")
            return [], []

    
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
            shots=self.skill_config.get("shots", []),
            evidence_cache=self.evidence_cache,
            scoring_table=scoring_table,
            n_sources=len(self.evidence_sources),
        )
        
        # 修改 prompt 以输出 JSON 格式
        system_instruction = "You are a scoring assistant. Output JSON only."
        api_prompt = prompt + "\n\nIMPORTANT: Return a JSON object with exactly two keys:\n1. 'analysis': brief explanation of which criteria are met\n2. 'score': integer 1-7\n\nExample: {\"analysis\": \"The evidence covers...\", \"score\": 5}"
        
        logger.info("=== [Dynamic Scoring] Input Prompt ===")
        logger.info(api_prompt)
        logger.info("======================================")
        
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
                top_p=0.9,
                max_tokens=8096,  # 给评分分析留足空间
                response_format={"type": "json_object"},  # 强制 JSON 输出
                extra_body={"chat_template_kwargs": {"enable_thinking": True}},  # vLLM Qwen3 启用思考模式
            )
            
            # 统计 Token 使用
            add_litellm_usage(response)
            
            # 提取内容和思考内容
            content, reasoning_content = self._extract_content_from_response(response)
            logger.debug(f"动态评分 JSON 输出: {content[:200]}...")

            # 记录思考内容 (如果存在)
            if reasoning_content:
                logger.info("=== [Dynamic Scoring] Reasoning Content ===")
                logger.info(reasoning_content[:500] + "..." if len(reasoning_content) > 500 else reasoning_content)
                logger.info("============================================")

            logger.info("=== [Dynamic Scoring] Output Content ===")
            logger.info(content)
            logger.info("========================================")
            
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
            shots=self.skill_config.get("shots", []),
            scoring_table=scoring_table,
            evidence_cache=self.evidence_cache,
            evidence_chunk_map=self.evidence_chunk_map,  # 使用精确映射
            final_score=self.current_score,
        )
        
        logger.info("=== [Final Report] Input Prompt ===")
        logger.info(prompt)
        logger.info("===================================")
        
        try:
            # 使用 Pydantic Schema 强制输出结构
            system_instruction = "You are an evaluation report generator. Output valid JSON matching the required schema."
            
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                api_base=self.llm_base_url,
                api_key=self.llm_api_key,
                temperature=0.1,
                top_p=0.9,
                max_tokens=16384,
                response_format=FinalReportOutput,  # 使用 Pydantic Schema 强制验证
                extra_body={"chat_template_kwargs": {"enable_thinking": True}},  # vLLM Qwen3 启用思考模式
            )
            
            # 统计 Token 使用
            add_litellm_usage(response)
            
            # 提取内容和思考内容
            content, reasoning_content = self._extract_content_from_response(response)
            logger.debug(f"LLM 原始输出: {content[:500]}...")

            # 记录思考内容 (如果存在)
            if reasoning_content:
                logger.info("=== [Final Report] Reasoning Content ===")
                logger.info(reasoning_content[:500] + "..." if len(reasoning_content) > 500 else reasoning_content)
                logger.info("=========================================")

            logger.info("=== [Final Report] Output Content ===")
            logger.info(content)
            logger.info("=====================================")
            
            # 解析 JSON 并验证结构
            import re
            # 移除可能的 markdown 代码块
            if "```json" in content:
                content = content.split("```json")[1]
                if "```" in content:
                    content = content.split("```")[0]
            content = content.strip()
            
            # 尝试提取 JSON 对象
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                content = json_match.group(0)
            
            cot = json.loads(content)
            
            # 使用 Pydantic 验证结构，确保字段完整
            try:
                validated = FinalReportOutput.model_validate(cot)
                step3_reasoning = validated.step3_reasoning.model_dump()
                step1_locate = validated.step1_locate
            except Exception as validation_error:
                logger.warning(f"Schema 验证失败，使用原始数据: {validation_error}")
                step3_reasoning = cot.get("step3_reasoning", "")
                step1_locate = cot.get("step1_locate", f"证据来自以下章节: {', '.join(self.evidence_sources)}")
            
            return {
                "status": "success",
                "skill_id": self.skill_id,
                "domain": self.domain_id,
                "title": self.skill_config.get("title", ""),
                "chain_of_thought": {
                    "step1_locate": step1_locate,  # 使用已验证的变量
                    "step2_evidence": self.evidence_cache,
                    "step3_reasoning": step3_reasoning,  # 已验证的结构化 dict
                    "score": self.current_score,
                },
                "evidence_sources": self.evidence_sources,
                "iteration_stats": {
                    "total_chunks": len(self.evidence_sources) if self.evidence_sources else 0,
                    "evidence_count": len(self.evidence_cache),
                    "early_stopped": self.current_score == 7,
                },
            }
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}, 使用简化报告")
            return self._simple_report()
            
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
