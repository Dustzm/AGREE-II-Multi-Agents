# AMA (AGREE II Multi-Agents)

[English](README.md) | **简体中文**

AMA 用 AGREE II 工具评价临床实践指南（PDF）。多智能体流水线解析指南、为 23 个条目评分并生成报告，报告中的每个判断都能回到它所依据的原文句子。

## 功能

- **完整覆盖 AGREE II。** 6 个领域、23 个条目，领域标准化分数按 AGREE II 官方公式计算。
- **三种评价引擎。** `llm` 通过迭代阅读为条目评分。`rule` 先抽取结构化事实，再用条目规则评分。`hybrid`（默认）用训练好的选路模型为每个条目在两条路径中选择一条。
- **证据可追溯。** 报告引用原文句子，并在原始 PDF 中定位。
- **条目可配置。** 每个条目是一个 YAML Skill（`core/skills/item_<n>.yaml`），包含评分原则，可在 Web 界面中编辑。
- **Web 平台。** 单篇评价与实时进度、批量评价、报告浏览、与人工评分对比、模型配置、用户管理。界面支持英文和中文。
- **MCP 端点。** 其他智能体可以通过 Model Context Protocol 提交 PDF 并获取评价结果。

## 工作流程

```
PDF ──► 解析 ──► 证据池 ──► 条目评价（23 个条目） ──► 报告
        MinerU   分块、标签、   hybrid 选路                Reporter
                 附件          ├─ 规则引擎                 领域分数、
                               └─ LLM 评价器               引用证据
```

1. **解析。** MinerU 把 PDF 转成结构化 Markdown，结果按文档缓存。
2. **证据池。** 预处理器把文档切成片段，按 AGREE II 领域打标签，构建标题层级，并读取附件（PDF、Word、Excel、PowerPoint、URL）。
3. **条目评价。** 条目并发执行。`hybrid` 模式下，选路模型为每个条目选择规则引擎或 LLM 评价器。条目 17 固定走 LLM 路径。规则路径失败时可以回退到 LLM 路径，并标记为 degraded。
4. **报告。** Reporter 计算领域分数，写出 `appraisal_report.json` 和 `appraisal_report.md`。

流水线位于 `core/pipeline/`，对外只发事件。Web 界面、批量 Worker 和 MCP 服务是同一条流水线的三个入口。

## 快速开始

### 环境要求

