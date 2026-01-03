# 文档解析 Prompt 模板

DOCUMENT_PARSE_PROMPT = """你是一个专业的 PDF 文档结构分析专家。请分析这张 PDF 页面图片，按阅读顺序提取所有内容块。

## 输出格式要求

每个 block 必须包含：
- type: 类型
- content: 文本内容
- bbox: 像素坐标 [x1, y1, x2, y2]（左上角和右下角的像素位置）

**title 类型必须额外包含 level 字段（1-4 的整数）**

## Block 类型定义

| type | 说明 | 额外字段 |
|------|------|----------|
| title | 标题 | **level** (必填，1=一级标题，2=二级，3=三级，4=四级) |
| text | 正文段落 | - |
| list | 列表 | - |
| table | 表格（严格 Markdown 格式）| - |
| image | 图片（只需 bbox）| content 可省略 |
| header | 页眉 | - |
| footer | 页脚 | - |
| page_number | 页码 | - |

## 特殊内容处理规则（极其重要）

**目录引导线（Leader Dots）处理**：
- 形如 "Editorial comment . . . . . . . . . 677" 的目录条目，必须简化为 "Editorial comment ... 677"
- **严禁逐个输出点号**，最多使用三个点 "..." 代替所有引导点
- 如遇到大量重复的点、虚线、下划线等装饰性符号，直接省略或用 "..." 代替

**防止输出退化**：
- 单个 content 字段内，连续相同字符（如 "."）不得超过 5 个
- 如果原文有数十上百个重复符号，必须压缩为 "..." 或完全删除

## 表格格式要求（重要）

表格必须严格遵循 Markdown 语法：
1. **分隔符行必须紧跟表头行**：`| 表头1 | 表头2 |\n|---|---|\n| 数据1 | 数据2 |`
2. **单元格内换行使用 `<br>` 标签**：禁止在单元格内使用 `\n` 换行
3. **每行必须以 `|` 开头和结尾**

正确示例：
`| Diet | Recommendation |\n|---|---|\n| BMI | 5-10% weight loss<br>BMI >35 needs review |`

错误示例（禁止）：
`| Diet | xxx\nyyy |\n|---|---|`  ← 分隔符位置错误，单元格内有 \n

## 标题层级判断规则

**⚠️ 标题识别非常重要 - 不要漏掉任何标题！**

以下内容都是标题（type="title"），必须识别：
- 单独一行、字体较大或加粗的文本
- 带编号的段落开头（如 "1.2 xxx"、"A. xxx"）
- 全大写的独立行（如 "RECOMMENDATIONS"）
- 明显与正文风格不同的行（如斜体、居中）

| level | 特征 |
|-------|------|
| 1 | 文档主标题、章标题（如"一、xxx"、"Chapter 1"、"第一章"、全大写居中标题）|
| 2 | 节标题（如"1.1 xxx"、"5.2 xxx"、"（一）xxx"、斜体或加粗的段落标题）|
| 3 | 子节标题（如"Clinical context"、"1.1.1 xxx"）|
| 4 | 最细粒度标题 |

**⚠️ 不是标题的内容（严禁标记为 title）**：
- "Figure 1. xxx" "Figure. xxx" → 这是**图片描述**，应标记为 `text` 类型
- "Table 1. xxx" "Table. xxx" → 这是**表格描述**，应标记为 `text` 类型
- 表格内的文本、图片说明文字等

## 输出示例
{
  "blocks": [
    {"type": "header", "content": "General practice ...", "bbox": [80, 50, 520, 80]},
    {"type": "title", "level": 2, "content": "5.2 A structured diabetes care program", "bbox": [120, 100, 870, 150]},
    {"type": "table", "content": "| Parameter | Target |\\n|---|---|\\n| BMI | 5-10% weight loss<br>BMI >35 needs review |\\n| BP | ≤140/90 mmHg |", "bbox": [120, 160, 870, 400]},
    {"type": "title", "level": 3, "content": "Clinical context", "bbox": [120, 420, 340, 450]},
    {"type": "text", "content": "The goal of a structured care program...", "bbox": [120, 460, 870, 520]}
  ]
}


**重要提醒**：
1. bbox 坐标是**像素坐标**，直接输出你看到的位置即可
2. 每个 title 必须判断 level (1-4)
3. 按页面阅读顺序排列
4. 严格仅输出 JSON，不要有其他内容
5. 禁止重复输出省略号、重复符号、换行符\n、或其他无意义填充内容
6. 输出完 JSON 后立即停止，不要继续生成
7. "Figure." "Table." 开头的描述性文字是 text 类型，不是 title"""


# 需要过滤掉的块类型
SKIP_BLOCK_TYPES = {"header", "footer", "page_number"}
