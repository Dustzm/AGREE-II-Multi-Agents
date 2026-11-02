"""
后端国际化模块

提供 t() 函数用于消息翻译，默认语言为英语
"""
from typing import Optional
from contextvars import ContextVar

request_language = ContextVar("request_language", default="en")

# 支持的语言
SUPPORTED_LANGUAGES = ["en", "zh"]
DEFAULT_LANGUAGE = "en"  # 默认优先英语


def get_language(lang: Optional[str]) -> str:
    """
    获取有效语言代码
    
    Args:
        lang: 请求的语言代码 (如 'zh', 'en', 'zh-CN', 'en-US')
    
    Returns:
        有效语言代码，异常或未知时返回英语
    """
    if not lang:
        return DEFAULT_LANGUAGE

    candidates = []
    for index, entry in enumerate(lang.split(",")):
        parts = entry.strip().lower().split(";")
        code = parts[0].split("-")[0]
        quality = 1.0
        try:
            for option in parts[1:]:
                if option.strip().startswith("q="):
                    quality = float(option.strip()[2:])
        except ValueError:
            continue
        if code in SUPPORTED_LANGUAGES and 0 < quality <= 1:
            candidates.append((quality, -index, code))
    return max(candidates)[2] if candidates else DEFAULT_LANGUAGE


# 中文消息字典
ZH_MESSAGES = {
    # === agent_runner.py ===
    "error.pdf_not_found": "PDF 文件不存在: {path}",
    "status.init_task_dir": "📁 初始化任务目录...",
    "error.init_failed": "初始化失败: {error}",
    "status.task_id": "任务ID: {task_id}",
    "status.reusing_task": "复用已有任务: {task_id}",
    "status.doc_found_skip": "📄 发现已解析的文档，跳过解析步骤",
    "status.reusing_mineru_cache": "📄 复用文件解析结果 ({pages} 页)",
    "status.parsing_mineru": "📄 文件解析中...",
    "status.parse_done_mineru": "✓ 文件解析完成 ({pages} 页)",
    "status.parsing_pypdf": "📄 文件解析中...",
    "status.parse_done_pypdf": "✓ 文件解析完成 ({pages} 页)",
    "status.parse_retry": "⚠️ 解析失败，{delay}秒后重试 ({attempt}/{max_retries})...",
    "error.parse_failed": "PDF 解析失败: {error}",
    "error.mineru_not_configured": "MinerU 服务未配置 (MINERU_SERVER_URL)",
    "error.mineru_unavailable": "MinerU 服务不可用",
    "status.building_evidence": "正在构建语义来源池...",
    "status.evidence_done": "语义来源池构建完成",
    "status.evidence_failed": "语义来源池构建失败，将使用默认模式",
    "status.reusing_evidence": "复用已有证据池，跳过预处理",
    "status.preprocess_error": "预处理异常，继续执行",
    "status.starting_agents": "🤖 启动智能体团队...",
    "status.entering_report_phase": "批量评价完成，进入报告阶段...",
    "status.report_retry": "⚠️ 报告生成连接异常，正在重试 ({attempt}/{max_retries})...",
    "agent.orchestrator_start": "开始协调评价任务",
    "agent.initiator_working": "正在生成评价计划...",
    "agent.appraiser_working": "正在评价项目...",
    "agent.reporter_working": "正在生成报告...",
    "agent.appraising_item": "正在评价 {skill_id}...",
    "agent.processing": "{agent} 正在处理...",
    "agent.report_done": "报告生成完成",
    "agent.initiator_done": "评价计划已生成",
    "agent.batch_appraise_start": "开始并发评价所有条目...",
    "agent.batch_appraise_done": "所有条目评价完成",
    "agent.item_completed": "{skill_id} 评价完成 ({duration}s)",
    "agent.item_failed": "{skill_id} 评价失败",
    "agent.batch_progress": "评价进度: {completed}/{total}",
    "agent.reporter_done": "报告生成完成",
    "status.retry_appraise": "⚠️ 检测到评价流程异常，正在重试 ({attempt}/{max_retries})...",
    "error.appraise_not_called": "评价流程异常：Orchestrator 未执行条目评价",
    
    # === chat.py ===
    "chat.default_response": "我是 AMA 评价助手，有什么可以帮你的吗？",
    "chat.fallback_response": "我是 AMA，一个 AGREE-II 指南质量评价系统。你可以上传 PDF 文件开始评价，或者继续和我聊天。",
    "error.missing_task_id": "缺少 task_id",
    "error.task_already_running": "当前会话已有任务在运行，请先终止或等待完成",
    "error.no_active_task": "当前没有可终止的任务",
    "status.task_cancelled": "任务已取消",
    "status.task_cancelling": "正在终止任务，等待当前步骤结束...",
    "status.starting_appraisal": "开始评价: {filename}",
    "status.appraisal_complete": "评价完成",
    
    # === batch.py ===
    "batch.task_pending": "等待中",
    "batch.task_running": "运行中",
    "batch.task_done": "已完成",
    "batch.task_error": "失败",
    "batch.task_cancelled": "已取消",

    # === config.py ===
    "config.connection.official_provider": "已选择官方提供商 ({provider})，请运行健康验证测试实际可用性",
    "config.connection.base_url_missing": "未配置 LLM Base URL",
    "config.connection.success": "连接成功",
    "config.connection.auth_failed": "认证失败 (401)，请检查 API Key",
    "config.connection.forbidden": "访问被拒绝 (403)",
    "config.connection.models_unsupported": "服务连通（但提供商不支持获取模型列表），请运行健康验证确认推理",
    "config.connection.http_error": "服务返回异常状态码: {status}",
    "config.connection.connect_failed": "无法连接到 {url}，请检查 URL 是否正确",
    "config.connection.timeout": "连接超时，服务可能不可用",
    "config.connection.error": "连接测试异常: {error}",
    "config.health.base_url_missing": "提供商 '{provider}' 要求配置 LLM Base URL",
    "config.health.reasoning": "[思考模式] {reasoning}",
    "config.health.empty_reply": "（模型已响应，但未返回文本内容）",
    "config.health.success": "LLM 服务正常，推理可用",
    "config.health.error": "健康验证异常: {error}",
}

