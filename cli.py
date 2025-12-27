#!/usr/bin/env python3
"""
AMA (AGREE-Multi-Agents) 交互式命令行界面

提供类似 Claude Code 的交互式 CLI 体验
集成 Portal Agent 进行意图识别和用户交互
真正调用智能体团队进行 AGREE-II 指南质量评价
"""
import asyncio
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from google.adk import runners
from google.genai import types

from core.config import get_settings
from core.agents import (
    create_portal_agent,
    create_orchestrator,
    list_input_pdfs,
    check_extraction_intent,
    init_task_directory,
)
from core.parsers import MinerUParser


# 初始化 Rich Console
console = Console()


# 智能体颜色映射 (Portal 不在此列，因为不显示其状态)
AGENT_COLORS = {
    "Orchestrator": "bold cyan",
    "AMACoordinator": "bold cyan",
    "Initiator": "bold green",
    "Appraiser": "bold magenta",
    "Reporter": "bold blue",
    # 保留旧智能体颜色
    "Explorer": "bold yellow", 
    "Extractor": "bold magenta",
    "Validator": "bold blue",
}


def print_banner():
    """打印欢迎横幅 - 彩色大字"""
    # ASCII Art 大字
    logo = """
 █████╗ ███╗   ███╗ █████╗ 
██╔══██╗████╗ ████║██╔══██╗
███████║██╔████╔██║███████║
██╔══██║██║╚██╔╝██║██╔══██║
██║  ██║██║ ╚═╝ ██║██║  ██║
╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝
"""
    
    # 渐变颜色
    colors = ["bright_blue", "blue", "cyan", "bright_cyan", "green", "bright_green"]
    lines = logo.strip().split('\n')
    
    console.print()
    for i, line in enumerate(lines):
        color = colors[i % len(colors)]
        console.print(line, style=f"bold {color}")
    
    console.print()
    console.print("        [dim] AGREE-II Multi-Agent Appraisal System[/]")
    console.print()



def print_help():
    """打印帮助信息"""
    help_panel = Panel(
        """[bold]可用命令:[/]
  /help    - 显示此帮助
  /list    - 列出可处理的 PDF 文件
  /status  - 查看系统状态
  /agents  - 查看智能体团队
  /clear   - 清屏
  /exit    - 退出

[bold]直接对话:[/]
  输入任何文字即可与 AI 对话
  说「我想评价指南」开始处理 PDF""",
        title="💡 帮助",
        border_style="blue"
    )
    console.print(help_panel)


def print_agents_info():
    """打印智能体团队信息"""
    agents_table = Table(title="🤖 智能体团队", show_header=True, header_style="bold")
    agents_table.add_column("智能体", style="cyan")
    agents_table.add_column("角色")
    agents_table.add_column("职责")
    
    agents_table.add_row(
        "[bold cyan]Orchestrator[/]",
        "编排器",
        "协调整个评价流程"
    )
    agents_table.add_row(
        "[bold green]Initiator[/]",
        "规划师", 
        "扫描文档，生成评价计划"
    )
    agents_table.add_row(
        "[bold magenta]Appraiser[/]",
        "评价专家",
        "执行 AGREE-II 条目评价 (CoT)"
    )
    agents_table.add_row(
        "[bold blue]Reporter[/]",
        "报告员",
        "汇总评价，生成最终报告"
    )
    
    console.print(agents_table)


def show_pdf_list() -> list:
    """显示 PDF 文件列表并返回文件名列表"""
    result = list_input_pdfs()
    
    if result["status"] == "error":
        console.print(f"[red]错误: {result['error']}[/]")
        return []
    
    if result["count"] == 0:
        console.print(f"[yellow]📁 输入目录为空: {result['input_dir']}[/]")
        console.print("[dim]请将 PDF 文件放入 input/ 目录后重试[/]")
        return []
    
    table = Table(title=f"📁 可处理的 PDF 文件 ({result['count']} 个)")
    table.add_column("#", style="dim", width=4)
    table.add_column("文件名", style="cyan")
    table.add_column("大小", style="dim")
    
    file_names = []
    for i, f in enumerate(result["files"], 1):
        table.add_row(str(i), f["name"], f["size"])
        file_names.append(f["name"])
    
    console.print(table)
    return file_names


