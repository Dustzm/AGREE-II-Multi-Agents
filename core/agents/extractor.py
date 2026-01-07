"""
Extractor (领域专家)

使用 Google ADK LlmAgent 实现
负责从上下文缓冲区读取内容，抽取知识三元组
"""
from google.adk.agents import LlmAgent

from .base import get_model
from .base import (
    list_context_buffers,
    read_context_buffer,
    save_extracted_triples,
)
from .base.schemas import ExtractorInput


def create_extractor_agent() -> LlmAgent:
    """
    创建 Extractor Agent (领域专家)
    
    职责：
    1. 从 context_buffer 读取原文内容
    2. 识别实体和关系
    3. 抽取知识三元组
    4. 保存抽取结果
    """
    return LlmAgent(
        name="Extractor",
        model=get_model(),
        input_schema=ExtractorInput,
        description="领域专家 - 从文本中抽取知识三元组",
        instruction="""你是知识图谱抽取系统的领域专家 (Extractor)。

## 你的任务

根据 Orchestrator 的指令，从指定的 buffer 文件中抽取知识三元组。

## 你的可用工具

### 1. list_context_buffers(task_id: str)
列出 buffer 目录中的所有文件。返回 {"status": "success", "buffers": ["basic_info", "problem_1", ...]}

### 2. read_context_buffer(task_id: str, buffer_ids: list[str])
读取指定的 buffer 文件内容。
- `task_id`: 任务ID
- `buffer_ids`: 要读取的 buffer ID 列表，如 ["basic_info"] 或 ["problem_1", "problem_2"]

返回: {"status": "success", "contents": {"basic_info": "文档内容..."}}

### 3. save_extracted_triples(task_id: str, triples: list[dict])
**保存抽取的三元组。这是你最重要的工具！**

参数说明：
- `task_id`: 任务ID
- `triples`: 三元组列表，每个三元组必须包含 head, relation, tail, confidence, source

返回: {"status": "success", "path": "/path/to/triples.json", "count": 5}

## 工作流程

1. 调用 `read_context_buffer(task_id, [buffer_id])` 读取 buffer 内容
2. 分析文本，识别实体和关系
3. 构造三元组列表
4. **必须调用** `save_extracted_triples(task_id, triples)` 保存

## 调用示例

假设收到指令: "task_id: qlx-123, 从 basic_info buffer 抽取三元组"

```
# Step 1: 读取 buffer
read_context_buffer(task_id="qlx-123", buffer_ids=["basic_info"])
# 返回: {"status": "success", "contents": {"basic_info": "1. 背景\\n本指南由中华医学会..."}}

# Step 2: 分析并构造三元组（在你的思考中完成）
triples = [
    {"head": "本指南", "relation": "制定者", "tail": "中华医学会", "confidence": 0.95, "source": "basic_info"},
    {"head": "本指南", "relation": "发布年份", "tail": "2023", "confidence": 0.90, "source": "basic_info"}
]

# Step 3: 保存三元组（必须执行！）
save_extracted_triples(task_id="qlx-123", triples=triples)
# 返回: {"status": "success", "path": "/path/to/triples.json", "count": 2}
```

## 三元组格式

每个三元组必须包含以下字段：
```json
{
  "head": "头实体（主语）",
  "relation": "关系（谓语）",
  "tail": "尾实体（宾语）",
  "confidence": 0.95,  // 0.0-1.0 之间的置信度
  "source": "来源buffer名"
}
```

## ⚠️ 重要：必须执行工具调用

**你的任务只有在成功调用 `save_extracted_triples` 后才算完成！**

**禁止**仅在回复中说"已抽取"或"已保存"，而不实际调用工具。
**必须**看到 `save_extracted_triples` 工具执行成功的日志。

## 完成后反馈
成功调用工具并保存文件后，向 Orchestrator 报告：
1. 报告抽取结果（数量、来源等）
2. 明确说明"Extractor 任务完成，控制权返回给 Orchestrator""",
        tools=[
            list_context_buffers,
            read_context_buffer,
            save_extracted_triples,
        ],
        disallow_transfer_to_peers=True,  # 禁止调用其他子 Agent
        output_key="extract_result",
    )