# 英文消息字典
EN_MESSAGES = {
    # === agent_runner.py ===
    "error.pdf_not_found": "PDF file not found: {path}",
    "status.init_task_dir": "📁 Initializing task directory...",
    "error.init_failed": "Initialization failed: {error}",
    "status.task_id": "Task ID: {task_id}",
    "status.reusing_task": "Reusing existing task: {task_id}",
    "status.doc_found_skip": "📄 Parsed document found, skipping parsing",
    "status.reusing_mineru_cache": "📄 Reusing parsed file result ({pages} pages)",
    "status.parsing_mineru": "📄 Parsing file...",
    "status.parse_done_mineru": "✓ File parsed ({pages} pages)",
    "status.parsing_pypdf": "📄 Parsing file...",
    "status.parse_done_pypdf": "✓ File parsed ({pages} pages)",
    "status.parse_retry": "⚠️ Parse failed, retrying in {delay}s ({attempt}/{max_retries})...",
    "error.parse_failed": "PDF parsing failed: {error}",
    "error.mineru_not_configured": "MinerU service not configured (MINERU_SERVER_URL)",
    "error.mineru_unavailable": "MinerU service unavailable",
    "status.building_evidence": "Building semantic source pool...",
    "status.evidence_done": "Semantic source pool built",
    "status.evidence_failed": "Source pool failed, using default mode",
    "status.reusing_evidence": "Reusing existing evidence pool, skipping preprocessing",
    "status.preprocess_error": "Preprocessing error, continuing",
    "status.starting_agents": "🤖 Starting agent team...",
    "status.entering_report_phase": "Batch appraisal complete, entering report phase...",
    "status.report_retry": "⚠️ Report generation connection issue, retrying ({attempt}/{max_retries})...",
    "agent.orchestrator_start": "Starting coordination",
    "agent.initiator_working": "Generating appraisal plan...",
    "agent.appraiser_working": "Appraising items...",
    "agent.reporter_working": "Generating report...",
    "agent.appraising_item": "Appraising {skill_id}...",
    "agent.processing": "{agent} processing...",
    "agent.report_done": "Report generated",
    "agent.initiator_done": "Appraisal plan generated",
    "agent.batch_appraise_start": "Starting concurrent appraisal of all items...",
    "agent.batch_appraise_done": "All items appraised",
    "agent.item_completed": "{skill_id} completed ({duration}s)",
    "agent.item_failed": "{skill_id} failed",
    "agent.batch_progress": "Progress: {completed}/{total}",
    "agent.reporter_done": "Report generated",
    "status.retry_appraise": "⚠️ Appraisal flow error detected, retrying ({attempt}/{max_retries})...",
    "error.appraise_not_called": "Appraisal flow error: Orchestrator did not execute item appraisal",
    
    # === chat.py ===
    "chat.default_response": "I'm AMA evaluation assistant, how can I help you?",
    "chat.fallback_response": "I'm AMA, an AGREE-II guideline quality evaluation system. You can upload a PDF file to start evaluation, or continue chatting with me.",
    "error.missing_task_id": "Missing task_id",
    "error.task_already_running": "A task is already running in this session. Stop it or wait for completion first.",
    "error.no_active_task": "No active task can be cancelled right now",
    "status.task_cancelled": "Task cancelled",
    "status.task_cancelling": "Stopping task, waiting for the current step to finish...",
    "status.starting_appraisal": "Starting appraisal: {filename}",
    "status.appraisal_complete": "Appraisal complete",
    
    # === batch.py ===
    "batch.task_pending": "Pending",
    "batch.task_running": "Running",
    "batch.task_done": "Completed",
    "batch.task_error": "Failed",
    "batch.task_cancelled": "Cancelled",

    # === config.py ===
    "config.connection.official_provider": "Official provider selected ({provider}); run the health check to verify inference",
    "config.connection.base_url_missing": "LLM Base URL is not configured",
    "config.connection.success": "Connection successful",
    "config.connection.auth_failed": "Authentication failed (401); check the API Key",
    "config.connection.forbidden": "Access denied (403)",
    "config.connection.models_unsupported": "Service is reachable, but the provider does not support listing models; run the health check to verify inference",
    "config.connection.http_error": "Service returned HTTP status {status}",
    "config.connection.connect_failed": "Unable to connect to {url}; check the URL",
    "config.connection.timeout": "Connection timed out; the service may be unavailable",
    "config.connection.error": "Connection test failed: {error}",
    "config.health.base_url_missing": "Provider '{provider}' requires an LLM Base URL",
    "config.health.reasoning": "[Reasoning] {reasoning}",
    "config.health.empty_reply": "(The model responded without text content)",
    "config.health.success": "LLM service is healthy and inference is available",
    "config.health.error": "Health check failed: {error}",
}