def print_agent_status(agent_name: str, status: str):
    """打印智能体工作状态"""
    color = AGENT_COLORS.get(agent_name, "white")
    console.print(f"[{color}]🤖 {agent_name}[/] {status}")


class WeaverCLI:
    """KG-Weaver 交互式 CLI"""
    
    def __init__(self):
        self.settings = get_settings()
        self.current_task_id: Optional[str] = None
        self.running = True
        self.pending_extraction: Optional[str] = None  # 待确认的评价文件
        self.parser = MinerUParser()
        
        # Spinner 控制
        self._spinner_running = False
        self._spinner_thread = None
        
        # Portal Agent 相关 (延迟初始化)
        self._portal_runner = None
        self._portal_session = None
    
    def _start_spinner(self, agent_name: str, args_text: str = ""):
        """启动后台 spinner 动画"""
        self._stop_spinner()  # 先停止之前的
        self._spinner_running = True
        
        # ANSI 颜色映射
        ANSI_COLORS = {
            "Initiator": "\033[92m",    # 绿色
            "Explorer": "\033[93m",     # 黄色
            "Extractor": "\033[95m",    # 紫色
            "Validator": "\033[94m",    # 蓝色
        }
        RESET = "\033[0m"
        
        def spin():
            spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            idx = 0
            args_display = f" ({args_text})" if args_text else ""
            color = ANSI_COLORS.get(agent_name, "")
            while self._spinner_running:
                char = spinner_chars[idx % len(spinner_chars)]
                status = f"\r{color}{char} {agent_name}{RESET} 处理中{args_display}    "
                sys.stdout.write(status)
                sys.stdout.flush()
                time.sleep(0.1)
                idx += 1
            # 清除当前行
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.flush()
        
        self._spinner_thread = threading.Thread(target=spin, daemon=True)
        self._spinner_thread.start()
    
    def _stop_spinner(self):
        """停止 spinner 动画"""
        if self._spinner_running:
            self._spinner_running = False
            if self._spinner_thread:
                self._spinner_thread.join(timeout=0.5)
            self._spinner_thread = None
    
    async def _get_portal_session(self):
        """获取或创建 Portal Agent 会话"""
        if self._portal_runner is None:
            portal_agent = create_portal_agent()
            self._portal_runner = runners.InMemoryRunner(
                agent=portal_agent,
                app_name='kg_weaver_portal',
            )
            self._portal_session = await self._portal_runner.session_service.create_session(
                app_name='kg_weaver_portal',
                user_id='cli_user'
            )
        return self._portal_runner, self._portal_session
    
    def run(self):
        """运行 CLI 主循环"""
        print_banner()
        console.print("[dim]你好！我是 AMA，输入 /help 查看帮助，或直接和我聊天[/]\n")
        
        while self.running:
            try:
                # 获取用户输入
                user_input = Prompt.ask("[bold cyan]>[/]").strip()
                
                if not user_input:
                    continue
                
                # 处理输入
                self.handle_input(user_input)
                
            except KeyboardInterrupt:
                self._stop_spinner()  # 停止 spinner 动画
                console.print("\n[dim]按 Ctrl+C 中断，输入 /exit 退出[/]")
            except EOFError:
                self.running = False
        
        console.print("\n[bold blue]👋 再见！[/]")
    
    def handle_input(self, user_input: str):
        """处理用户输入"""
        # 处理待确认的评价
        if self.pending_extraction:
            self.handle_extraction_confirmation(user_input)
            return
        
        if user_input.startswith("/"):
            self.handle_command(user_input)
        else:
            self.handle_chat(user_input)
    
    def handle_command(self, command: str):
        """处理命令"""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        if cmd == "/help":
            print_help()
        elif cmd == "/list":
            show_pdf_list()
        elif cmd == "/agents":
            print_agents_info()
        elif cmd == "/status":
            self.show_status()
        elif cmd == "/extract":
            if args:
                self.start_extraction(args)
            else:
                self.prompt_extraction()
        elif cmd == "/clear":
            console.clear()
            print_banner()
        elif cmd in ("/exit", "/quit", "/q"):
            self.running = False
        else:
            console.print(f"[red]未知命令: {cmd}[/]")
            console.print("[dim]输入 /help 查看帮助[/]")
    
    def handle_chat(self, message: str):
        """处理对话消息 - 使用意图识别"""
        # 检查意图
        intent = check_extraction_intent(message)
        
        if intent["has_intent"] and intent["confidence"] >= 0.3:
            # 用户可能想评价指南
            console.print(f"\n[dim]🔍 检测到评价意图 (关键词: {', '.join(intent['keywords_found'])})[/]")
            self.prompt_extraction()
        else:
            # 闲聊模式
            self.chat_response(message)
    
    def chat_response(self, message: str):
        """生成闲聊响应 - 使用 Portal Agent"""
        asyncio.run(self._chat_with_portal(message))
    
    async def _chat_with_portal(self, message: str):
        """异步调用 Portal Agent 进行对话"""
        try:
            runner, session = await self._get_portal_session()
            
            # 构建消息
            user_message = types.Content(
                role='user',
                parts=[types.Part(text=message)]
            )
            
            response_text = ""
            
            with console.status("[dim]思考中...[/]", spinner="dots"):
                async for event in runner.run_async(
                    user_id=session.user_id,
                    session_id=session.id,
                    new_message=user_message,
                ):
                    # 提取响应文本
                    if hasattr(event, 'content') and event.content:
                        if hasattr(event.content, 'parts'):
                            for part in event.content.parts:
                                if hasattr(part, 'text') and part.text:
                                    response_text = part.text.strip()
            
            # 过滤 <think>...</think> 标签内容
            if response_text:
                import re
                response_text = re.sub(r'<think>.*?</think>', '', response_text, flags=re.DOTALL).strip()
            
            if response_text:
                console.print(f"\n[bold blue]🏥 AMA[/]: {response_text}\n")
            else:
                # 降级到默认响应
                console.print(f"\n[bold blue]🏥 AMA[/]: 我理解了你说的「{message[:20]}{'...' if len(message) > 20 else ''}」。想评价指南吗？\n")
        
        except Exception as e:
            error_msg = str(e).lower()
            if any(kw in error_msg for kw in ["connection", "connect", "refused", "timeout", "api_key"]):
                console.print(f"\n[yellow]⚠️ 未连接到模型，使用离线模式[/]")
                console.print(f"[bold blue]🏥 AMA[/]: 你好！我是 AMA。目前模型未连接，但你可以使用 /list 查看文件或 /help 获取帮助。\n")
            else:
                console.print(f"\n[bold blue]🏥 AMA[/]: 我理解了，有什么我可以帮你的吗？\n")
    
    def prompt_extraction(self):
        """引导用户选择要评价的文件"""
        console.print("\n[bold]📋 让我看看有哪些文件可以处理...[/]\n")
        
        file_names = show_pdf_list()
        
        if not file_names:
            return
        
        console.print("\n[bold]请选择要处理的文件:[/]")
        console.print("[dim]输入文件编号或文件名，输入 'c' 取消[/]")
        self.pending_extraction = ",".join(file_names)  # 保存文件列表用于验证
    
    def handle_extraction_confirmation(self, user_input: str):
        """处理评价确认"""
        if user_input.lower() in ('c', 'cancel', '取消'):
            console.print("[dim]已取消[/]\n")
            self.pending_extraction = None
            return
        
        file_names = self.pending_extraction.split(",")
        selected_file = None
        
        # 尝试按编号选择
        if user_input.isdigit():
            idx = int(user_input) - 1
            if 0 <= idx < len(file_names):
                selected_file = file_names[idx]
        
        # 尝试按文件名选择
        if not selected_file:
            for name in file_names:
                if user_input.lower() in name.lower():
                    selected_file = name
                    break
        
        if selected_file:
            self.pending_extraction = None
            self.start_extraction(selected_file)
        else:
            console.print(f"[yellow]未找到匹配的文件: {user_input}[/]")
            console.print("[dim]请重新输入，或输入 'c' 取消[/]")
    
    def start_extraction(self, filename: str):
        """开始 AGREE-II 指南评价"""
        input_path = Path(self.settings.app.dir_input) / filename
        
        if not input_path.exists():
            console.print(f"[red]文件不存在: {input_path}[/]")
            return
        
        console.print(f"\n[bold green]🚀 开始处理: {filename}[/]\n")
        
        # Step 1: 初始化任务目录
        console.print("[dim]📁 初始化任务目录...[/]")
        task_result = init_task_directory(filename)
        if task_result["status"] == "error":
            console.print(f"[red]初始化失败: {task_result['error']}[/]")
            return
        
        self.current_task_id = task_result["task_id"]
        console.print(f"[green]✓[/] 任务ID: [cyan]{self.current_task_id}[/]")
        
        # Step 2: 解析 PDF (使用 MinerU VLM)
        console.print("\n[dim]📄 使用 MinerU VLM 解析 PDF...[/]")
        source_path = task_result["paths"]["source"]
        prep_dir = task_result["paths"]["prep_dir"]
        
        # MinerU 解析是异步的，需要等待完成
        with console.status("[bold cyan]⏳ MinerU 解析中，请稍候...[/]", spinner="dots"):
            parse_result = asyncio.run(self._parse_pdf_async(
                str(input_path), source_path, prep_dir
            ))
        
        if parse_result["status"] == "error":
            console.print(f"[red]PDF 解析失败: {parse_result['error']}[/]")
            return
        
        console.print(f"[green]✓[/] PDF 解析完成 ({parse_result['pages']} 页)")
        console.print(f"[dim]  source.md: {source_path}[/]")
        console.print(f"[dim]  中间文件: {prep_dir}[/]")
        
        # Step 3: 运行智能体团队
        console.print("\n[bold]🤖 启动智能体团队...[/]\n")
        asyncio.run(self.run_agents(filename))
    
    async def _parse_pdf_async(self, pdf_path: str, source_path: str, prep_dir: str) -> dict:
        """异步解析 PDF"""
        return await self.parser.parse_pdf_to_file(pdf_path, source_path, prep_dir)
    
    async def run_agents(self, filename: str):
        """
        异步运行智能体团队 - Orchestrator 通过 AgentTool 调用子 Agent
        """
        try:
            # 创建 Orchestrator
            orchestrator = create_orchestrator()
            
            # 创建 Runner
            runner = runners.InMemoryRunner(
                agent=orchestrator,
                app_name='kg_weaver',
            )
            
            # 创建 Session
            session = await runner.session_service.create_session(
                app_name='kg_weaver',
                user_id='cli_user'
            )
            
            # 构建初始消息
            prompt = f"""请开始处理 AGREE-II 指南评价任务。

任务信息：
- task_id: {self.current_task_id}
- 源文档已解析并保存到 source.md

请按照工作流程执行：
1. 调用 Initiator 生成评价计划
2. 调用 read_extraction_plan 读取计划
3. 根据计划中的阶段标记，调用对应的工具：
   - [APPRAISE:item_x] 标记 -> 调用 Appraiser
   - [REPORT] 标记 -> 调用 Reporter
4. 每个任务前后更新状态 (in_progress -> completed)

开始执行！"""
            
            initial_message = types.Content(
                role='user',
                parts=[types.Part(text=prompt)]
            )
            
            # 运行智能体
            console.print(f"[{AGENT_COLORS['Orchestrator']}]Orchestrator[/] 开始协调任务...\n")
            
            async for event in runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=initial_message,
            ):
                # 处理事件
                await self.handle_agent_event(event)
            
            console.print(f"\n[bold green]🎉 处理完成！[/]")
            console.print(f"[dim]输出目录: {self.settings.app.dir_output}/{self.current_task_id}[/]\n")
        
        except ConnectionError as e:
            console.print(f"\n[bold red]❌ 未连接到模型，请配置[/]")
            console.print(f"[dim]请检查 .env 文件中的 LLM_BASE_URL 配置[/]")
            console.print(f"[dim]错误详情: {e}[/]\n")
        except Exception as e:
            error_msg = str(e).lower()
            # 停止 spinner 动画
            self._stop_spinner()
            # 检测常见的连接错误
            if any(keyword in error_msg for keyword in [
                "connection", "connect", "refused", "timeout", 
                "unreachable", "network", "host", "resolve",
                "api_key", "apikey", "unauthorized", "401", "403"
            ]):
                console.print(f"\n[bold red]❌ 未连接到模型，请配置[/]")
                console.print(Panel(
                    """[bold]请检查以下配置:[/]
                    
1. LLM_BASE_URL - 模型 API 地址
2. LLM_API_KEY - API 密钥 (如需要)
3. LLM_MODEL_NAME - 模型名称

[dim]配置文件: .env[/]""",
                    title="⚙️ 配置提示",
                    border_style="yellow"
                ))
                console.print(f"[dim]错误详情: {e}[/]\n")
            else:
                console.print(f"\n[red]智能体执行出错: {e}[/]")
                import traceback
                console.print(f"[dim]{traceback.format_exc()}[/]")
    
    async def handle_agent_event(self, event):
        """处理智能体事件"""
        # 子代理名称列表（用于识别 AgentTool 调用）
        SUB_AGENTS = {"Initiator", "Appraiser", "Reporter"}
        
        try:
            # 检查事件类型
            if hasattr(event, 'content') and event.content:
                content = event.content
                
                # 检查是否有文本输出
                if hasattr(content, 'parts'):
                    for part in content.parts:
                        if hasattr(part, 'text') and part.text:
                            text = part.text.strip()
                            if text:
                                import re
                                
                                # 提取 <think> 标签中的内容
                                think_match = re.search(r'<think>(.*?)</think>', text, flags=re.DOTALL)
                                think_content = think_match.group(1).strip() if think_match else None
                                
                                # 提取正文（去除 think 标签后的内容）
                                normal_content = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
                                
                                # 处理未闭合的 <think> 开头（流式输出）
                                if text.startswith('<think>') and '</think>' not in text:
                                    # 去除开头标签，显示为思考内容
                                    think_content = text[7:].strip()
                                    normal_content = None
                                
                                # 处理只有 </think>（流式输出后半部分）
                                if '</think>' in text and '<think>' not in text:
                                    # 去除闭合标签和之前的内容
                                    normal_content = re.sub(r'^.*?</think>', '', text, flags=re.DOTALL).strip()
                                    think_content = None
                                
                                # 渲染思考内容（深灰色，带 💭 图标）
                                if think_content:
                                    console.print(f"[dim grey42]💭{think_content}[/]")
                                
                                # 渲染正文内容（正常颜色，带 💬 图标）
                                if normal_content:
                                    agent_name = self.detect_agent_from_text(normal_content)
                                    color = AGENT_COLORS.get(agent_name, "white")
                                    console.print(f"[{color}]💬{normal_content}[/]")
                        
                        # 检查工具调用
                        if hasattr(part, 'function_call') and part.function_call:
                            func_name = part.function_call.name
                            
                            # 判断是否为子代理调用
                            if func_name in SUB_AGENTS:
                                color = AGENT_COLORS.get(func_name, "cyan")
                                
                                # 构建显示文本
                                args_text = ""
                                if hasattr(part.function_call, 'args') and part.function_call.args:
                                    args = part.function_call.args
                                    if isinstance(args, dict):
                                        args_text = ", ".join([f"{k}={v}" for k, v in args.items()])
                                
                                # 启动后台 spinner
                                self._start_spinner(func_name, args_text)
                            else:
                                # 普通工具调用
                                console.print(f"[dim]调用工具: {func_name}[/]")
                        
                        # 检查工具响应
                        if hasattr(part, 'function_response') and part.function_response:
                            func_name = part.function_response.name
                            
                            # 判断是否为子代理完成
                            if func_name in SUB_AGENTS:
                                # 停止 spinner
                                self._stop_spinner()
                                color = AGENT_COLORS.get(func_name, "cyan")
                                console.print(f"[{color}]✅ {func_name}[/] 完成")
                            else:
                                # 普通工具完成
                                console.print(f"[dim]工具完成: {func_name}[/]")
                            
        except Exception as e:
            # 忽略处理单个事件的错误
            pass
    
    def detect_agent_from_text(self, text: str) -> str:
        """从文本尝试检测智能体"""
        text_lower = text.lower()
        
        if "initiator" in text_lower or "规划" in text or "计划" in text:
            return "Initiator"
        elif "appraiser" in text_lower or "评价" in text or "条目" in text:
            return "Appraiser"
        elif "reporter" in text_lower or "报告" in text or "汇总" in text:
            return "Reporter"
        else:
            return "Orchestrator"
    
    def show_status(self):
        """显示当前状态"""
        status_panel = Panel(
            f"""[bold]当前任务[/]: {self.current_task_id or '无'}
[bold]输入目录[/]: {self.settings.app.dir_input}
[bold]输出目录[/]: {self.settings.app.dir_output}""",
            title="📊 系统状态",
            border_style="blue"
        )
        console.print(status_panel)


def main():
    """主入口"""
    cli = WeaverCLI()
    cli.run()


if __name__ == "__main__":
    main()
