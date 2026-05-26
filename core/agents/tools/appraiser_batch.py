"""
并发批量评价工具

使用 asyncio.Semaphore 控制单指南条目并发，从 .env 读取 APPRAISAL_MAX_CONCURRENCY。
支持显式注入线程池控制整体评价并发，避免跨 worker 共享全局线程池。
支持进度回调，用于前端实时展示
"""
import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Callable, Awaitable

from concurrent.futures import ThreadPoolExecutor

from core.config import get_settings
from core.agents.appraiser import run_iterative_evaluation
from core.agents.tools.appraiser_tools import save_item_evaluation
from core.skills import list_available_skills, get_skill_summary
from core.utils.cancellation import CancellationRequested, CancellationToken

logger = logging.getLogger(__name__)

ITEM_LLM_MAX_RETRIES = 3
ITEM_LLM_MAX_ATTEMPTS = ITEM_LLM_MAX_RETRIES + 1

def create_llm_executor(pool_concurrency: int) -> ThreadPoolExecutor:
    """创建批量条目评价线程池。"""
    logger.info("创建 LLM 评价线程池: pool_concurrency=%s", pool_concurrency)
    return ThreadPoolExecutor(max_workers=pool_concurrency, thread_name_prefix="llm_eval_")


def shutdown_llm_executor(executor: ThreadPoolExecutor | None) -> None:
    """关闭批量条目评价线程池。"""
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


# ============================================================================
# 全局进度回调注册（用于外部注入，如 agent_runner）
# ============================================================================
_global_progress_callback: Optional[Callable[[str, str, str, Optional[float]], Awaitable[None]]] = None
_global_summary_callback: Optional[Callable[[int, int, float], Awaitable[None]]] = None


def register_progress_callbacks(
    on_item_progress: Optional[Callable[[str, str, str, Optional[float]], Awaitable[None]]] = None,
    on_summary: Optional[Callable[[int, int, float], Awaitable[None]]] = None
):
    """
    注册全局进度回调

    Args:
        on_item_progress: 单条目进度回调 (skill_id, title, status, duration)
        on_summary: 汇总进度回调 (total, completed, elapsed)
    """
    global _global_progress_callback, _global_summary_callback
    _global_progress_callback = on_item_progress
    _global_summary_callback = on_summary


def clear_progress_callbacks():
    """清除全局进度回调"""
    global _global_progress_callback, _global_summary_callback
    _global_progress_callback = None
    _global_summary_callback = None


