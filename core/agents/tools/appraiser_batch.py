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
from core.llm.failures import reported_kind
from core.agents.tools.appraiser_tools import save_item_evaluation
from core.skills import list_available_skills, get_skill_summary
from core.utils.cancellation import CancellationRequested, CancellationToken
from core.rule_engine.appraiser_adapter import append_rule_engine_title_suffix
from core.runtime import (
    ExecutionRuntime,
    ExecutionStage,
    InstrumentedThreadPoolExecutor,
    bind_execution_runtime,
    get_execution_runtime,
    reset_execution_runtime,
)

logger = logging.getLogger(__name__)

def create_llm_executor(pool_concurrency: int) -> ThreadPoolExecutor:
    """创建批量条目评价线程池。"""
    logger.info("创建 LLM 评价线程池: pool_concurrency=%s", pool_concurrency)
    return InstrumentedThreadPoolExecutor(max_workers=pool_concurrency, thread_name_prefix="llm_eval_")


def shutdown_llm_executor(executor: ThreadPoolExecutor | None) -> None:
    """关闭批量条目评价线程池。"""
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


def get_llm_executor_stats(executor: ThreadPoolExecutor | None) -> dict[str, int]:
    """返回评价线程池统计；普通线程池返回空统计。"""
    if executor is None or not hasattr(executor, "stats"):
        return {}
    return executor.stats()


