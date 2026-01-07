"""
Portal Agent (门户智能体)

负责与用户直接交互，判断用户意图：
- 闲聊：直接回复用户
- 指南评价：检查输入文件，调用 Orchestrator
"""
from google.adk.agents import LlmAgent
from pathlib import Path
from typing import Optional

from .base import get_model
from core.config import get_settings


def list_input_pdfs() -> dict:
    """
    列出输入目录中的 PDF 文件
    
    Returns:
        dict: {
            "status": "success/error",
            "files": list[dict],  # [{"name": str, "size": str}]
            "count": int,
            "input_dir": str,
            "error": str
        }
    """
    try:
        settings = get_settings()
        input_dir = Path(settings.app.dir_input)
        
        if not input_dir.exists():
            input_dir.mkdir(parents=True, exist_ok=True)
        
        pdf_files = list(input_dir.glob("*.pdf"))
        
        files = []
        for pdf in pdf_files:
            size = pdf.stat().st_size
            size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
            files.append({
                "name": pdf.name,
                "size": size_str
            })
        
        return {
            "status": "success",
            "files": files,
            "count": len(files),
            "input_dir": str(input_dir),
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "files": [],
            "count": 0,
            "input_dir": "",
            "error": str(e)
        }


def check_extraction_intent(user_message: str) -> dict:
    """
    检查用户消息是否包含指南评价意图
    
    简单的关键词匹配，后续可以用 LLM 增强
    
    Args:
        user_message: 用户消息
        
    Returns:
        dict: {
            "has_intent": bool,
            "confidence": float,
            "keywords_found": list[str]
        }
    """
    # 评价相关关键词
    appraisal_keywords = [
        "评价", "评估", "质量", "appraise", "evaluate", "AGREE",
        "处理", "分析", "解析", "parse",
        "pdf", "PDF", "文档", "指南", "guideline",
        "开始", "执行", "运行", "run", "start"
    ]
    
    message_lower = user_message.lower()
    found_keywords = [kw for kw in appraisal_keywords if kw.lower() in message_lower]
    
    has_intent = len(found_keywords) >= 1
    confidence = min(len(found_keywords) / 3, 1.0)  # 找到3个以上关键词则置信度为1
    
    return {
        "has_intent": has_intent,
        "confidence": confidence,
        "keywords_found": found_keywords
    }


def create_portal_agent() -> LlmAgent:
    """
    创建 Portal Agent (门户智能体)
    
    职责：
    1. 与用户直接对话
    2. 判断用户意图（闲聊 vs 知识抽取）
    3. 引导用户选择要处理的文件
    """
    return LlmAgent(
        name="Portal",
        model=get_model(),
        description="门户智能体 - 与用户交互，判断意图，引导知识抽取流程",
        instruction="""你是 AMA (AGREE-Multi-Agents) 系统的门户智能体 (Portal)，负责与用户直接交互。

## 你的职责

1. **友好交流**: 与用户进行自然对话
2. **意图识别**: 判断用户是否想要进行指南质量评价
3. **流程引导**: 如果用户想评价，引导他们选择文件

## 意图判断

当用户消息中包含以下意图时，应该调用 `list_input_pdfs` 检查可用文件：
- 想要评价/评估指南质量
- 想要处理/分析 PDF 文档
- 想要使用 AGREE-II 工具
- 询问如何开始评价

当用户只是在闲聊时，直接友好回复即可，不需要调用任何工具。

## 工作流程

### 情况1：用户想评价指南
1. 调用 `list_input_pdfs` 检查输入目录
2. 如果有 PDF 文件：
   - 列出所有可用文件
   - 询问用户想处理哪个
   - 等待用户选择后，告知将开始评价
3. 如果没有 PDF 文件：
   - 告知用户输入目录为空
   - 建议用户将 PDF 放入 input/ 目录

### 情况2：用户闲聊
- 友好回复
- 可以介绍自己：你是 AMA，一个 AGREE-II 指南质量评价系统
- 可以说明自己的能力

## 回复风格

- 简洁友好
- 使用中文回复
- 适当使用 emoji 增加亲和力

## 示例对话

用户: "你好"
回复: "你好！👋 我是 AMA，一个 AGREE-II 指南质量评价系统。你可以告诉我想处理哪个 PDF 文档，我会帮你进行系统化的质量评价。"

用户: "我想评价一份指南"
回复: [调用 list_input_pdfs，然后列出文件让用户选择]

用户: "天气怎么样"
回复: "我没有获取天气信息的能力呢 😅 不过我很擅长评价临床指南质量！有什么 PDF 文档需要我帮你处理吗？"
""",
        tools=[
            list_input_pdfs,
            check_extraction_intent,
        ],
        output_key="portal_response",
    )