# 业务消息与现有运行状态共用同一套键。
from .catalog import MESSAGES as BUSINESS_MESSAGES

for _key, (_en, _zh) in BUSINESS_MESSAGES.items():
    EN_MESSAGES[_key] = _en
    ZH_MESSAGES[_key] = _zh

# 原始异常通过 diagnostic 单独传递，避免翻译前缀后混入其他语言。
for _key, _en, _zh in (
    ("error.init_failed", "Initialization failed. See diagnostic details.", "初始化失败，请查看诊断详情。"),
    ("error.parse_failed", "PDF parsing failed. See diagnostic details.", "PDF 解析失败，请查看诊断详情。"),
    ("config.connection.error", "Connection test failed. See diagnostic details.", "连接测试失败，请查看诊断详情。"),
    ("config.health.error", "Health check failed. See diagnostic details.", "健康验证失败，请查看诊断详情。"),
):
    EN_MESSAGES[_key] = _en
    ZH_MESSAGES[_key] = _zh

# 消息字典映射
MESSAGES = {
    "zh": ZH_MESSAGES,
    "en": EN_MESSAGES,
}


def t(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """
    翻译函数
    
    Args:
        key: 消息键名
        lang: 语言代码 (zh/en)，为 None 时使用请求上下文，缺省英语
        **kwargs: 格式化参数
    
    Returns:
        翻译后的消息，找不到时返回 key 本身
    """
    try:
        lang = get_language(lang) if lang else request_language.get()
        messages = MESSAGES.get(lang, EN_MESSAGES)
        template = messages.get(key)
        
        if template is None:
            # 回退到英语
            template = EN_MESSAGES.get(key, key)
        
        if kwargs:
            return template.format(**kwargs)
        return template
    
    except Exception:
        # 任何异常返回英语或 key
        template = EN_MESSAGES.get(key, key)
        try:
            if kwargs:
                return template.format(**kwargs)
            return template
        except Exception:
            return key