async def appraise_all(
    task_id: str,
    skill_ids: Optional[List[str]] = None,
    lang: str = "en",
    on_item_progress: Optional[Callable[[str, str, str, Optional[float]], Awaitable[None]]] = None,
    on_summary: Optional[Callable[[int, int, float], Awaitable[None]]] = None,
    cancellation_token: Optional[CancellationToken] = None,
    executor: Optional[ThreadPoolExecutor] = None,
    runtime: Optional[ExecutionRuntime] = None,
    analysis_engine: str | None = None,
    collect_exceptions: bool = False,
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
        analysis_engine: 本任务使用的评价引擎；为空时沿用全局配置

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
    if cancellation_token:
        cancellation_token.raise_if_cancelled()
    runtime = runtime or get_execution_runtime()
    owns_runtime = runtime is None
    owns_executor = owns_runtime and executor is None
    if runtime is None:
        executor = executor or create_llm_executor(pool_concurrency)
        runtime = ExecutionRuntime.from_settings(
            settings,
            executor=executor,
            owns_executor=False,
        )

    # 获取待评价的条目列表
    if skill_ids is None:
        skill_ids = list_available_skills(enabled_only=True)

    total = len(skill_ids)
    logger.info(
        f"开始并发评价: task_id={task_id}, 条目数={total}, "
        f"单指南并发={item_concurrency}, 全局池并发={pool_concurrency}"
    )

    # 使用传入的回调或全局回调
    progress_callback = on_item_progress
    summary_callback = on_summary

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
    degraded_items: Dict[str, str] = {}
    report_degraded_items: Dict[str, str] = {}
    durations: Dict[str, float] = {}
    failed: Dict[str, str] = {}
    failed_details: Dict[str, Dict[str, str]] = {}
    failures: Dict[str, BaseException] = {}
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
            title = append_rule_engine_title_suffix(
                title,
                skill_id,
                lang=lang,
                analysis_engine=analysis_engine,
            )

            # 通知开始
            if progress_callback:
                try:
                    await progress_callback(skill_id, title, "started", None)
                except Exception as e:
                    logger.warning(f"进度回调失败: {e}")

            # 条目级令牌：超时时只取消本条目，让仍在运行的线程在下一次模型调用前退出
            item_token = CancellationToken.linked_to(cancellation_token)
            try:
                def run_item_evaluation_with_logging() -> Dict[str, Any]:
                    logger.info("条目评价线程开始: task_id=%s item=%s", task_id, skill_id)
                    try:
                        if analysis_engine is None:
                            return run_iterative_evaluation(
                                task_id,
                                skill_id,
                                lang,
                                item_token,
                            )
                        return run_iterative_evaluation(
                            task_id,
                            skill_id,
                            lang,
                            item_token,
                            analysis_engine=analysis_engine,
                        )
                    finally:
                        logger.info("条目评价线程返回: task_id=%s item=%s", task_id, skill_id)

                result = await runtime.run_blocking(
                    ExecutionStage.APPRAISER,
                    run_item_evaluation_with_logging,
                    timeout=llm_timeout,
                    serialization_key=(task_id, skill_id),
                )
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
                    logger.info("条目评价开始保存: task_id=%s item=%s", task_id, skill_id)
                    save_item_evaluation(
                        task_id=task_id,
                        skill_id=skill_id,
                        step1_locate=cot.get("step1_locate", ""),
                        step2_evidence=cot.get("step2_evidence", []),
                        step3_reasoning=cot.get("step3_reasoning", ""),
                        score=cot.get("score", 1),
                        evidence_sources=result.get("evidence_sources", []),
                        rule_engine=result.get("rule_engine"),
                        hybrid_router=result.get("hybrid_router"),
                    )
                    logger.info("条目评价保存完成: task_id=%s item=%s", task_id, skill_id)

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
                    rule_engine_payload = result.get("rule_engine") or {}
                    outcome: Dict[str, Any] = {"status": "success", "duration": duration}
                    if rule_engine_payload.get("degraded"):
                        outcome["degraded"] = True
                        outcome["fallback_reason"] = rule_engine_payload.get("fallback_reason")
                    if (result.get("iteration_stats") or {}).get("report_degraded"):
                        # 条目评分已产出，但最终解释是降级文本，与规则引擎降级分开记账
                        outcome["report_degraded"] = True
                    return skill_id, outcome
                else:
                    error_msg = result.get("error", "评价失败")

                    # 通知失败
                    if progress_callback:
                        try:
                            await progress_callback(skill_id, title, "failed", None)
                        except Exception as e:
                            logger.warning(f"进度回调失败: {e}")

                    logger.warning(f"评价失败: {skill_id} - {error_msg}")
                    return skill_id, {"status": "failed", "error": error_msg, "type": "EvaluationError", "stage": "item_evaluation"}

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
            except asyncio.TimeoutError as e:
                item_token.cancel("item_timeout")
                error_msg = f"{type(e).__name__}: 条目评价超时 ({llm_timeout}s)"
                if progress_callback:
                    try:
                        await progress_callback(skill_id, title, "failed", None)
                    except Exception as ex:
                        logger.warning(f"进度回调失败: {ex}")
                logger.exception("条目评价超时: %s - %s", skill_id, error_msg)
                return skill_id, {"status": "failed", "error": error_msg, "type": type(e).__name__, "stage": "item_evaluation", "exception": e}
            except Exception as e:
                error_msg = str(e)

                # 通知失败
                if progress_callback:
                    try:
                        await progress_callback(skill_id, title, "failed", None)
                    except Exception as ex:
                        logger.warning(f"进度回调失败: {ex}")

                logger.exception("评价异常: %s - %s", skill_id, error_msg)
                return skill_id, {"status": "failed", "error": error_msg, "type": type(e).__name__, "stage": getattr(e, "appraisal_stage", "item_evaluation"), "exception": e}

    # 并发执行所有评价任务
    if cancellation_token:
        cancellation_token.raise_if_cancelled()
    runtime_token = bind_execution_runtime(runtime)
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
        for requested_id, item in zip(skill_ids, results):
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
                failed[requested_id] = str(item)
                failed_details[requested_id] = {
                    "type": type(item).__name__,
                    "stage": "item_evaluation",
                    "message": str(item),
                    "failure_kind": reported_kind(item),
                }
                failures[requested_id] = item
                continue

            skill_id, result = item
            if isinstance(result.get("duration"), (int, float)):
                durations[skill_id] = round(float(result["duration"]), 3)
            if result.get("status") == "success":
                completed.append(skill_id)
                if result.get("degraded"):
                    degraded_items[skill_id] = result.get("fallback_reason") or "unknown"
                if result.get("report_degraded"):
                    report_degraded_items[skill_id] = "report_explanation_degraded"
            else:
                failed[skill_id] = result.get("error", "Unknown error")
                failed_details[skill_id] = {
                    "type": result.get("type", "EvaluationError"),
                    "stage": result.get("stage", "item_evaluation"),
                    "message": failed[skill_id],
                    "failure_kind": reported_kind(
                        result.get("exception"),
                        result.get("type", "EvaluationError"),
                        failed[skill_id],
                    ),
                }
                if isinstance(result.get("exception"), BaseException):
                    failures[skill_id] = result["exception"]

        total_duration = time.time() - start_time

        # 确定整体状态
        if not failed:
            status = "success"
        elif completed:
            status = "partial"
        else:
            status = "error"

        logger.info(
            "并发评价完成: 成功=%s, 失败=%s, 降级=%s, 耗时=%.1fs",
            len(completed),
            len(failed),
            len(degraded_items),
            total_duration,
        )
        if degraded_items:
            logger.warning("条目规则引擎降级回退: %s", degraded_items)

        summary = {
            "status": status,
            "completed": completed,
            "degraded": degraded_items,
            "report_degraded": report_degraded_items,
            "durations": durations,
            "failed": failed,
            "failed_details": failed_details,
            "total": total,
            "duration": total_duration,
            "item_concurrency": item_concurrency,
            "pool_concurrency": pool_concurrency,
        }
        if collect_exceptions:
            summary["exceptions"] = failures
        try:
            from core.observability.appraisal import write_failure_report

            write_failure_report(task_id, summary)
        except Exception:  # 观测数据不得影响主流程
            logger.warning("failure_report 写出异常", exc_info=True)
        return summary
    finally:
        reset_execution_runtime(runtime_token)
        if owns_runtime:
            await runtime.aclose()
            if owns_executor:
                await asyncio.to_thread(shutdown_llm_executor, executor)