async def appraise_all(
    task_id: str,
    skill_ids: Optional[List[str]] = None,
    lang: str = "en",
    on_item_progress: Optional[Callable[[str, str, str, Optional[float]], Awaitable[None]]] = None,
    on_summary: Optional[Callable[[int, int, float], Awaitable[None]]] = None,
    cancellation_token: Optional[CancellationToken] = None,
    executor: Optional[ThreadPoolExecutor] = None,
) -> Dict[str, Any]:
    """
    并发评价所有/指定条目

    Args:
        task_id: 任务 ID
        skill_ids: 指定条目列表，None 表示所有启用的条目
        lang: 语言 (zh/en)
        on_item_progress: 进度回调函数
            - 参数: (skill_id, title, status, duration)
            - status: "started" | "completed" | "failed"
            - duration: 完成耗时（秒），仅 completed 时有值
        on_summary: 汇总进度回调函数
            - 参数: (total, completed, elapsed)

    Returns:
        {
            "status": "success" | "partial" | "error",
            "completed": ["item_1", "item_2", ...],
            "failed": {"item_3": "error message", ...},
            "total": 23,
            "duration": 45.2,
            "item_concurrency": 5,
            "pool_concurrency": 10
        }
    """
    settings = get_settings()
    item_concurrency = settings.app.appraisal_max_concurrency
    pool_concurrency = settings.app.appraisal_pool_max_concurrency
    llm_timeout = settings.app.batch_item_llm_timeout
    owns_executor = executor is None
    executor = executor or create_llm_executor(pool_concurrency)

    # 获取待评价的条目列表
    if skill_ids is None:
        skill_ids = list_available_skills(enabled_only=True)

    total = len(skill_ids)
    logger.info(
        f"开始并发评价: task_id={task_id}, 条目数={total}, "
        f"单指南并发={item_concurrency}, 全局池并发={pool_concurrency}"
    )

    # 使用传入的回调或全局回调
    progress_callback = on_item_progress or _global_progress_callback
    summary_callback = on_summary or _global_summary_callback

    # 推送初始进度 0/total
    if summary_callback:
        try:
            logger.info(f"推送初始进度: 0/{total}")
            await summary_callback(total, 0, 0.0)
            logger.info(f"初始进度推送完成")
        except Exception as e:
            logger.warning(f"推送初始进度失败: {e}")

    # 单个指南任务的条目并发控制
    semaphore = asyncio.Semaphore(item_concurrency)

    # 结果收集
    completed: List[str] = []
    failed: Dict[str, str] = {}
    completed_count = 0
    start_time = time.time()

    async def evaluate_item(skill_id: str) -> tuple[str, Dict[str, Any]]:
        """评价单个条目"""
        nonlocal completed_count
        if cancellation_token:
            cancellation_token.raise_if_cancelled()
        async with semaphore:
            if cancellation_token:
                cancellation_token.raise_if_cancelled()
            item_start = time.time()
            title = get_skill_summary(skill_id, lang=lang) or skill_id

            # 通知开始
            if progress_callback:
                try:
                    await progress_callback(skill_id, title, "started", None)
                except Exception as e:
                    logger.warning(f"进度回调失败: {e}")

            try:
                # 使用全局线程池执行同步评价，统一限制所有指南任务的总并发。
                loop = asyncio.get_running_loop()
                last_timeout_error = None
                for attempt in range(1, ITEM_LLM_MAX_ATTEMPTS + 1):
                    if cancellation_token:
                        cancellation_token.raise_if_cancelled()
                    try:
                        result = await asyncio.wait_for(
                            loop.run_in_executor(
                                executor,
                                run_iterative_evaluation,
                                task_id,
                                skill_id,
                                lang,
                                cancellation_token,
                            ),
                            timeout=llm_timeout,
                        )
                        break
                    except asyncio.TimeoutError as exc:
                        last_timeout_error = exc
                        logger.warning(
                            "条目评价超时: %s - 尝试 %s/%s (%ss)",
                            skill_id,
                            attempt,
                            ITEM_LLM_MAX_ATTEMPTS,
                            llm_timeout,
                        )
                        if attempt < ITEM_LLM_MAX_ATTEMPTS and progress_callback:
                            try:
                                await progress_callback(skill_id, title, "retrying", None)
                            except Exception as ex:
                                logger.warning(f"进度回调失败: {ex}")
                else:
                    raise last_timeout_error or asyncio.TimeoutError()
                duration = time.time() - item_start

                # 保存结果
                status = result.get("status")
                if status == "cancelled":
                    if cancellation_token and cancellation_token.cancelled():
                        raise CancellationRequested(result.get("error", "任务已取消"))
                    error_msg = result.get("error", "条目评价被取消")
                    if progress_callback:
                        try:
                            await progress_callback(skill_id, title, "failed", None)
                        except Exception as e:
                            logger.warning(f"进度回调失败: {e}")
                    logger.error("条目评价返回 cancelled 但批量任务未取消: %s - %s", skill_id, error_msg)
                    return skill_id, {"status": "failed", "error": error_msg, "reason": "item_cancelled"}
                if status in ("success", "no_evidence"):
                    cot = result.get("chain_of_thought", {})
                    save_item_evaluation(
                        task_id=task_id,
                        skill_id=skill_id,
                        step1_locate=cot.get("step1_locate", ""),
                        step2_evidence=cot.get("step2_evidence", []),
                        step3_reasoning=cot.get("step3_reasoning", ""),
                        score=cot.get("score", 1),
                        evidence_sources=result.get("evidence_sources", [])
                    )

                    # 更新完成计数并通知
                    completed_count += 1
                    if progress_callback:
                        try:
                            await progress_callback(skill_id, title, "completed", duration)
                        except Exception as e:
                            logger.warning(f"进度回调失败: {e}")

                    # 发送汇总进度
                    if summary_callback:
                        try:
                            elapsed = time.time() - start_time
                            logger.info(f"推送汇总进度: {completed_count}/{total}")
                            await summary_callback(total, completed_count, elapsed)
                        except Exception as e:
                            logger.warning(f"汇总回调失败: {e}")

                    logger.info(f"评价完成: {skill_id} ({duration:.1f}s)")
                    return skill_id, {"status": "success", "duration": duration}
                else:
                    error_msg = result.get("error", "评价失败")

                    # 通知失败
                    if progress_callback:
                        try:
                            await progress_callback(skill_id, title, "failed", None)
                        except Exception as e:
                            logger.warning(f"进度回调失败: {e}")

                    logger.warning(f"评价失败: {skill_id} - {error_msg}")
                    return skill_id, {"status": "failed", "error": error_msg}

            except CancellationRequested as exc:
                if cancellation_token and cancellation_token.cancelled():
                    raise
                error_msg = str(exc)
                if progress_callback:
                    try:
                        await progress_callback(skill_id, title, "failed", None)
                    except Exception as ex:
                        logger.warning(f"进度回调失败: {ex}")
                logger.exception("条目评价收到取消异常但批量任务未取消: %s - %s", skill_id, error_msg)
                return skill_id, {"status": "failed", "error": error_msg, "reason": "item_cancelled"}
            except asyncio.TimeoutError:
                error_msg = f"条目评价超时 ({llm_timeout}s)"
                if progress_callback:
                    try:
                        await progress_callback(skill_id, title, "failed", None)
                    except Exception as ex:
                        logger.warning(f"进度回调失败: {ex}")
                logger.error(
                    "评价超时: %s - %s，已尝试 %s 次",
                    skill_id,
                    error_msg,
                    ITEM_LLM_MAX_ATTEMPTS,
                )
                return skill_id, {"status": "failed", "error": error_msg, "reason": "llm_timeout"}
            except Exception as e:
                error_msg = str(e)

                # 通知失败
                if progress_callback:
                    try:
                        await progress_callback(skill_id, title, "failed", None)
                    except Exception as ex:
                        logger.warning(f"进度回调失败: {ex}")

                logger.exception("评价异常: %s - %s", skill_id, error_msg)
                return skill_id, {"status": "failed", "error": error_msg}

    # 并发执行所有评价任务
    if cancellation_token:
        cancellation_token.raise_if_cancelled()
    tasks = [evaluate_item(sid) for sid in skill_ids]
    try:
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except CancellationRequested:
            if not (cancellation_token and cancellation_token.cancelled()):
                raise
            logger.info("批量条目评价收到取消请求，停止等待新条目")
            return {
                "status": "cancelled",
                "completed": completed,
                "failed": failed,
                "total": total,
                "duration": time.time() - start_time,
                "item_concurrency": item_concurrency,
                "pool_concurrency": pool_concurrency,
            }

        # 汇总结果
        for item in results:
            if isinstance(item, BaseException):
                # gather 返回的异常
                if isinstance(item, CancellationRequested):
                    if cancellation_token and cancellation_token.cancelled():
                        logger.info("批量条目评价汇总阶段检测到取消")
                        return {
                            "status": "cancelled",
                            "completed": completed,
                            "failed": failed,
                            "total": total,
                            "duration": time.time() - start_time,
                            "item_concurrency": item_concurrency,
                            "pool_concurrency": pool_concurrency,
                        }
                    logger.error(
                        "条目评价取消异常未伴随批量取消",
                        exc_info=(type(item), item, item.__traceback__),
                    )
                elif isinstance(item, asyncio.CancelledError):
                    if cancellation_token and cancellation_token.cancelled():
                        logger.info("批量条目评价汇总阶段检测到 asyncio 取消")
                        return {
                            "status": "cancelled",
                            "completed": completed,
                            "failed": failed,
                            "total": total,
                            "duration": time.time() - start_time,
                            "item_concurrency": item_concurrency,
                            "pool_concurrency": pool_concurrency,
                        }
                    logger.error(
                        "条目评价 asyncio 取消未伴随批量取消",
                        exc_info=(type(item), item, item.__traceback__),
                    )
                else:
                    logger.error(
                        "任务异常",
                        exc_info=(type(item), item, item.__traceback__),
                    )
                continue

            skill_id, result = item
            if result.get("status") == "success":
                completed.append(skill_id)
            else:
                failed[skill_id] = result.get("error", "Unknown error")

        total_duration = time.time() - start_time

        # 确定整体状态
        if not failed:
            status = "success"
        elif completed:
            status = "partial"
        else:
            status = "error"

        logger.info(f"并发评价完成: 成功={len(completed)}, 失败={len(failed)}, 耗时={total_duration:.1f}s")

        return {
            "status": status,
            "completed": completed,
            "failed": failed,
            "total": total,
            "duration": total_duration,
            "item_concurrency": item_concurrency,
            "pool_concurrency": pool_concurrency,
        }
    finally:
        if owns_executor:
            shutdown_llm_executor(executor)