- Python 3.11，Node.js 20 或更高版本
- 一个大模型服务。支持的提供商：vLLM、OpenAI 兼容接口、OpenAI、Anthropic、DeepSeek、Gemini、DashScope、智谱
- 一个用于解析 PDF 的 [MinerU](https://github.com/opendatalab/MinerU) VLM 服务

### 1. 安装

```bash
conda create -n ama python=3.11
conda activate ama
pip install -r requirements.txt

cd web/frontend && npm install
```

### 2. 配置

```bash
cp .env.example .env
```

| 变量 | 说明 |
|---|---|
| `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL_NAME` | 评价模型，也可以在“配置”页面设置 |
| `LLM_CONTEXT_WINDOW` | 所部署模型的上下文窗口（tokens），也可以在“配置”页面设置 |
| `MINERU_SERVER_URL`、`MINERU_API_KEY` | MinerU VLM 服务 |
| `ANALYSIS_ENGINE` | `hybrid`（默认）、`rule` 或 `llm` |
| `AMA_AUTH_ENABLED` | Web 登录，默认 `true` |
| `AMA_AUTH_SECRET`、`LLM_CONFIG_SECRET` | 会话签名密钥和模型配置加密密钥，生产环境两者都要设置 |
| `AMA_MCP_ENABLED`、`AMA_MCP_API_KEY` | MCP 端点，默认关闭 |

`.env.example` 还包含并发上限、规则引擎策略和 MCP 限制。

启用登录时，用种子文件创建首批账号。数据库为空且首次启动时，AMA 会导入项目根目录下的 `users.json`：

```json
{
  "users": [
    { "username": "admin", "password": "<你的密码>", "display_name": "Admin", "role": "admin" }
  ]
}
```

角色为 `admin` 和 `user`。密码以哈希形式保存在 `data/ama.db`。不要把 `users.json` 提交到版本库。之后可以在“用户”页面添加更多账号。

### 3. 启动

```bash
./service.sh start     # 后端、批量 Worker、MCP Worker（启用时）、前端
./service.sh status
./service.sh stop
./service.sh restart
```

脚本要求存在名为 `ama` 的 conda 环境。也可以手动启动各进程：

```bash
uvicorn web.backend.app.main:app --host 0.0.0.0 --port 8011
python -m web.backend.app.batch_worker
cd web/frontend && npm run dev
```

| 服务 | 地址 |
|---|---|
| Web 界面 | http://localhost:8012/ama |
| API 文档（Swagger） | http://localhost:8011/ama/api-docs |
| 健康检查 | http://localhost:8011/ama/health |

### 4. 第一次评价

1. 打开**配置**，确认模型可达且已填写上下文窗口。
2. 打开**评价**，上传或选择一份指南 PDF 并开始。
3. 打开**报告**，查看分数和引用的证据。

各页面的用法见[用户手册](docs/USER_MANUAL.md)。

## Docker

`docker-compose.yml` 使用预先构建的镜像，请先构建：

```bash
docker build -f web/backend/Dockerfile -t ama-backend:0.1 .
docker build -t ama-frontend:0.1 web/frontend
docker compose up -d
```

前端地址为 http://localhost:8012/ama ，后端端口为 8011。加上 `--profile mcp` 可以启动 MCP Worker。

## MCP

设置 `AMA_MCP_ENABLED=true` 和 `AMA_MCP_API_KEY` 后，后端在 8011 端口提供 Streamable HTTP 端点 `/ama/mcp/` 和上传端点 `/ama/mcp/uploads/`。两者都使用 `Authorization: Bearer <API key>`。

| 工具 | 用途 |
|---|---|
| `submit_pdf_appraisal` | 提交一份 PDF 进入评价队列 |
| `get_appraisal_status` | 查询进度 |
| `get_appraisal_result` | 获取完成的报告 |
| `cancel_appraisal` | 取消排队中或运行中的任务 |

详见 [docs/AMA_MCP_Integration_Guide.pdf](docs/AMA_MCP_Integration_Guide.pdf)。

## AGREE II 领域

| 领域 | 条目 | 名称 |
|---|---|---|
| 1 | 1–3 | 范围和目的（Scope and Purpose） |
| 2 | 4–6 | 参与人员（Stakeholder Involvement） |
| 3 | 7–14 | 制定的严谨性（Rigour of Development） |
| 4 | 15–17 | 表达的清晰性（Clarity of Presentation） |
| 5 | 18–21 | 应用性（Applicability） |
| 6 | 22–23 | 编辑的独立性（Editorial Independence） |

## 模型上下文窗口与失败恢复

- 新评价需要所部署模型的上下文窗口，可在“配置”页面或通过 `LLM_CONTEXT_WINDOW` 设置。模型 ID 与内置模型目录匹配时会自动填入建议值。
- 正在执行的文档沿用启动时的模型配置。
- 所有模型请求都经过 `core/llm/gateway.py`，由它处理 token 预算、并发上限和用量统计。超出窗口的输入按各阶段的规则拆分与合并。无法再拆分的输入会明确失败，不会静默丢弃证据。
- 每个条目有一份恢复额度，由传输重试、截断重试和 JSON 修复共用（`LLM_RETRY_QUOTA_*`，默认合计 8 次）。
- 缺失判定、无效引用和解析失败都按失败上报，不会被转换成低分。失败以 `core/pipeline/` 中的 `FailureCode` 表达，各入口再映射为自己的文案。

## 项目结构

```
core/                  评价引擎，与 Web 层无关
  pipeline/            一次评价的编排：解析 → 证据 → 条目 → 报告
  agents/              Appraiser、Reporter、Explorer（附件）、Portal（对话）
  rule_engine/         事实抽取、校验与条目评分规则
  hybrid_router/       按条目在规则引擎与 LLM 路径之间选择
  skills/              AGREE II 23 个条目的定义（YAML）
  llm/                 网关、上下文预算、结构化输出、恢复
  preprocessor/        分块、领域标签、证据池
  attachment/          附件与 URL 解析
  parsers/  prompts/  config/  runtime/  observability/  utils/
web/backend/           FastAPI 服务（端口 8011，前缀 /ama），SQLite 位于 data/ama.db
web/frontend/          React 19 + Vite + Tailwind CSS（端口 8012，base /ama/）
mcp/                   MCP 服务、上传端点与 Worker
mineru/  mineru_vl_utils/   解析 PDF 的 MinerU 客户端
docindex/              标题层级识别
docvlm/                独立的 VLM PDF 解析工具（命令行）
benchmark/             与人工评分的一致性指标
docs/                  用户手册与设计说明
service.sh             本地服务的启动、停止与状态
docker-compose.yml     容器部署
```

每个任务写入 `output/<文件名>-<时间戳>/`：

```
source.md                解析后的指南
appraisal_report.json    最终报告
appraisal_report.md
.ama/prep/               证据池与预处理结果
.ama/evaluations/        各条目的评价结果
```

## 技术栈

| 层 | 技术 |
|---|---|
| 智能体 | Google ADK、LiteLLM |
| 后端 | FastAPI、Pydantic v2、SQLite |
| PDF 解析 | MinerU（VLM HTTP 客户端） |
| 选路模型 | scikit-learn |
| 集成 | Model Context Protocol |
| 前端 | React 19、TypeScript 5、Vite 7、Tailwind CSS 4、react-router 7、i18next |

## 测试

```bash
python -m pytest -q web/backend/tests
cd web/frontend && npm test
```

测试不会调用真实模型。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md)、[docs/USER_MANUAL_EN.md](docs/USER_MANUAL_EN.md) | 中文与英文用户手册 |
| [docs/AMA_MCP_Integration_Guide.pdf](docs/AMA_MCP_Integration_Guide.pdf) | MCP 接入 |
| [docs/I18N.md](docs/I18N.md) | 界面消息与语言规则 |
| [docs/STRUCTURED_OUTPUT.md](docs/STRUCTURED_OUTPUT.md) | 模型结构化输出 |
| [docs/RULE_ENGINE_RELIABILITY_PLAN.md](docs/RULE_ENGINE_RELIABILITY_PLAN.md) | 规则引擎的失败处理 |
| [docs/ARCHITECTURE_OPTIMIZATION_PLAN.md](docs/ARCHITECTURE_OPTIMIZATION_PLAN.md) | 架构与性能设计 |

## License

[MIT](LICENSE)
