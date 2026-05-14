"""
智能体运行服务

封装 Web 单任务评价流程，支持 WebSocket 回调
"""
import asyncio
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from core.preprocessor import process_content_list
from core.agents.tools.appraiser_batch import clear_progress_callbacks
from core.utils.unicode_sanitizer import strip_surrogates
from web.backend.app.i18n import t, get_language

logger = logging.getLogger(__name__)


class AppraisalRunner:
    """
    评价任务运行器
    
    为 Web 单任务入口封装显式两阶段流程：
    1. 批量条目评价
    2. 最终报告生成
    """

    _REPORTER_MAX_RETRIES = 3
    _REPORTER_RETRY_BASE_DELAY = 1.0
    _REPORTER_RETRYABLE_KEYWORDS = (
        "connection error",
        "api connection",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "network",
    )
    _WORKFLOW_LANG = "en"
    
    def __init__(
        self,
        on_status: Optional[Callable[[str, str], None]] = None,
        on_agent: Optional[Callable[[str, str, int, int, Optional[str]], None]] = None,
        on_complete: Optional[Callable[[str], None]] = None,
        on_cancelled: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        lang: Optional[str] = None,
        output_subdir: Optional[str] = None,
    ):
        """
        Args:
            on_status: 状态回调 (type, content)
            on_agent: 智能体回调 (agent, content, progress, total, prompt)
            on_complete: 完成回调 (task_id)
            on_cancelled: 取消回调 (task_id)
            on_error: 错误回调 (error_msg)
            lang: 语言代码 (zh/en)，默认英语
            output_subdir: 输出子目录 (如 "batch_1", "batch_2")
        """
        self.on_status = on_status or (lambda *_: None)
        self.on_agent = on_agent or (lambda *_: None)
        self.on_complete = on_complete or (lambda *_: None)
        self.on_cancelled = on_cancelled or (lambda *_: None)
        self.on_error = on_error or (lambda *_: None)
        self.lang = get_language(lang)
        self.workflow_lang = self._WORKFLOW_LANG
        self.output_subdir = output_subdir
        
        self._cancelled = False
        self._cancel_notified = False
        
        # 用于 WebSocket 发送的回调（在 run 中设置）
        self._on_item_progress = None
        self._on_batch_summary = None
        
        # Token 使用统计
        self._token_tracker = None
        self._weaver_dir = None  # 保存路径
    
    async def _handle_item_progress(self, skill_id: str, title: str, status: str, duration: float = None):
        """单条目进度回调"""
        try:
            # 优先使用 WebSocket 专用回调（仅用于 Chat 单任务场景）
            if hasattr(self, '_send_item_progress') and self._send_item_progress:
                await self._send_item_progress(skill_id, title, status, duration)
            # 批量场景不更新单条目进度，由 on_batch_summary 统一更新
        except Exception as e:
            logger.warning(f"发送条目进度失败: {e}")

    async def _handle_batch_summary(self, total: int, completed: int, elapsed: float):
        """汇总进度回调"""
        try:
            logger.info(f"on_batch_summary 被调用: {completed}/{total}")
            if hasattr(self, '_send_batch_summary') and self._send_batch_summary:
                await self._send_batch_summary(total, completed, elapsed)
            else:
                # 回退使用 on_agent 回调（用于批量任务场景）
                msg = t("agent.batch_progress", self.lang, completed=completed, total=total)
                logger.info(f"调用 on_agent: Appraiser, {msg}")
                self.on_agent("Appraiser", msg, 1, 3, None)
        except Exception as e:
            logger.warning(f"发送汇总进度失败: {e}")
    
    def cancel(self):
        """取消当前任务"""
        self._cancelled = True

    def _notify_cancelled(self, task_id: str):
        """确保取消终态只通知一次。"""
        if self._cancelled and not self._cancel_notified:
            self._cancel_notified = True
            self.on_cancelled(task_id)
    
    def get_token_usage(self) -> dict:
        """获取当前任务的 token 使用统计"""
        if self._token_tracker:
            return self._token_tracker.to_dict()
        return {}

    def _report_paths(self, task_id: str) -> tuple[Optional[Path], Optional[Path]]:
        """获取报告文件路径。"""
        from core.agents.tools.public_tools import get_task_paths

        paths = get_task_paths(task_id)
        if not paths:
            return None, None

        task_dir = Path(paths["task_dir"])
        return task_dir / "appraisal_report.json", task_dir / "appraisal_report.md"

    def _report_exists(self, task_id: str) -> bool:
        """报告 JSON 与 Markdown 是否都已生成。"""
        json_path, md_path = self._report_paths(task_id)
        return bool(json_path and md_path and json_path.exists() and md_path.exists())

    def _walk_exception_chain(self, error: Exception) -> list[BaseException]:
        """展开异常链，便于识别被包装后的连接异常。"""
        chain: list[BaseException] = []
        current: BaseException | None = error
        while current and current not in chain:
            chain.append(current)
            current = current.__cause__ or current.__context__
        return chain

    def _is_retryable_reporter_error(self, error: Exception) -> bool:
        """判断 Reporter 阶段错误是否适合重试。"""
        retryable_types: tuple[type[BaseException], ...] = ()

        try:
            import openai

            retryable_types += tuple(
                exc
                for exc in (
                    getattr(openai, "APIConnectionError", None),
                    getattr(openai, "APITimeoutError", None),
                )
                if exc is not None
            )
        except Exception:
            pass

        try:
            import litellm

            retryable_types += tuple(
                exc
                for exc in (
                    getattr(litellm, "InternalServerError", None),
                    getattr(litellm, "ServiceUnavailableError", None),
                    getattr(litellm, "Timeout", None),
                    getattr(litellm, "APIConnectionError", None),
                )
                if exc is not None
            )
        except Exception:
            pass

        for candidate in self._walk_exception_chain(error):
            if retryable_types and isinstance(candidate, retryable_types):
                return True

            normalized = str(candidate).lower()
            if any(keyword in normalized for keyword in self._REPORTER_RETRYABLE_KEYWORDS):
                return True

        return False

    async def _sleep_with_cancel(self, delay: float) -> bool:
        """带取消检查的等待。"""
        remaining = max(delay, 0.0)
        while remaining > 0:
            if self._cancelled:
                return False
            interval = min(0.1, remaining)
            await asyncio.sleep(interval)
            remaining -= interval
        return not self._cancelled

    async def _run_batch_appraisal(self, task_id: str):
        """直接执行批量条目评价阶段。"""
        from core.agents.tools.appraiser_batch import appraise_all

        logger.info(f"[{task_id}] 开始批量条目评价阶段")
        self.on_agent("Appraiser", t("agent.batch_appraise_start", self.lang), 1, 3, None)

        result = await appraise_all(
            task_id,
            lang=self.workflow_lang,
            on_item_progress=self._handle_item_progress,
            on_summary=self._handle_batch_summary,
        )

        if self._cancelled:
            logger.info(f"[{task_id}] 批量条目评价结束后检测到取消")
            return

        status = result.get("status", "error")
        failed = result.get("failed", {})
        if status == "error":
            raise RuntimeError(f"批量条目评价失败: {failed or 'Unknown error'}")
        if status == "partial":
            logger.warning(f"[{task_id}] 批量条目评价部分失败，将继续进入报告阶段: {failed}")

        self.on_agent("Appraiser", t("agent.batch_appraise_done", self.lang), 1, 3, None)
        self.on_status("status", t("status.entering_report_phase", self.lang))
        logger.info(f"[{task_id}] 批量评价完成，进入报告阶段")

    async def _run_reporter_once(self, task_id: str):
        """执行一次 Reporter 阶段。"""
        from core.agents import create_reporter_agent
        from google.adk import runners
        from google.genai import types

        reporter = create_reporter_agent(self.workflow_lang)
        runner = runners.InMemoryRunner(
            agent=reporter,
            app_name='ama_web_reporter',
        )

        session = await runner.session_service.create_session(
            app_name='ama_web_reporter',
            user_id='web_user',
        )

        prompt = self._build_reporter_prompt(task_id)

        initial_message = types.Content(
            role='user',
            parts=[types.Part(text=strip_surrogates(prompt))],
        )

        report_tool_called = False

        async for event in runner.run_async(
            user_id='web_user',
            session_id=session.id,
            new_message=initial_message,
        ):
            if hasattr(event, 'usage_metadata') and event.usage_metadata and self._token_tracker:
                self._token_tracker.add_usage(event.usage_metadata)

            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        tool_name = part.function_call.name or ""
                        logger.info(f"[{task_id}] Reporter 工具调用: {tool_name}")
                        if 'generate_appraisal_report' in tool_name.lower():
                            report_tool_called = True
                    elif hasattr(part, 'function_response') and part.function_response:
                        response = part.function_response
                        tool_name = response.name if hasattr(response, 'name') else ""
                        logger.info(f"[{task_id}] Reporter 工具完成: {tool_name}")
                        if 'generate_appraisal_report' in tool_name.lower():
                            report_tool_called = True
                    elif hasattr(part, 'text') and part.text:
                        text = part.text.strip()
                        if text:
                            logger.info(f"[{task_id}] Reporter 输出: {text[:300]}")

            if self._cancelled:
                logger.info(f"[{task_id}] Reporter 当前尝试结束后检测到取消")
                break

        if self._cancelled:
            return

        if not report_tool_called and not self._report_exists(task_id):
            raise RuntimeError("Reporter 执行完成，但未调用 generate_appraisal_report 或未生成报告文件")

    async def _run_reporter_with_retries(self, task_id: str):
        """执行 Reporter 阶段，并对瞬时连接错误做重试。"""
        self.on_agent("Reporter", t("agent.reporter_working", self.lang), 2, 3, None)

        last_error: Exception | None = None
        for attempt in range(1, self._REPORTER_MAX_RETRIES + 1):
            if self._cancelled:
                logger.info(f"[{task_id}] Reporter 启动前检测到取消")
                return

            logger.info(
                f"[{task_id}] 开始 Reporter 尝试 attempt={attempt}/{self._REPORTER_MAX_RETRIES}"
            )
            try:
                await self._run_reporter_once(task_id)

                if self._cancelled:
                    logger.info(f"[{task_id}] Reporter 尝试完成后检测到取消")
                    return

                if not self._report_exists(task_id):
                    raise RuntimeError("Reporter 执行成功，但缺少 appraisal_report.json 或 appraisal_report.md")

                logger.info(f"[{task_id}] Reporter 阶段完成")
                return
            except Exception as error:
                if self._report_exists(task_id):
                    logger.warning(f"[{task_id}] Reporter 抛出异常但报告文件已生成，视为成功: {error}")
                    return

                last_error = error
                retryable = self._is_retryable_reporter_error(error)
                logger.warning(
                    f"[{task_id}] Reporter 失败 attempt={attempt}/{self._REPORTER_MAX_RETRIES} "
                    f"retryable={retryable} error={type(error).__name__}: {error}"
                )

                if not retryable or attempt >= self._REPORTER_MAX_RETRIES:
                    raise

                self.on_status(
                    "status",
                    t(
                        "status.report_retry",
                        self.lang,
                        attempt=attempt,
                        max_retries=self._REPORTER_MAX_RETRIES,
                    ),
                )
                delay = self._REPORTER_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                if not await self._sleep_with_cancel(delay):
                    logger.info(f"[{task_id}] Reporter 重试等待期间任务被取消")
                    return

        if last_error is not None:
            raise last_error
    
    async def run(self, task_id: str, pdf_path: str, reuse_task_id: Optional[str] = None):
        """
        运行评价任务
        
        参照 CLI 的处理流程:
        1. 初始化任务目录 (init_task_directory)
        2. 解析 PDF (MinerUParser.parse_pdf_to_file)
        3. 运行 Orchestrator
        
        Args:
            task_id: 任务 ID
            pdf_path: PDF 文件路径
            reuse_task_id: 可选，复用已有的 task_id（多轮实验时使用）
        """
        actual_task_id = task_id

        try:
            self._cancelled = False
            self._cancel_notified = False
            
            # 导入依赖（延迟导入避免循环依赖）
            from core.agents import init_task_directory
            from core.agents.tools.public_tools import get_task_paths
            from core.parsers import MinerUParser
            from core.config import get_settings
            import shutil
            
            pdf_file = Path(pdf_path)
            if not pdf_file.exists():
                self.on_error(t("error.pdf_not_found", self.lang, path=pdf_path))
                return
            
            # Step 1: 初始化任务目录
            self.on_status("status", t("status.init_task_dir", self.lang))
            
            # 复用预处理文件的情况（多轮实验）
            if reuse_task_id:
                # 获取第一轮的路径信息
                first_round_paths = get_task_paths(reuse_task_id)
                if first_round_paths:
                    first_prep_dir = Path(first_round_paths["prep_dir"])
                    
                    # 在当前轮次的 output_subdir 下创建新目录
                    task_result = init_task_directory(
                        pdf_file.name,
                        output_subdir=self.output_subdir,
                    )
                    
                    if task_result.get("status") == "error":
                        self.on_error(t("error.init_failed", self.lang, error=task_result.get('error', 'Unknown')))
                        return
                    
                    actual_task_id = task_result["task_id"]
                    source_path = task_result["paths"]["source"]
                    prep_dir = task_result["paths"]["prep_dir"]
                    
                    # 复制第一轮的预处理文件到新目录
                    new_prep_dir = Path(prep_dir)
                    try:
                        # 复制 source.md
                        first_source = Path(first_round_paths["source"])
                        if first_source.exists():
                            shutil.copy2(first_source, source_path)
                            logger.info(f"复制 source.md: {first_source} -> {source_path}")
                        
                        # 复制 prep 目录下的所有预处理文件
                        for item in first_prep_dir.iterdir():
                            if item.is_file():
                                dest = new_prep_dir / item.name
                                if not dest.exists():
                                    shutil.copy2(item, dest)
                                    logger.info(f"复制预处理文件: {item.name}")
                            elif item.is_dir():
                                # 复制子目录（包括 images）
                                dest = new_prep_dir / item.name
                                if not dest.exists():
                                    shutil.copytree(item, dest)
                                    logger.info(f"复制预处理目录: {item.name}")
                        
                        self.on_status("status", t("status.reusing_task", self.lang, task_id=actual_task_id))
                        logger.info(f"多轮实验: 已复制预处理文件到 {new_prep_dir}")
                    except Exception as e:
                        logger.warning(f"复制预处理文件失败: {e}，将重新生成")
                        # 复制失败时继续正常流程，会重新生成预处理文件
                else:
                    logger.warning(f"找不到第一轮目录: {reuse_task_id}，将创建新任务")
                    reuse_task_id = None  # 回退到正常流程
            
            if not reuse_task_id:
                # 使用文件名初始化（和 CLI 一致）
                task_result = init_task_directory(
                    pdf_file.name,
                    output_subdir=self.output_subdir,
                )
                
                if task_result.get("status") == "error":
                    self.on_error(t("error.init_failed", self.lang, error=task_result.get('error', 'Unknown')))
                    return
                
                actual_task_id = task_result["task_id"]
                source_path = task_result["paths"]["source"]
                prep_dir = task_result["paths"]["prep_dir"]

            
            self.on_status("status", t("status.task_id", self.lang, task_id=actual_task_id))
            
            if self._cancelled:
                self._notify_cancelled(actual_task_id)
                return

            
            # Step 2: 检查并解析 PDF
            source_file = Path(source_path)
            parse_success = False

            if source_file.exists():
                self.on_status("status", t("status.doc_found_skip", self.lang))
            else:
                # 使用 MinerU 解析，支持重试
                parse_success = False
                settings = get_settings()
                mineru_url = settings.mineru.server_url
                max_retries = settings.mineru.parse_retry_attempts
                retry_delay = settings.mineru.parse_retry_delay

                if not mineru_url:
                    if self._cancelled:
                        self._notify_cancelled(actual_task_id)
                        return

                    self.on_error(t("error.mineru_not_configured", self.lang))
                    return

                self.on_status("status", t("status.parsing_mineru", self.lang))
                parser = MinerUParser()

                # 先检查服务是否可用
                service_ok = await parser.health_check()
                if not service_ok:
                    if self._cancelled:
                        self._notify_cancelled(actual_task_id)
                        return

                    self.on_error(t("error.mineru_unavailable", self.lang))
                    return

                # 带重试的解析逻辑
                last_error = None
                for attempt in range(max_retries):
                    if self._cancelled:
                        self._notify_cancelled(actual_task_id)
                        return

                    try:
                        parse_result = await parser.parse_pdf_to_file(
                            pdf_path=str(pdf_file),
                            source_path=source_path,
                            prep_dir=prep_dir
                        )

                        if parse_result.get("status") == "success":
                            pages = parse_result.get("pages", 0)
                            msg = t("status.parse_done_mineru", self.lang, pages=pages)
                            logger.info(f"发送解析完成消息: {msg}")
                            self.on_status("status", msg)
                            parse_success = True
                            break
                        else:
                            last_error = parse_result.get('error', 'Unknown error')
                            logger.warning(f"MinerU 解析失败 (尝试 {attempt + 1}/{max_retries}): {last_error}")

                    except Exception as e:
                        last_error = str(e)
                        logger.warning(f"MinerU 解析异常 (尝试 {attempt + 1}/{max_retries}): {e}")

                    # 如果不是最后一次尝试，等待后重试
                    if attempt < max_retries - 1:
                        if self._cancelled:
                            self._notify_cancelled(actual_task_id)
                            return
                        self.on_status("status", t("status.parse_retry", self.lang, delay=retry_delay, attempt=attempt + 1, max_retries=max_retries))
                        await asyncio.sleep(retry_delay)
                        if self._cancelled:
                            self._notify_cancelled(actual_task_id)
                            return

                # 所有重试都失败
                if not parse_success:
                    self.on_error(t("error.parse_failed", self.lang, error=last_error))
                    return
            
            if self._cancelled:
                self._notify_cancelled(actual_task_id)
                return
            
            # Step 2.5: 语义证据池构建
            if parse_success or source_file.exists():
                pdf_stem = pdf_file.stem  # 不带 .pdf 后缀
                content_list_path = Path(prep_dir) / f"{pdf_stem}_content_list.json"
                evidence_pool_path = Path(prep_dir) / f"{pdf_stem}_evidence_pool.json"
                
                # 如果是复用模式且证据池已存在，跳过预处理（直接使用第一轮的证据池）
                if self._cancelled:
                    self._notify_cancelled(actual_task_id)
                    return

                if reuse_task_id and evidence_pool_path.exists():
                    logger.info(f"复用第一轮证据池，跳过预处理: {evidence_pool_path}")
                    self.on_status("status", t("status.reusing_evidence", self.lang))
                else:
                    self.on_status("status", t("status.building_evidence", self.lang))
                    
                    # 检查 content_list.json 是否存在
                    if content_list_path.exists():
                        try:
                            preprocess_result = await process_content_list(
                                content_list_path=str(content_list_path),
                                output_path=str(evidence_pool_path),
                                pdf_path=str(pdf_file),  # 添加 PDF 路径以构建 docindex
                                lang=self.workflow_lang,
                                progress_callback=lambda message: self.on_status("status", message),
                            )
                            
                            if preprocess_result["status"] == "success":
                                self.on_status("status", t("status.evidence_done", self.lang))
                            else:
                                logger.warning(f"预处理失败: {preprocess_result.get('error')}")
                                self.on_status("status", t("status.evidence_failed", self.lang))
                            if self._cancelled:
                                self._notify_cancelled(actual_task_id)
                                return
                        except Exception as e:
                            logger.error(f"预处理过程异常: {e}")
                            self.on_status("status", t("status.preprocess_error", self.lang))
                            if self._cancelled:
                                self._notify_cancelled(actual_task_id)
                                return
                    else:
                        logger.warning(f"未找到 content_list.json: {content_list_path}")
            
            # Step 3: 显式执行两阶段流程（批量评价 -> 报告生成）
            self.on_status("status", t("status.starting_agents", self.lang))
            self.on_agent("Orchestrator", t("agent.orchestrator_start", self.lang), 0, 3)

            from core.utils.token_tracker import TokenUsageTracker, set_global_tracker
            self._token_tracker = TokenUsageTracker(task_id=actual_task_id)
            set_global_tracker(self._token_tracker)
            self._weaver_dir = Path(task_result["paths"]["weaver_dir"]) if not reuse_task_id else Path(prep_dir).parent

            await self._run_batch_appraisal(actual_task_id)

            if self._cancelled:
                logger.info(f"[{actual_task_id}] 批量评价阶段结束后任务已取消")
                self._notify_cancelled(actual_task_id)
                return

            await self._run_reporter_with_retries(actual_task_id)

            if self._cancelled:
                self._notify_cancelled(actual_task_id)
                return

            self.on_agent("Reporter", t("agent.report_done", self.lang), 3, 3)
            
            # 保存 Token 统计到 .ama/token_usage.json
            if self._token_tracker and self._weaver_dir:
                token_usage_path = self._weaver_dir / "token_usage.json"
                self._token_tracker.save(token_usage_path)
            
            self.on_complete(actual_task_id)
        
        except Exception as e:
            logger.exception(f"评价任务失败: {e}")
            if self._cancelled:
                self._notify_cancelled(actual_task_id)
            else:
                self.on_error(str(e))
        
        finally:
            # 清理全局进度回调
            clear_progress_callbacks()
            # 清除全局 Token 追踪器
            from core.utils.token_tracker import set_global_tracker
            set_global_tracker(None)

    def _build_reporter_prompt(self, task_id: str) -> str:
        """构造固定英文的 Reporter 用户提示，避免被界面语言带偏。"""
        return f"""Please start the AGREE-II final report generation task.

Task information:
- task_id: {task_id}

Follow the Reporter workflow strictly:
1. Call get_guideline_title(task_id)
2. Call load_all_evaluations(task_id)
3. Analyze the results and call generate_appraisal_report(...) to save the report
4. Return a concise report summary
"""
