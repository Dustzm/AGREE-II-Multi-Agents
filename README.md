# AMA (AGREE-Multi-Agents)

> 多智能体 AGREE-II 指南质量评价系统

## 使用手册

- Web 端用户使用说明见 [docs/USER_MANUAL.md](docs/USER_MANUAL.md)

## 项目简介

AMA 是一个基于多智能体架构的临床实践指南质量评价系统。使用 AGREE-II 工具对指南进行系统化评价，通过模块化的 Skill 机制实现 23 个评价条目的独立评估。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    控制平面 (Control Plane)                   │
│                                                              │
│                      ┌──────────────┐                       │
│                      │ Orchestrator │                       │
│                      └──────────────┘                       │
│                             │                               │
│         ┌───────────────────┼───────────────────┐          │
│         ▼                   ▼                   ▼          │
│    ┌──────────┐       ┌──────────┐       ┌──────────┐      │
│    │Initiator │       │Appraiser │       │ Reporter │      │
│    │ (规划师) │       │(评价专家)│       │ (报告员) │      │
│    └──────────┘       └──────────┘       └──────────┘      │
│                             │                               │
│                      Skill 模块化                           │
│         ┌───────────────────┼───────────────────┐          │
│         ▼                   ▼                   ▼          │
│     [item_1]           [item_2]           [item_3]  ...   │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    数据平面 (Data Plane)                     │
│                                                              │
│  ┌─────────┐  ┌─────────┐  ┌──────────────┐  ┌──────────┐ │
│  │source.md│  │ plan.md │  │ evaluations/ │  │ report/  │ │
│  │(源文档) │  │(评价计划)│  │ (条目评价)  │  │(最终报告)│ │
│  └─────────┘  └─────────┘  └──────────────┘  └──────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 核心特性

- **AGREE-II 标准评价**: 支持 6 个维度、23 个条目的系统化评价
- **Chain of Thought**: 每个条目采用 CoT 四步法评价（定位→证据→推理→评分）
- **Skill 模块化**: 评价条目以 YAML 配置形式独立定义，易于扩展
- **维度分数计算**: 自动按 AGREE-II 官方公式计算标准化分数
- **Web 管理平台**: 提供可视化的评价任务管理和报告查看界面

## 快速开始

### 1. 环境准备

```bash
# 创建 conda 环境
conda create -n ama python=3.11
conda activate ama

# 安装依赖
pip install -r requirements.txt

# 安装前端依赖
cd web/frontend && npm install
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 配置 LLM 和 MinerU 服务地址
```

主要配置项：

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `LLM_BASE_URL` | 大模型 API 地址 | http://localhost:8080/v1 |
| `LLM_API_KEY` | API Key | - |
| `LLM_MODEL_NAME` | 模型名称 | qwen2.5 |
| `MINERU_SERVER_URL` | MinerU VLM 服务地址 | http://127.0.0.1:30000/ |

### 3. 启动服务

#### 方式一：一键启动 (推荐)

```bash
# 启动所有服务
./service.sh start

# 停止所有服务
./service.sh stop

# 重启所有服务
./service.sh restart

# 查看服务状态
./service.sh status
```

#### 方式二：手动启动

```bash
# 启动后端 API 服务 (端口: 8011)
uvicorn web.backend.app.main:app --host 0.0.0.0 --port 8011 --reload

# 启动批量任务 Worker
python -m web.backend.app.batch_worker

# 启动前端开发服务器 (端口: 8012)
cd web/frontend && npm run dev
```

### 4. 访问地址

| 服务 | 地址 |
|------|------|
| **Web 管理平台** | http://localhost:8012/ama |
| **后端 API** | http://localhost:8011 |
| **API 文档 (Swagger)** | http://localhost:8011/docs |

## AGREE-II 评价维度

| 维度 | 条目数 | 说明 |
|------|--------|------|
| Domain 1 | 3 | 范围和目的 (Scope and Purpose) |
| Domain 2 | 3 | 利益相关者参与 (Stakeholder Involvement) |
| Domain 3 | 8 | 制定的严谨性 (Rigour of Development) |
| Domain 4 | 3 | 表达的清晰性 (Clarity of Presentation) |
| Domain 5 | 4 | 应用性 (Applicability) |
| Domain 6 | 2 | 编辑独立性 (Editorial Independence) |

## 项目结构

