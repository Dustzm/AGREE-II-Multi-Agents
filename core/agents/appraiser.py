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
from core.agents.base.model_config import get_provider_kwargs

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
from core.utils.pdf_vision import get_stitched_pdf_pages_as_base64

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
        from core.agents.base.model_config import get_provider_config_for
        settings = get_settings()
        provider_cfg = get_provider_config_for(settings.llm.provider)
        prefix = provider_cfg["prefix"]
        self.model = f"{prefix}/{settings.llm.model_name}"
        # 优先使用用户配置的 base_url，其次使用 providers.json 中的 default_base_url
        self.llm_base_url = settings.llm.base_url or provider_cfg.get("default_base_url", "")
        self.llm_api_key = settings.llm.api_key or "dummy-key"
        
        logger.info(f"IterativeEvaluator 初始化: task={task_id}, skill={skill_id}, domain={self.domain_id}")
    
    def _find_source_pdf(self) -> str:
        """
        根据 task_id 在 input 目录下递归查找原始 PDF 文件
        
        task_id 格式形如 '1-2023-015E-03021630'，其中前缀 '1-2023-015E' 即为 PDF 文件名。
        
        Returns:
            PDF 文件的绝对路径，找不到时返回空字符串
        """
        settings = get_settings()
        input_dir = Path(settings.app.dir_input)
        
        # 从 task_id 提取 PDF 文件名前缀：去掉最后的时间戳后缀
        # 例如 '1-2023-015E-03021630' -> '1-2023-015E'
        parts = self.task_id.split('-')
        # task_id 末尾是一个纯数字时间戳(如03021630)，去掉它
        if parts and parts[-1].isdigit() and len(parts[-1]) >= 8:
            pdf_stem = '-'.join(parts[:-1])
        else:
            pdf_stem = self.task_id
        
        pdf_filename = f"{pdf_stem}.pdf"
        
        # 递归搜索 input 目录
        for pdf_file in input_dir.rglob(pdf_filename):
            logger.info(f"找到源 PDF: {pdf_file}")
            return str(pdf_file)
        
        logger.warning(f"未找到源 PDF: {pdf_filename} (搜索目录: {input_dir})")
        return ""
    
    def _log_llm_call(
        self,
        stage: str,
        response,
        content: str,
        reasoning_content: str | None,
    ) -> None:
        """
        统一记录 LLM 调用的完整日志
        
        Args:
            stage: 调用阶段名称 (如 "Evidence Extraction", "Dynamic Scoring")
            response: LiteLLM 响应对象
            content: 提取的最终输出内容
            reasoning_content: 思考内容 (可能为 None)
        """
        # 调用元信息
        usage = getattr(response, 'usage', None)
        model_used = getattr(response, 'model', self.model)
        
        logger.info(f"=== [{stage}] LLM Call Meta ===")
        logger.info(f"  Model: {model_used}")
        logger.info(f"  Skill: {self.skill_id}")
        if usage:
            prompt_tokens = getattr(usage, 'prompt_tokens', 'N/A')
            completion_tokens = getattr(usage, 'completion_tokens', 'N/A')
            total_tokens = getattr(usage, 'total_tokens', 'N/A')
            logger.info(f"  Tokens: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")
        
        # 完整思考内容（不截断）
        if reasoning_content:
            logger.info(f"=== [{stage}] Reasoning Content (full) ===")
            logger.info(reasoning_content)
            logger.info(f"=== [{stage}] End Reasoning Content ===")
        
        # 最终输出内容
        logger.info(f"=== [{stage}] Output Content ===")
        logger.info(content)
        logger.info(f"=== [{stage}] End Output Content ===")
    
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
            chunk_text = f"[{c.chunk_id}] ## {c.title}\n{c.content}"
            
            # 条目17：注入格式元数据供 LLM 评价推荐意见呈现方式
            if self.skill_id == "item_17" and getattr(c, "format_hints", None):
                format_desc = c.format_hints.get("format_description", "")
                if format_desc:
                    chunk_text += f"\n\n{format_desc}"
            
            combined_content.append(chunk_text)
        
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
                temperature=0,    # 非 thinking 模式，使用贪婪解码确保确定性
                top_p=0.1,
                seed=42,          # 固定随机种子
                max_tokens=8192,
                response_format={"type": "json_object"},
                **get_provider_kwargs(),
            )
            
            # 统计 Token 使用
            add_litellm_usage(response)
            
            # 提取内容和思考内容
            content, reasoning_content = self._extract_content_from_response(response)
            
            # 记录完整 LLM 调用日志
            self._log_llm_call(
                stage="Evidence Extraction",
                response=response,
                content=content,
                reasoning_content=reasoning_content,
            )
            
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
        
        # prompt 模板已统一为 JSON 输出格式，与 response_format 一致
        system_instruction = "You are a scoring assistant. Output JSON only."
        api_prompt = prompt
        
        logger.info("=== [Dynamic Scoring] Input Prompt ===")
        logger.info(api_prompt)
        logger.info("======================================")
        
        try:
            messages = [
                {"role": "system", "content": system_instruction}
            ]
            
            # 条目17应用多模态特供处理（仅当模型具备视觉能力时）
            if self.skill_id == "item_17" and get_settings().llm.is_vision_model:
                vision_prompt = self.skill_config.get(
                    f"vision_prompt_{self.lang}", 
                    self.skill_config.get("vision_prompt_en", "Please judge based on the provided images.")
                )
                
                # 从证据池里捞页码
                paths = get_task_paths(self.task_id)
                pool = EvidencePool.load(paths["prep_dir"])
                from collections import Counter
                page_counter = Counter()
                # 提取 evidence_chunk_map 中的 ID
                for cid in self.evidence_chunk_map:
                    chunk = next((c for c in pool.chunks if c.chunk_id == cid), None)
                    if chunk and chunk.start_page is not None:
                        # 用 start_page 作为主要抓图目标密度统计
                        page_counter[chunk.start_page] += 1
                
                if page_counter:
                    # 获取提及频数最高的前 2 页
                    most_common_pages = [page for page, count in page_counter.most_common(2)]
                    target_pages = sorted(most_common_pages)
                    logger.info(f"多模态评分(item_17)：按密度选取最高频的 {len(target_pages)} 页生成长图 {target_pages}")
                    pdf_path = self._find_source_pdf()
                    b64_img = get_stitched_pdf_pages_as_base64(pdf_path, target_pages)
                    
                    full_text_prompt = api_prompt + "\n\n" + vision_prompt
                    logger.info("=== [Dynamic Scoring] Vision Prompt Attached ===")
                    logger.info(vision_prompt)
                    logger.info("================================================")
                    
                    if b64_img:
                        messages.append({
                            "role": "user", 
                            "content": [
                                {"type": "text", "text": full_text_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                            ]
                        })
                        logger.info(">> 已成功挂载 VLM 长拼接单图数据")
                    else:
                        messages.append({"role": "user", "content": api_prompt})
                else:
                    messages.append({"role": "user", "content": api_prompt})
            else:
                messages.append({"role": "user", "content": api_prompt})

            response = litellm.completion(
                model=self.model,
                messages=messages,
                api_base=self.llm_base_url,
                api_key=self.llm_api_key,
                temperature=0,    # 非 thinking 模式，使用贪婪解码确保确定性
                top_p=0.1,
                seed=42,
                max_tokens=8096,
                response_format={"type": "json_object"},
                **get_provider_kwargs(),
            )
            
            # 统计 Token 使用
            add_litellm_usage(response)
            
            # 提取内容和思考内容
            content, reasoning_content = self._extract_content_from_response(response)

            # 记录完整 LLM 调用日志
            self._log_llm_call(
                stage="Dynamic Scoring",
                response=response,
                content=content,
                reasoning_content=reasoning_content,
            )
            
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
        
        max_retries = 3
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                # 使用 Pydantic Schema 强制输出结构
                system_instruction = "You are an evaluation report generator. Output valid JSON matching the required schema."
                
                messages = [
                    {"role": "system", "content": system_instruction}
                ]
                
                # 条目17同样在综合报告阶段注入图片（仅当模型具备视觉能力时）
                if self.skill_id == "item_17" and get_settings().llm.is_vision_model:
                    vision_prompt = self.skill_config.get(
                        f"vision_prompt_{self.lang}", 
                        self.skill_config.get("vision_prompt_en", "")
                    )
                    paths = get_task_paths(self.task_id)
                    pool = EvidencePool.load(paths["prep_dir"])
                    from collections import Counter
                    page_counter = Counter()
                    for cid in self.evidence_chunk_map:
                        chunk = next((c for c in pool.chunks if c.chunk_id == cid), None)
                        if chunk and chunk.start_page is not None:
                            page_counter[chunk.start_page] += 1
                    
                    if page_counter:
                        most_common_pages = [page for page, count in page_counter.most_common(2)]
                        target_pages = sorted(most_common_pages)
                        logger.info(f"多模态报告(item_17) attempt {attempt}：按密度选取最高频的 {len(target_pages)} 页成图 {target_pages}")
                        pdf_path = self._find_source_pdf()
                        # 使用 pdf_vision.py 中用户默认设置的高 DPI
                        b64_img = get_stitched_pdf_pages_as_base64(pdf_path, target_pages)
                        
                        full_text_prompt = prompt + "\n\n" + vision_prompt
                        
                        if b64_img:
                            messages.append({
                                "role": "user", 
                                "content": [
                                    {"type": "text", "text": full_text_prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                                ]
                            })
                        else:
                            messages.append({"role": "user", "content": prompt})
                    else:
                        messages.append({"role": "user", "content": prompt})
                else:
                    messages.append({"role": "user", "content": prompt})
                
                response = litellm.completion(
                    model=self.model,
                    messages=messages,
                    api_base=self.llm_base_url,
                    api_key=self.llm_api_key,
                    temperature=0,    # 非 thinking 模式，使用贪婪解码确保确定性
                    top_p=0.1,
                    seed=42,
                    max_tokens=16384,
                    response_format={"type": "json_object"},  # 使用简单 json_object 避免 SGLang xgrammar 兼容性问题
                    **get_provider_kwargs(),
                )
                
                # 统计 Token 使用
                add_litellm_usage(response)
                
                # 提取内容和思考内容
                content, reasoning_content = self._extract_content_from_response(response)

                # 记录完整 LLM 调用日志
                self._log_llm_call(
                    stage=f"Final Report (attempt {attempt}/{max_retries})",
                    response=response,
                    content=content,
                    reasoning_content=reasoning_content,
                )
                
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
                        "step1_locate": step1_locate,
                        "step2_evidence": self.evidence_cache,
                        "step3_reasoning": step3_reasoning,
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
                last_error = e
                logger.warning(f"最终报告 JSON 解析失败 (第 {attempt}/{max_retries} 次): {e}")
                
            except Exception as e:
                last_error = e
                logger.warning(f"最终报告生成失败 (第 {attempt}/{max_retries} 次): {e}")
        
        # 所有重试均失败，使用简化报告
        logger.error(f"最终报告生成在 {max_retries} 次尝试后均失败，回退到简化报告。最后错误: {last_error}")
        return self._simple_report()
    
    def _simple_report(self) -> Dict[str, Any]:
        """生成简化报告（LLM 调用失败时的回退）"""
        # 安全检查：如果有证据但评分为最低分，说明评分可能不准确
        if self.evidence_cache and self.current_score == 1:
            logger.warning(
                f"⚠️ 简化报告异常检测: 已提取 {len(self.evidence_cache)} 条证据，"
                f"但当前评分为 1（最低分），评分可能因 LLM 调用失败而不准确。"
                f"证据摘要: {[e[:80] + '...' for e in self.evidence_cache[:3]]}"
            )
        
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
        
        reasoning = f"基于 {len(self.evidence_cache)} 条证据进行评价（注意：最终报告 LLM 调用失败，此为简化回退报告）。"
        if matched_condition:
            reasoning += f" 根据评分标准，符合【{matched_condition}】条件，评分为 {self.current_score}。"
        else:
            reasoning += f" 评分为 {self.current_score}。"
        
        # 如果有证据但评分异常低，在报告中明确标注
        if self.evidence_cache and self.current_score == 1:
            reasoning += f" ⚠️ 警告：已提取到 {len(self.evidence_cache)} 条证据但评分为最低分，建议人工复核。"
        
        return {
            "status": "fallback",  # 标记为回退报告，区别于正常 success
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
