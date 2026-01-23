# Tech Stack

## 编程语言
- **Python**: >=3.11 (核心业务逻辑、多智能体框架、文档解析)
- **TypeScript**: 用于前端界面开发，确保类型安全。

## 后端技术
- **FastAPI**: 核心 Web 框架，提供高性能异步 API。
- **Uvicorn**: ASGI 服务器，用于运行 FastAPI 应用。
- **Pydantic**: 用于数据验证和设置管理。

## AI 与 多智能体
- **Google ADK & LiteLLM**: 用于多模型适配和 OpenAI 兼容接口调用。
- **AMA Agent Framework**: 项目自定义的多智能体协作框架（Orchestrator, Appraiser 等）。

## 文档解析与处理
- **MinerU**: 深度 PDF 解析与内容提取。
- **PyPDF & PDFPlumber**: 基础 PDF 操作。
- **Office Suite**: `python-docx` (Word), `openpyxl` (Excel), `python-pptx` (PPTX)。

## 前端技术
- **React**: 核心前端 UI 框架。
- **Tailwind CSS / UI 组件库**: 实现医疗专业感的界面设计。

## 其他工具
- **Dotenv**: 环境变量管理。
- **Rich**: 用于美化 CLI 输出。
- **Httpx**: 用于异步 HTTP 请求（如调用 MinerU API）。