```
AMA/
├── core/                     # 核心智能体逻辑
│   ├── agents/               # 智能体实现
│   │   ├── orchestrator.py   # 主编排器
│   │   ├── initiator.py      # 规划师智能体
│   │   ├── appraiser.py      # 评价专家智能体
│   │   └── reporter.py       # 报告员智能体
│   ├── config/               # 配置管理
│   │   └── settings.py       # Pydantic 设置 (读取 .env)
│   ├── parsers/              # PDF 解析器 (MinerU 集成)
│   ├── prompts/              # 智能体提示词模板
│   └── skills/               # AGREE-II 条目配置 (YAML)
│       ├── item_1.yaml
│       ├── item_2.yaml
│       └── ...               # 共 23 个条目
├── web/
│   ├── backend/              # FastAPI 后端
│   │   └── app/
│   │       ├── main.py       # FastAPI 入口
│   │       ├── api/          # API 路由
│   │       └── services/     # 业务逻辑
│   └── frontend/             # React + Vite 前端
│       └── src/
│           ├── pages/        # 页面组件
│           └── components/   # 通用组件
├── service.sh                    # 服务管理脚本
├── input/                    # 输入 PDF 目录
├── output/                   # 输出目录
│   └── {task_id}/
│       ├── source.md         # 解析后的文档
│       └── .ama/
│           ├── plan.md       # 评价计划
│           ├── evaluations/  # 条目评价结果
│           └── report/       # 最终报告
└── .env                      # 环境配置
```

## 技术栈

### 后端

| 组件 | 技术 | 说明 |
|------|------|------|
| **智能体框架** | Google ADK | 多智能体开发框架 |
| **LLM 集成** | LiteLLM | OpenAI 兼容 API 抽象层 |
| **Web 框架** | FastAPI | 异步 REST API 服务 |
| **PDF 解析** | MinerU | VLM HTTP Client 模式 |
| **数据验证** | Pydantic v2 | 模式和配置验证 |

### 前端

| 组件 | 技术 | 版本 |
|------|------|------|
| **框架** | React | 19.x |
| **构建工具** | Vite | 7.x |
| **语言** | TypeScript | 5.x |
| **样式** | TailwindCSS | 4.x |
| **路由** | react-router-dom | 7.x |

## 开发计划

- [x] 项目架构设计
- [x] Skill 模块化机制
- [x] Appraiser 智能体 (CoT 评价)
- [x] Reporter 智能体 (维度分数计算)
- [x] 完整 23 条目配置
- [x] 前端 Web 管理平台
- [ ] 端到端测试
- [ ] 多评价者支持

## 模型上下文窗口与失败恢复

在管理台「配置」中填写部署服务实际支持的**上下文窗口（tokens）**。也可在未被运行时配置覆盖的环境中设置 `LLM_CONTEXT_WINDOW=131072`。未配置时，新评价会在调用模型前给出配置错误；历史报告仍可读取。

- 模型 ID 精确匹配本地 models.dev 快照时自动填写建议值；同名模型存在冲突或无法匹配时手动填写。建议值可以修改，保存后的值不会随目录更新自动改变。
- 切换 provider、模型或服务地址会重新确认窗口；正在执行的文档使用启动时的配置快照。第三方网关和自部署服务不会套用官方服务的输入／输出上限。
- 模型请求统一经过 `core/llm/gateway.py`（参数、预算、并发闸门、用量统计）；`context.py` 负责完整请求预算，`json_output.py` 负责有限语法兼容及截断检测，`recovery.py` 提供条目共享的恢复额度、唯一的重试循环和超限拆分 `split_on_overflow`。业务阶段只提供拆分与合并规则，并保留来源及索引。
- 评价编排在 `core/pipeline/`：失败以 `FailureCode` 表达，Web、批量和 MCP 各自映射为界面文案、状态原因或外部错误码，不解析错误文字。
- 输入估算包含提示、输出预留、工具定义和历史；未知 tokenizer 使用保守估算，图片另留预算。估算无法保证与所有服务的计费／视觉 tokenizer 完全一致。不可再拆分的评分输入超限会明确失败，不静默丢弃证据。
- 每条目最多共享三次额外恢复机会；传输、JSON 修复、缺失判定补齐共用额度。缺失判定、无效引用和解析失败不会转换为正常低分。指南缺少条目时保留 partial 状态。

目录由应用离线读取；更新快照时需同时记录来源、抓取时间和上游许可说明，保存在同一个 JSON 文件中。

本地回归可运行 `python -m pytest -q web/backend/tests/test_llm_budget.py web/backend/tests/test_appraisal_recovery.py`。真实模型端到端评价需在模型资源可用后单独验证。

## License

MIT
