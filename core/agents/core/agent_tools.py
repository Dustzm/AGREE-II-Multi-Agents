"""
Agent 工具封装

将 Explorer、Extractor 等 Agent 封装为工具函数
供 Coordinator 直接调用，避免 transfer_to_agent 的控制流问题
"""
import asyncio
from typing import Optional

from google.adk.runners import InMemoryRunner
from google.genai import types

# 注意：不在顶层导入 Agent 创建函数，避免循环导入
# 在函数内按需导入


def _run_agent_sync(agent, user_message: str, app_name: str = "kg-weaver") -> str:
    """
    同步执行 Agent 并返回结果
    
    Args:
        agent: ADK Agent 实例
        user_message: 用户消息
        app_name: 应用名称
        
    Returns:
        Agent 的最终响应文本
    """
    async def _run():
        runner = InMemoryRunner(agent=agent, app_name=app_name)
        session = await runner.session_service.create_session(
            app_name=app_name,
            user_id="tool_caller"
        )
        
        user_content = types.Content(
            role="user",
            parts=[types.Part(text=user_message)]
        )
        
        final_response = ""
        async for event in runner.run_async(
            user_id="tool_caller",
            session_id=session.id,
            new_message=user_content
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        final_response = part.text  # 取最后一次响应
        
        return final_response
    
    # 获取或创建事件循环
    try:
        loop = asyncio.get_running_loop()
        # 如果已在异步环境中，创建新任务
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, _run())
            return future.result()
    except RuntimeError:
        # 没有运行的事件循环，直接运行
        return asyncio.run(_run())


def run_initiator(task_id: str, instruction: str = "") -> dict:
    """
    执行 Initiator Agent，生成全局地图和抽取计划
    
    Args:
        task_id: 任务ID
        instruction: 额外指令（可选）
        
    Returns:
        dict: {"status": "success/error", "response": str, "error": str}
    """
    try:
        from ..initiator import create_initiator_agent
        agent = create_initiator_agent()
        user_message = f"""任务ID: {task_id}
        
请执行初始化任务：
1. 调用 read_source_document 读取源文档
2. 分析文档结构，生成全局地图 (save_guide_map)
3. 制定抽取计划 (save_extraction_plan)

{instruction}"""
        
        response = _run_agent_sync(agent, user_message)
        return {
            "status": "success",
            "response": response,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "response": "",
            "error": str(e)
        }


def run_explorer(task_id: str, section_id: str, instruction: str = "") -> dict:
    """
    执行 Explorer Agent，提取指定章节的原文并写入 buffer
    
    Args:
        task_id: 任务ID
        section_id: 章节ID（如 "basic_info", "problem_1"）
        instruction: 额外指令（可选）
        
    Returns:
        dict: {"status": "success/error", "response": str, "error": str}
    """
    try:
        from ..explorer import create_explorer_agent
        agent = create_explorer_agent()
        user_message = f"""任务ID: {task_id}
章节ID: {section_id}

请执行以下步骤：
1. 调用 read_source_document(task_id="{task_id}") 读取源文档
2. 调用 read_guide_map(task_id="{task_id}") 了解文档结构
3. 根据章节ID "{section_id}" 定位并提取相关原文
4. 调用 write_to_context_buffer(task_id="{task_id}", section_id="{section_id}", content=提取的原文) 保存

**必须调用 write_to_context_buffer 工具！**

{instruction}"""
        
        response = _run_agent_sync(agent, user_message)
        return {
            "status": "success",
            "response": response,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "response": "",
            "error": str(e)
        }


def run_extractor(task_id: str, buffer_id: str, instruction: str = "") -> dict:
    """
    执行 Extractor Agent，从 buffer 中抽取知识三元组
    
    Args:
        task_id: 任务ID
        buffer_id: Buffer ID（如 "basic_info", "problem_1"）
        instruction: 额外指令（可选）
        
    Returns:
        dict: {"status": "success/error", "response": str, "error": str}
    """
    try:
        from ..extractor import create_extractor_agent
        agent = create_extractor_agent()
        user_message = f"""任务ID: {task_id}
Buffer ID: {buffer_id}

请执行以下步骤：
1. 调用 read_context_buffer(task_id="{task_id}", buffer_ids=["{buffer_id}"]) 读取内容
2. 分析文本，识别实体和关系
3. 构造三元组列表
4. 调用 save_extracted_triples(task_id="{task_id}", triples=三元组列表) 保存

**必须调用 save_extracted_triples 工具！**

{instruction}"""
        
        response = _run_agent_sync(agent, user_message)
        return {
            "status": "success",
            "response": response,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "response": "",
            "error": str(e)
        }


def run_validator(task_id: str, instruction: str = "") -> dict:
    """
    执行 Validator Agent，验证抽取结果并生成最终知识图谱
    
    Args:
        task_id: 任务ID
        instruction: 额外指令（可选）
        
    Returns:
        dict: {"status": "success/error", "response": str, "error": str}
    """
    try:
        from ..validator import create_validator_agent
        agent = create_validator_agent()
        user_message = f"""任务ID: {task_id}

请执行验证任务：
1. 读取所有抽取的三元组
2. 验证三元组的质量和一致性
3. 合并去重，生成最终知识图谱
4. 保存验证报告

{instruction}"""
        
        response = _run_agent_sync(agent, user_message)
        return {
            "status": "success",
            "response": response,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "response": "",
            "error": str(e)
        }
