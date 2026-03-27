# AMA (AGREE-Multi-Agents) 系统架构分析

## 概述

AMA 是一个基于 **Google ADK (Agent Development Kit)** 的多智能体系统，用于自动化执行 **AGREE-II 临床指南质量评价**。系统从 PDF 指南文档输入开始，经过预处理、智能体评价、报告生成等流程，最终输出结构化的评价报告。

---

## 系统流程图

流程图文件位置：`docs/ama_flowchart.drawio`

可使用 [draw.io](https://app.diagrams.net/) 或 VS Code 的 Draw.io 插件打开查看。

---

## 四层架构

### 1️⃣ 输入层 (Input Layer)

| 组件 | 说明 |
|------|------|
| **PDF 指南文档** | 放置在 `input/` 目录下的 PDF 文件 |
| **用户输入** | 通过 CLI (`cli.py`) 或 Web 界面与系统交互 |
| **Skills 配置** | 23 个 AGREE-II 条目配置文件 (`core/skills/item_*.yaml`) |

### 2️⃣ 预处理层 (Preprocessing Pipeline)

预处理流程将 PDF 文档转换为结构化的语义证据池，为后续智能体评价提供上下文。

```
PDF → MinerU Parser → DocIndex → Chunker → Labeler → EvidencePool
```

| 组件 | 文件位置 | 功能 |
|------|----------|------|
| **MinerU Parser** | `core/parsers/mineru.py` | PDF 解析，生成 `content_list.json` 和 `model.json` |
| **DocIndex** | `docindex/` | 构建文档结构树，分析标题层级 (`cluster_level`) |
| **DocumentChunker** | `core/preprocessor/chunker.py` | 标题-内容块切分，计算 section 路径 |
| **Explorer** | `core/agents/explorer.py` | 附件检测与提取 |
| **SemanticLabeler** | `core/preprocessor/labeler.py` | LLM 语义分类（Overview/Method/Recommendation 等） |
| **EvidencePool** | `core/preprocessor/evidence.py` | 证据池构建，建立领域-标签映射索引 |
| **VisualEvidence** | `core/preprocessor/visual_evidence.py` | 可视化证据生成，用于前端渲染 |

#### 语义标签类型

```python
class SemanticLabel(Enum):
    OVERVIEW = "overview"              # 概述/背景
    EXECUTIVE_SUMMARY = "executive_summary"  # 执行摘要
    METHOD = "method"                  # 方法/流程
    RECOMMENDATION = "recommendation"  # 推荐建议
    APPENDIX = "appendix"              # 附录
    OTHERS = "others"                  # 其他
    IRRELEVANT = "irrelevant"          # 无关内容
```

#### 领域-标签映射

| 领域 | 相关语义标签 |
|------|-------------|
| Domain 1 (Scope & Purpose) | Overview, Executive Summary, Method |
| Domain 2 (Stakeholder Involvement) | Overview, Executive Summary, Method, Appendix |
| Domain 3 (Rigour of Development) | Method, Recommendation, Appendix |
| Domain 4 (Clarity of Presentation) | Executive Summary, Recommendation |
| Domain 5 (Applicability) | Recommendation, Appendix |
| Domain 6 (Editorial Independence) | Overview, Others, Appendix |

### 3️⃣ 智能体编排层 (Agent Orchestration)

基于 Google ADK 实现的多智能体协作系统。

```
Portal → Orchestrator → [Appraiser × 23] → Reporter
```

| 智能体 | 文件位置 | 功能 |
|--------|----------|------|
| **Portal Agent** | `core/agents/portal.py` | 用户交互，意图识别 |
| **Orchestrator** | `core/agents/orchestrator.py` | 智能编排器，协调子智能体 |
| **IterativeEvaluator** | `core/agents/appraiser.py` | 迭代评价器，逐条目评价 |
| **Reporter** | `core/agents/reporter.py` | 汇总评价结果，生成最终报告 |

#### IterativeEvaluator 评价流程

```
┌─────────────────────────────────────────────────────────────┐
│                    IterativeEvaluator                       │
├─────────────────────────────────────────────────────────────┤
│  1️⃣ load_evidence_for_skill  → 加载领域相关证据             │
│                     ↓                                       │
│  2️⃣ _extract_evidence_batch  → 迭代遍历 chunks 提取证据     │
│                     ↓                                       │
│  3️⃣ _score_current           → 根据累积证据动态评分         │
│                     ↓                                       │
│  4️⃣ _synthesize_report       → 综合生成最终评价报告         │
└─────────────────────────────────────────────────────────────┘
```

#### 并发批量评价

`appraise_all` 工具使用 `asyncio.Semaphore` 控制并发，一次性评价所有 23 个条目：

```python
# appraiser_batch.py
async def appraise_all(task_id: str, skill_ids: List[str] = None, ...):
    semaphore = asyncio.Semaphore(max_concurrency)  # 从 .env 读取
    tasks = [evaluate_item(skill_id) for skill_id in skill_ids]
    results = await asyncio.gather(*tasks)
```

### 4️⃣ 输出层 (Output Layer)

| 输出文件 | 位置 | 说明 |
|----------|------|------|
| **prep/** | `output/{task_id}/prep/` | 预处理中间文件 |
| `*_content_list.json` | prep/ | MinerU 解析结果 |
| `*_model.json` | prep/ | MinerU 模型文件 |
| `*_docindex.json` | prep/ | 文档结构索引 |
| `*_evidence_pool.json` | prep/ | 语义证据池 |
| `*_visual_evidence.json` | prep/ | 可视化证据 |
| **evaluations/** | `output/{task_id}/.ama/evaluations/` | 各条目评价结果 |
| `item_1.json` ~ `item_23.json` | evaluations/ | 23 个条目的详细评价 |
| **appraisal_report.json** | `output/{task_id}/` | 最终 JSON 报告 |
| **appraisal_report.md** | `output/{task_id}/` | 最终 Markdown 报告 |
| **token_usage.json** | `output/{task_id}/.ama/` | Token 使用统计 |

---

## 核心数据结构

### TitleContentBlock (标题-内容块)

```python
@dataclass
class TitleContentBlock:
    title: str                    # 标题文本
    title_page_idx: int           # 标题所在页码
    contents: List[Dict[str, Any]] # 内容项列表
    raw_text: str                 # 原始文本
    section: str                  # 章节路径 (如 "1.Introduction > 1.1.Background")
    is_appendix: bool = False     # 是否为附件
```

### SemanticChunk (语义块)

```python
@dataclass
class SemanticChunk:
    chunk_id: str                 # 唯一标识
    title: str                    # 标题
    content: str                  # 内容
    label: SemanticLabel          # 语义标签
    start_page: int               # 起始页
    end_page: int                 # 结束页
    reasoning: str                # LLM 分类理由
    block_ids: List[str]          # 关联的 block ID
    section: str                  # 章节路径
```

### 评价结果结构

```json
{
  "skill_id": "item_7",
  "title": "Systematic methods were used to search for evidence",
  "chain_of_thought": {
    "step1_locate": "证据来源位置描述",
    "step3_reasoning": {
      "summary": "一句话总结",
      "reasons": [
        {"point": "要点名称", "explanation": "详细解释", "evidence_chunks": ["chunk_1"]}
      ],
      "score_justification": "评分判定理由"
    },
    "score": 5
  },
  "evidence": ["证据1", "证据2", ...],
  "timestamp": "2026-01-20T10:30:00"
}
```

### 最终报告结构

```json
{
  "guideline_title": "指南标题",
  "evaluator": "AMA (AGREE-Multi-Agents)",
  "evaluation_date": "2026-01-20 10:30:00",
  "task_id": "xxx",
  "domain_scores": {
    "1": {"name": "Scope and Purpose", "score": 83.3, "items": [...]},
    "2": {"name": "Stakeholder Involvement", "score": 66.7, ...},
    ...
  },
  "overall_assessment": {
    "strengths": ["优势1", "优势2"],
    "weaknesses": ["不足1", "不足2"],
    "recommendation": "Recommend with modifications"
  },
  "total_raw_score": 108,
  "overall_quality_score": 5
}
```

---

## 技术栈

| 类别 | 技术 |
|------|------|
| **智能体框架** | Google ADK (Agent Development Kit) |
| **LLM 调用** | LiteLLM (支持多模型切换) |
| **PDF 解析** | MinerU (远程服务) / PyPDF (本地回退) |
| **并发控制** | asyncio.Semaphore |
| **Web 后端** | FastAPI + WebSocket |
| **Web 前端** | React + TypeScript + Ant Design |
| **配置管理** | Pydantic Settings + YAML |

---

## 关键配置

### 环境变量 (.env)

```bash
# LLM 配置
LLM_MODEL=gemini/gemini-2.5-flash
LLM_API_KEY=xxx

# MinerU 服务
MINERU_SERVER_URL=http://xxx:8010

# 并发配置
APPRAISAL_MAX_CONCURRENCY=10

# 语言设置
PROMPT_LANG=zh
SKILL_LANG=en
```

### Skills 配置 (item_*.yaml)

```yaml
id: item_7
name: Systematic search
name_zh: 系统性检索
domain: 3
enabled: true

criteria:
  - Named the electronic databases searched
  - Provided time periods searched
  - Described search strategy
  ...

scoring_guide:
  score_1: "No information about search strategy"
  score_7: "Comprehensive search strategy provided"
  ...
```

---

## 运行方式

### CLI 模式

```bash
python cli.py
```

### Web 模式

```bash
./service.sh start
```

访问 `http://localhost:3000` 使用 Web 界面。

### 批量评价 (Benchmark)

```bash
python cli.py benchmark --batch-name batch_1 --rounds 3
```

---

## 目录结构

```
AGREE-II-Multi-Agents/
├── core/
│   ├── agents/           # 智能体定义
│   │   ├── portal.py     # 门户智能体
│   │   ├── orchestrator.py # 编排器
│   │   ├── appraiser.py  # 迭代评价器
│   │   ├── reporter.py   # 报告员
│   │   └── tools/        # 工具函数
│   ├── preprocessor/     # 预处理模块
│   │   ├── chunker.py    # 文档分块
│   │   ├── labeler.py    # 语义标签
│   │   └── evidence.py   # 证据池
│   ├── prompts/          # 提示词模板
│   ├── skills/           # AGREE-II 条目配置
│   └── parsers/          # PDF 解析器
├── web/
│   ├── backend/          # FastAPI 后端
│   └── frontend/         # React 前端
├── input/                # PDF 输入目录
├── output/               # 评价结果输出
├── docs/                 # 文档
│   ├── ama_flowchart.drawio  # 流程图
│   └── AMA_SYSTEM_ARCHITECTURE.md  # 本文档
└── cli.py                # 命令行入口
```

---

## 总结

AMA 系统通过以下核心流程实现 AGREE-II 指南自动评价：

1. **预处理**: PDF → 结构化语义证据池
2. **智能编排**: Orchestrator 协调 23 个并发评价任务
3. **迭代评价**: 逐 chunk 提取证据 → 动态评分 → 综合报告
4. **结果汇总**: 6 维度标准化分数 + 推荐意见

整个系统设计遵循模块化、可扩展的原则，支持多语言、多模型配置，适用于大规模临床指南质量评价任务。
