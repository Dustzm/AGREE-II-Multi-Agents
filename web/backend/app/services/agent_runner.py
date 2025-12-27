"""
智能体运行服务

封装 Orchestrator 调用，支持 WebSocket 回调
参照 CLI 的处理逻辑实现
"""
import asyncio
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

logger = logging.getLogger(__name__)


class AppraisalRunner:
    """
    评价任务运行器
    
    封装 Orchestrator 的创建和执行，支持状态回调
    参照 CLI 的 WeaverCLI.start_extraction 和 run_agents 实现
    """
    
    def __init__(
        self,
        on_status: Optional[Callable[[str, str], None]] = None,
        on_agent: Optional[Callable[[str, str, int, int, Optional[str]], None]] = None,
        on_complete: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            on_status: 状态回调 (type, content)
            on_agent: 智能体回调 (agent, content, progress, total, prompt)
            on_complete: 完成回调 (task_id)
            on_error: 错误回调 (error_msg)
        """
        self.on_status = on_status or (lambda *_: None)
        self.on_agent = on_agent or (lambda *_: None)
        self.on_complete = on_complete or (lambda *_: None)
        self.on_error = on_error or (lambda *_: None)
        
        self._cancelled = False
    
    def cancel(self):
        """取消当前任务"""
        self._cancelled = True
    
    async def run(self, task_id: str, pdf_path: str):
        """
        运行评价任务
        
        参照 CLI 的处理流程:
        1. 初始化任务目录 (init_task_directory)
        2. 解析 PDF (MinerUParser.parse_pdf_to_file)
        3. 运行 Orchestrator
        
        Args:
            task_id: 任务 ID
            pdf_path: PDF 文件路径
        """
        try:
            self._cancelled = False
            
            # 导入智能体（延迟导入避免循环依赖）
            from core.agents import create_orchestrator, init_task_directory
            from core.parsers import MinerUParser
            from core.config import get_settings
            from google.adk import runners
            from google.genai import types
            
            pdf_file = Path(pdf_path)
            if not pdf_file.exists():
                self.on_error(f"PDF 文件不存在: {pdf_path}")
                return
            
            # Step 1: 初始化任务目录
            self.on_status("status", "📁 初始化任务目录...")
            
            # 使用文件名初始化（和 CLI 一致）
            task_result = init_task_directory(pdf_file.name)
            
            if task_result.get("status") == "error":
                self.on_error(f"初始化失败: {task_result.get('error', '未知错误')}")
                return
            
            actual_task_id = task_result["task_id"]
            source_path = task_result["paths"]["source"]
            prep_dir = task_result["paths"]["prep_dir"]
            
            self.on_status("status", f"任务ID: {actual_task_id}")
            
            if self._cancelled:
                return
            
            # Step 2: 检查并解析 PDF
            source_file = Path(source_path)
            
            if source_file.exists():
                self.on_status("status", "📄 发现已解析的文档，跳过解析步骤")
            else:
                # 尝试使用 MinerU，失败则回退到 PyPDF
                parse_success = False
                
                # 首先尝试 MinerU（如果配置了远程服务）
                try:
                    settings = get_settings()
                    mineru_url = settings.mineru.server_url
                    
                    # 只要配置了 MinerU URL 就尝试使用
                    if mineru_url:
                        self.on_status("status", f"📄 正在解析 PDF (MinerU VLM)...")
                        
                        parser = MinerUParser()
                        # 先检查服务是否可用
                        service_ok = await parser.health_check()
                        
                        if service_ok:
                            parse_result = await parser.parse_pdf_to_file(
                                pdf_path=str(pdf_file),
                                source_path=source_path,
                                prep_dir=prep_dir
                            )
                            
                            if parse_result.get("status") == "success":
                                pages = parse_result.get("pages", 0)
                                self.on_status("status", f"✓ PDF 解析完成 ({pages} 页) - MinerU")
                                parse_success = True
                            else:
                                logger.warning(f"MinerU 解析失败: {parse_result.get('error')}")
                        else:
                            logger.warning(f"MinerU 服务不可用 ({mineru_url})，将尝试本地解析")
                    else:
                        logger.info("MinerU 未配置 (MINERU_SERVER_URL)，使用本地解析器")
                        
                except Exception as e:
                    logger.warning(f"MinerU 解析异常: {e}，将尝试本地解析")
                
                # 如果 MinerU 失败，回退到 PyPDF
                if not parse_success:
                    self.on_status("status", "📄 正在解析 PDF (本地 PyPDF)...")
                    
                    try:
                        from core.parsers import PyPDFParser
                        pypdf_parser = PyPDFParser()
                        
                        # PyPDFParser.parse_pdf 是同步的
                        result = pypdf_parser.parse_pdf(str(pdf_file), str(source_file.parent))
                        
                        if result.get("status") == "success":
                            # 确保保存为 source.md
                            if result.get("output_path") != source_path:
                                import shutil
                                if Path(result["output_path"]).exists():
                                    shutil.move(result["output_path"], source_path)
                            
                            pages = result.get("pages", 0)
                            self.on_status("status", f"✓ PDF 解析完成 ({pages} 页) - PyPDF")
                            parse_success = True
                        else:
                            self.on_error(f"PDF 解析失败: {result.get('error', '未知错误')}")
                            return
                            
                    except Exception as e:
                        self.on_error(f"PDF 解析失败: {str(e)}")
                        return
            
            if self._cancelled:
                return
            
            # Step 3: 运行 Orchestrator
            self.on_status("status", "🤖 启动智能体团队...")
            self.on_agent("Orchestrator", "开始协调评价任务", 0, 4)
            
            orchestrator = create_orchestrator()
            runner = runners.InMemoryRunner(
                agent=orchestrator,
                app_name='ama_web',
            )
            
            session = await runner.session_service.create_session(
                app_name='ama_web',
                user_id='web_user'
            )
            
            # 构建初始消息（参照 CLI）
            prompt = f"""请开始处理 AGREE-II 指南评价任务。

任务信息：
- task_id: {actual_task_id}
- 源文档已解析并保存到 source.md

请按照工作流程执行：
1. 调用 Initiator 生成评价计划
2. 调用 read_plan 读取计划
3. 根据计划中的阶段标记，调用对应的工具：
   - [APPRAISE:item_x] 标记 -> 调用 Appraiser
   - [REPORT] 标记 -> 调用 Reporter
4. 每个任务前后更新状态 (in_progress -> completed)

开始执行！"""
            
            initial_message = types.Content(
                role='user',
                parts=[types.Part(text=prompt)]
            )
            
            # 跟踪当前智能体和调用状态
            current_agent = "Orchestrator"
            agent_progress = {
                "Initiator": 1, 
                "Appraiser": 2, 
                "Reporter": 3
            }
            agent_messages = {
                "Initiator": "正在生成评价计划...",
                "Appraiser": "正在评价项目...",
                "Reporter": "正在生成报告..."
            }
            
            # 运行对话
            async for event in runner.run_async(
                user_id='web_user',
                session_id=session.id,
                new_message=initial_message
            ):
                if self._cancelled:
                    break
                
                # 处理事件内容
                if hasattr(event, 'content') and event.content:
                    for part in event.content.parts:
                        # 1. 检测函数调用（工具调用）
                        if hasattr(part, 'function_call') and part.function_call:
                            tool_name = part.function_call.name
                            logger.info(f"检测到工具调用: {tool_name}")
                            
                            # 根据工具名称判断是哪个子代理
                            agent_name = None
                            custom_message = None
                            
                            if 'initiator' in tool_name.lower():
                                agent_name = "Initiator"
                            elif tool_name.lower().startswith('appraiser'):
                                agent_name = "Appraiser"
                                appraiser_prompt = None  # 用于存储提示词
                                # 解析 skill_id 以显示具体条目
                                try:
                                    # 从工具名提取 skill_id，如 Appraiser_item_1 -> item_1
                                    skill_id = ""
                                    if '_item_' in tool_name.lower():
                                        skill_id = tool_name.split('_', 1)[1]  # item_1
                                    else:
                                        # 从参数中提取
                                        args = part.function_call.args
                                        if args:
                                            if isinstance(args, dict):
                                                skill_id = args.get("skill_id", "")
                                            elif hasattr(args, 'get'):
                                                skill_id = args.get("skill_id", "")
                                            else:
                                                import json
                                                args_dict = json.loads(str(args)) if isinstance(args, str) else {}
                                                skill_id = args_dict.get("skill_id", "")
                                    
                                    if skill_id:
                                        custom_message = f"正在评价 {skill_id}..."
                                        logger.info(f"Appraiser 正在评价条目: {skill_id}")
                                        
                                        # 获取渲染后的提示词
                                        try:
                                            from core.prompts.renderer import render_appraiser_prompt
                                            appraiser_prompt = render_appraiser_prompt(skill_id)
                                        except Exception as e:
                                            logger.warning(f"获取提示词失败: {e}")
                                except Exception as e:
                                    logger.warning(f"解析 Appraiser 参数失败: {e}")
                                    
                            elif 'reporter' in tool_name.lower():
                                agent_name = "Reporter"
                            
                            # 发送子代理状态更新
                            if agent_name:
                                # 对于 Appraiser，总是发送更新（因为可能是不同条目）
                                if agent_name == "Appraiser" or agent_name != current_agent:
                                    current_agent = agent_name
                                    progress = agent_progress.get(agent_name, 0)
                                    message = custom_message or agent_messages.get(agent_name, f"{agent_name} 正在处理...")
                                    # 发送带提示词的消息（只有 Appraiser 有）
                                    self.on_agent(agent_name, message, progress, 4, appraiser_prompt if agent_name == "Appraiser" else None)
                                    logger.info(f"切换到代理: {agent_name}, 消息: {message}")
                                    appraiser_prompt = None  # 重置
                        
                        # 2. 检测函数响应（工具返回）
                        elif hasattr(part, 'function_response') and part.function_response:
                            # 函数执行完成，可以在这里添加额外的状态更新
                            pass
                        
                        # 3. 处理文本输出
                        elif hasattr(part, 'text') and part.text:
                            text = part.text
                            # 过滤思考过程
                            if '<think>' in text or '💭' in text:
                                continue
                            # 只显示有意义的输出
                            if len(text.strip()) > 20:
                                # 截取前 200 字符
                                display_text = text[:200] + "..." if len(text) > 200 else text
                                self.on_status("status", display_text)
                
                # 备用：仍然检查 author（以防某些事件有）
                if hasattr(event, 'author') and event.author:
                    author = event.author
                    if author != current_agent and author in agent_progress:
                        current_agent = author
                        progress = agent_progress.get(author, 0)
                        message = agent_messages.get(author, f"{author} 正在处理...")
                        self.on_agent(author, message, progress, 4)
                        logger.info(f"通过 author 切换到代理: {author}")
            
            
            if not self._cancelled:
                self.on_agent("Reporter", "报告生成完成", 4, 4)
                self.on_complete(actual_task_id)
        
        except Exception as e:
            logger.exception(f"评价任务失败: {e}")
            self.on_error(str(e))
