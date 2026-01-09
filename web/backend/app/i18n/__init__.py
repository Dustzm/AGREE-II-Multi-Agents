"""
后端国际化模块

提供 t() 函数用于消息翻译，默认语言为英语
"""
from typing import Optional

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
    
    # 规范化语言代码
    lang = lang.lower().split("-")[0]  # 'zh-CN' -> 'zh'
    
    if lang in SUPPORTED_LANGUAGES:
        return lang
    
    return DEFAULT_LANGUAGE


# 中文消息字典
ZH_MESSAGES = {
    # === agent_runner.py ===
    "error.pdf_not_found": "PDF 文件不存在: {path}",
    "status.init_task_dir": "📁 初始化任务目录...",
    "error.init_failed": "初始化失败: {error}",
    "status.task_id": "任务ID: {task_id}",
    "status.doc_found_skip": "📄 发现已解析的文档，跳过解析步骤",
    "status.parsing_mineru": "📄 正在解析 PDF (MinerU VLM)...",
    "status.parse_done_mineru": "✓ PDF 解析完成 ({pages} 页) - MinerU",
    "status.parsing_pypdf": "📄 正在解析 PDF (本地 PyPDF)...",
    "status.parse_done_pypdf": "✓ PDF 解析完成 ({pages} 页) - PyPDF",
    "error.parse_failed": "PDF 解析失败: {error}",
    "status.building_evidence": "正在构建语义证据池...",
    "status.evidence_done": "语义证据池构建完成",
    "status.evidence_failed": "语义证据池构建失败，将使用默认模式",
    "status.preprocess_error": "预处理异常，继续执行",
    "status.starting_agents": "🤖 启动智能体团队...",
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
    "agent.reporter_done": "报告生成完成",
    
    # === chat.py ===
    "chat.default_response": "我是 AMA 评价助手，有什么可以帮你的吗？",
    "chat.fallback_response": "我是 AMA，一个 AGREE-II 指南质量评价系统。你可以上传 PDF 文件开始评价，或者继续和我聊天。",
    "error.missing_task_id": "缺少 task_id",
    "status.task_cancelled": "任务已取消",
    "status.starting_appraisal": "开始评价: {filename}",
    "status.appraisal_complete": "评价完成",
    
    # === batch.py ===
    "batch.task_pending": "等待中",
    "batch.task_running": "运行中",
    "batch.task_done": "已完成",
    "batch.task_error": "失败",
    "batch.task_cancelled": "已取消",
}

# 英文消息字典
EN_MESSAGES = {
    # === agent_runner.py ===
    "error.pdf_not_found": "PDF file not found: {path}",
    "status.init_task_dir": "📁 Initializing task directory...",
    "error.init_failed": "Initialization failed: {error}",
    "status.task_id": "Task ID: {task_id}",
    "status.doc_found_skip": "📄 Parsed document found, skipping parsing",
    "status.parsing_mineru": "📄 Parsing PDF (MinerU VLM)...",
    "status.parse_done_mineru": "✓ PDF parsed ({pages} pages) - MinerU",
    "status.parsing_pypdf": "📄 Parsing PDF (Local PyPDF)...",
    "status.parse_done_pypdf": "✓ PDF parsed ({pages} pages) - PyPDF",
    "error.parse_failed": "PDF parsing failed: {error}",
    "status.building_evidence": "Building semantic evidence pool...",
    "status.evidence_done": "Semantic evidence pool built",
    "status.evidence_failed": "Evidence pool failed, using default mode",
    "status.preprocess_error": "Preprocessing error, continuing",
    "status.starting_agents": "🤖 Starting agent team...",
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
    "agent.reporter_done": "Report generated",
    
    # === chat.py ===
    "chat.default_response": "I'm AMA evaluation assistant, how can I help you?",
    "chat.fallback_response": "I'm AMA, an AGREE-II guideline quality evaluation system. You can upload a PDF file to start evaluation, or continue chatting with me.",
    "error.missing_task_id": "Missing task_id",
    "status.task_cancelled": "Task cancelled",
    "status.starting_appraisal": "Starting appraisal: {filename}",
    "status.appraisal_complete": "Appraisal complete",
    
    # === batch.py ===
    "batch.task_pending": "Pending",
    "batch.task_running": "Running",
    "batch.task_done": "Completed",
    "batch.task_error": "Failed",
    "batch.task_cancelled": "Cancelled",
}

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
        lang: 语言代码 (zh/en)，为 None 时使用英语
        **kwargs: 格式化参数
    
    Returns:
        翻译后的消息，找不到时返回 key 本身
    """
    try:
        lang = get_language(lang)
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