def create_appraise_all_tool(
    lang: str = "en",
    on_item_progress: Optional[Callable[[str, str, str, Optional[float]], Awaitable[None]]] = None,
    on_summary: Optional[Callable[[int, int, float], Awaitable[None]]] = None,
    cancellation_token: Optional[CancellationToken] = None,
):
    """
    创建 appraise_all 工具函数（用于 Orchestrator）

    Args:
        lang: 语言
        on_item_progress: 单条目进度回调
        on_summary: 汇总进度回调

    Returns:
        异步工具函数
    """
    async def appraise_all_tool(task_id: str) -> Dict[str, Any]:
        """
        并发评价所有启用的 AGREE-II 条目

        使用此工具可以一次性评价所有条目，比逐个调用 Appraiser_item_X 更高效。
        单指南条目并发由 APPRAISAL_MAX_CONCURRENCY 控制，
        全局线程池并发由 APPRAISAL_POOL_MAX_CONCURRENCY 控制。

        Args:
            task_id: 任务 ID

        Returns:
            评价结果汇总
        """
        return await appraise_all(
            task_id,
            lang=lang,
            on_item_progress=on_item_progress,
            on_summary=on_summary,
            cancellation_token=cancellation_token,
        )

    appraise_all_tool.__name__ = "appraise_all"
    appraise_all_tool.__doc__ = """并发评价所有启用的 AGREE-II 条目

使用此工具可以一次性评价所有条目，比逐个调用 Appraiser_item_X 更高效。
单指南条目并发由 APPRAISAL_MAX_CONCURRENCY 控制（默认 5）。
全局线程池并发由 APPRAISAL_POOL_MAX_CONCURRENCY 控制（默认 10）。

Args:
    task_id: 任务 ID

Returns:
    评价结果汇总，包含:
    - status: "success" | "partial" | "error"
    - completed: 成功的条目列表
    - failed: 失败的条目及原因
    - total: 总条目数
    - duration: 总耗时（秒）
    - item_concurrency: 单指南条目并发
    - pool_concurrency: 全局线程池并发
"""

    return appraise_all_tool
