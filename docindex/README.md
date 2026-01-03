# DocIndex: 基于聚类算法的文档标题层级构建系统

## 概述

`docindex` 是一个用于从 PDF 文档中自动提取标题并构建层级结构的工具包。它结合了 **MinerU 结构化解析**、**PDF 原生字体提取** 和 **多维特征聚类** 技术，能够准确识别文档中的标题层级关系。

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                      build_doc_index()                          │
│                        (pipeline.py)                            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   loader.py   │   │font_extractor │   │ classifier.py │
│               │   │     .py       │   │               │
│  加载 MinerU  │   │  提取字体特征  │   │   聚类分级    │
│  content_list │   │  + 居中检测   │   │               │
└───────────────┘   └───────────────┘   └───────────────┘
```

---

## 核心模块详解

### 1. `loader.py` - 数据加载

#### 功能
从 MinerU 解析输出的 `content_list.json` 加载内容块。

#### 核心数据结构

```python
@dataclass
class MineruBlock:
    block_id: str           # 唯一标识 (如 "p0_b1")
    type: str               # 类型: "title", "text", "table", "list"
    content: str            # 文本内容
    bbox: List[float]       # 边界框 [x0, y0, x1, y1]
    page_idx: int           # 页码索引 (0-based)
    text_level: int         # MinerU 标注的层级 (1-N)
    
    # 字体属性 (由 font_extractor 填充)
    font_size: float        # 字号 (pt)
    font_name: str          # 字体名称
    is_bold: bool           # 是否粗体
    is_centered: bool       # 是否居中
    
    # 聚类结果 (由 classifier 填充)
    level: int              # 最终层级
```

---

### 2. `font_extractor.py` - 字体特征提取

#### 功能
1. 从 PDF 源文件提取所有字符及其字体元数据
2. 将 MinerU 标题与 PDF 字符进行匹配
3. 检测标题是否居中对齐

#### 2.1 字符提取

```python
def extract_chars_with_font(pdf_path: str) -> List[CharInfo]
```

使用 `pdfplumber` 提取每个字符的：
- `text`: 字符文本
- `size`: 字号 (pt)
- `fontname`: 字体名称
- `top`, `x0`: 位置坐标
- `width`: 字符宽度
- `page_width`: 页面宽度
- `page_idx`: 页码

#### 2.2 标题匹配

```python
def match_title_font(
    title: str,                    # 标题文本
    chars: List[CharInfo],         # PDF 字符列表
    page_idx: int = None,          # 限定页码
    bbox: List[float] = None,      # MinerU bbox
    bbox_scale: tuple = None,      # 坐标缩放比例
    margin: float = 20.0,          # 区域扩展边距 (pt)
    line_tolerance: float = None   # 行高容差 (默认: 0.8 * font_size)
) -> Dict[str, Any]
```

**匹配流程：**

1. **文本预处理**
   - 清理 LaTeX 数学公式 (`$...$`)
   - 移除序号前缀 (`1.`, `一、`, `(1)` 等)
   - 标准化：去除标点、空格，转小写

2. **区域筛选**
   - 根据 `page_idx` 筛选指定页的字符
   - 根据 `bbox` + `margin` 筛选区域内字符
   - 坐标缩放：`pdf_coord = mineru_coord * scale`

3. **首字符锚定**
   - 在候选区域中搜索首字符匹配

4. **同行字符聚合**
   - 使用 `line_tolerance` 聚合同一行字符
   - 默认容差：`0.8 × font_size` (自适应)

5. **文本匹配验证**
   - 简单包含匹配
   - CJK 容错匹配 (处理隐形水印干扰)
   - 子序列匹配 (处理插入字符干扰)

**返回值：**
```python
{
    "font_size": 10.5,         # 字号 (pt)
    "font_name": "SimHei",     # 字体名称
    "is_bold": True,           # 粗体检测 (基于字体名称)
    "is_centered": True        # 居中检测
}
```

#### 2.3 居中检测算法 (Strict Centering)

居中检测使用 **左右边距对称性检测** 而非简单的中点检测，以避免长文本误判。

**检测逻辑：**

```python
def check_centered(region_start, region_end):
    region_width = region_end - region_start
    line_width = max_x1 - min_x0
    
    # 1. 满行检测：如果占宽 > 80%，认为是正文，不是标题
    if line_width > region_width * 0.8:
        return False
    
    # 2. 对称性检测：左右边距差 < 阈值
    left_margin = min_x0 - region_start
    right_margin = region_end - max_x1
    return abs(left_margin - right_margin) < threshold
```

**参数：**
| 参数             | 值        | 说明               |
| ---------------- | --------- | ------------------ |
| `threshold`      | 60.0 pt   | 左右边距差允许范围 |
| `fullness_ratio` | 0.8 (80%) | 满行判定阈值       |

**支持的布局：**
1. **整页居中**: `check_centered(0, page_width)`
2. **左栏居中**: `check_centered(0, page_width/2)`
3. **右栏居中**: `check_centered(page_width/2, page_width)`

**Pattern Override 机制：**

编号子标题即使通过对称性检测，也会被强制设为 `is_centered = False`：

```python
numbered_pattern = r'^[（\(]?[一二三四五六七八九十\d]+[）\)、\.]'
# 匹配: (一), （一）, 一、, 1., 1.1 等
```

---

## 标题层级判定流程详解

### 完整判定流程图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           标题层级判定流程                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐                                                            │
│  │  原始标题   │ "注射前的心理准备"                                           │
│  └──────┬──────┘                                                            │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────┐                               │
│  │ Step 1: 字体匹配 (font_extractor.py)    │                               │
│  │ ────────────────────────────────────────│                               │
│  │ 1. 定位 bbox 区域内的 PDF 字符          │                               │
│  │ 2. 文本匹配验证                         │                               │
│  │ 3. 提取字体属性                         │                               │
│  │    → font_size = 9.0                    │                               │
│  │    → is_bold = False                    │                               │
│  └──────┬──────────────────────────────────┘                               │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────┐                               │
│  │ Step 2: 居中检测                        │                               │
│  │ ────────────────────────────────────────│                               │
│  │ 1. 计算行宽度: line_width = 71.99pt     │                               │
│  │ 2. 计算栏宽度: col_width = 306.14pt     │                               │
│  │ 3. 满行检测: 71.99 / 306.14 = 23.5%     │                               │
│  │    → < 80%, 不是满行 ✓                  │                               │
│  │ 4. 对称性检测:                          │                               │
│  │    左边距 = 142.60, 右边距 = 91.55      │                               │
│  │    差值 = 51.05 < 60.0 ✓                │                               │
│  │ 5. Pattern Override 检测:               │                               │
│  │    不匹配编号模式 ✓                     │                               │
│  │    → is_centered = True                 │                               │
│  └──────┬──────────────────────────────────┘                               │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────┐                               │
│  │ Step 3: Pattern Score 计算              │                               │
│  │ ────────────────────────────────────────│                               │
│  │ "注射前的心理准备" 不匹配任何编号模式    │                               │
│  │    → pattern_score = 0                  │                               │
│  └──────┬──────────────────────────────────┘                               │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────┐                               │
│  │ Step 4: 构建聚类键                      │                               │
│  │ ────────────────────────────────────────│                               │
│  │ key = (9.0, True, 0)                    │                               │
│  │       ↑     ↑    ↑                      │                               │
│  │       │     │    └─ pattern_score       │                               │
│  │       │     └────── is_centered         │                               │
│  │       └────────── font_size (0.5pt精度) │                               │
│  └──────┬──────────────────────────────────┘                               │
│         │                                                                   │
│         ▼                                                                   │
│  ┌─────────────────────────────────────────┐                               │
│  │ Step 5: 全局排序与层级分配              │                               │
│  │ ────────────────────────────────────────│                               │
│  │ 所有聚类键按规则排序后:                  │                               │
│  │ • Level 1: (21.0, False, 0) - 大标题    │                               │
│  │ • Level 2: (14.0, False, 0) - 次大标题  │                               │
│  │ • Level 3: (10.5, True, 0)  - 居中标题  │                               │
│  │ • Level 4: (9.0, True, 0)   ← 我们的标题│                               │
│  │ • Level 5: (9.0, False, 30) - 靠左编号  │                               │
│  │ • Level 6: (9.0, False, 0)  - 靠左无号  │                               │
│  │                                         │                               │
│  │ "注射前的心理准备" → Level 4            │                               │
│  └─────────────────────────────────────────┘                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 排序规则详解

聚类键按以下规则进行 **降序排序** (值大的排前面):

```python
sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], k[1], k[2]), reverse=True)
```

**排序优先级：**

| 优先级 | 维度            | 含义                       | 示例比较                   |
| ------ | --------------- | -------------------------- | -------------------------- |
| 1      | `font_size`     | 字号越大，层级越高         | `21.0 > 14.0 > 10.5 > 9.0` |
| 2      | `is_centered`   | 居中优先于靠左             | `True > False`             |
| 3      | `pattern_score` | 有明确编号的优先于无编号的 | `30 > 20 > 10 > 0`         |

**为什么这样排序？**

1. **字号优先**: 字号是最显著的视觉特征，大字号几乎总是更重要的标题
2. **居中次之**: 在相同字号下，居中排列的标题通常是章节大标题
3. **编号最后**: 编号模式用于区分同字号、同对齐方式的不同层级

### 实际案例分析

以医学指南文档 `1-2016-002C.pdf` 为例：

```
输入标题列表:
┌────────────────────────────────────────────────┬────────┬────────┬───────┐
│ 标题内容                                        │ 字号   │ 居中   │ Score │
├────────────────────────────────────────────────┼────────┼────────┼───────┤
│ 中国糖尿病药物注射技术指南(2016年版)             │ 21.0pt │ False  │ 0     │
│ 《中华糖尿病杂志》第二届通讯编辑委员名单          │ 14.0pt │ False  │ 0     │
│ 结语                                            │ 10.5pt │ True   │ 0     │
│ 附录                                            │ 10.5pt │ True   │ 0     │
│ 注射前的心理准备                                 │ 9.0pt  │ True   │ 0     │
│ 注射部位规范检查3要素                            │ 9.0pt  │ True   │ 0     │
│ (一)儿童                                        │ 9.0pt  │ False  │ 30    │
│ (二)青少年                                      │ 9.0pt  │ False  │ 30    │
│ 推荐：                                          │ 9.0pt  │ False  │ 0     │
│ 参考：                                          │ 9.0pt  │ False  │ 0     │
└────────────────────────────────────────────────┴────────┴────────┴───────┘

聚类结果:
┌───────┬────────────────────────────────┬──────────────────────┬───────┐
│ Level │ 聚类键                          │ 包含标题数           │ 示例  │
├───────┼────────────────────────────────┼──────────────────────┼───────┤
│ 1     │ (21.0, False, 0)               │ 1                    │ 指南题目    │
│ 2     │ (14.0, False, 0)               │ 1                    │ 编委名单    │
│ 3     │ (10.5, True, 0)                │ 2                    │ 结语, 附录  │
│ 4     │ (9.0, True, 0)                 │ ~10                  │ 章节大标题  │
│ 5     │ (9.0, False, 30)               │ ~50                  │ (一)xxx 子标题 │
│ 6     │ (9.0, False, 0)                │ ~40                  │ 推荐：, 参考：│
└───────┴────────────────────────────────┴──────────────────────┴───────┘
```

### 关键判定参数汇总

#### 字体匹配阶段

| 参数             | 默认值            | 作用                             |
| ---------------- | ----------------- | -------------------------------- |
| `margin`         | 40.0 pt           | 扩大 bbox 搜索区域，容错定位误差 |
| `line_tolerance` | `0.8 × font_size` | 同行字符聚合容差，适配不同字号   |

#### 居中检测阶段

| 参数               | 默认值    | 作用                                           |
| ------------------ | --------- | ---------------------------------------------- |
| `threshold`        | 60.0 pt   | 左右边距差允许范围，越小越严格                 |
| `fullness_ratio`   | 0.8 (80%) | 满行判定阈值，超过此比例视为正文而非标题       |
| `numbered_pattern` | (见下)    | 编号子标题模式，匹配后强制 `is_centered=False` |

```python
numbered_pattern = r'^[（\(]?[一二三四五六七八九十\d]+[）\)、\.]'
```

#### 聚类阶段

| 参数                  | 默认值 | 作用                                         |
| --------------------- | ------ | -------------------------------------------- |
| `max_levels`          | 6      | 最大层级数，超出部分合并到最近层级           |
| `font_size_precision` | 0.5 pt | 字号四舍五入精度，避免微小字号差异创建新层级 |

#### 层级合并权重

| 维度            | 权重 | 含义                               |
| --------------- | ---- | ---------------------------------- |
| `size_weight`   | 1000 | 字号差异最重要，优先合并相近字号   |
| `center_weight` | 100  | 居中状态次之，优先合并相同对齐方式 |
| `score_weight`  | 1    | Pattern 分数最后，仅微调           |

---

### 3. `classifier.py` - 层级聚类

#### 功能
基于多维特征对标题进行聚类，自动划分层级。

```python
def classify_title_levels(
    title_blocks: List[MineruBlock],
    max_levels: int = 6            # 最大层级数
) -> Dict[str, Any]
```

#### 3.1 聚类键 (Clustering Key)

每个标题按以下三个维度分组：

```python
key = (font_size, is_centered, pattern_score)
```

| 维度            | 说明                  | 排序              |
| --------------- | --------------------- | ----------------- |
| `font_size`     | 四舍五入到 0.5pt 精度 | 降序 (大字号优先) |
| `is_centered`   | 是否居中              | 降序 (居中优先)   |
| `pattern_score` | 标题模式分数          | 降序 (高分优先)   |

#### 3.2 Pattern Score 计算

```python
def get_pattern_score(text: str) -> int:
    # 中文数字开头 (一、二、...) -> 30
    if re.match(r'^[一二三四五六七八九十]+[\.\、 ]', text):
        return 30
    
    # 复杂阿拉伯数字 (1.1, 1.2.3) -> 10
    if re.match(r'^\d+(\.\d+)+', text):
        return 10
    
    # 简单阿拉伯数字 (1., 2.) -> 20
    if re.match(r'^\d+([^\d\.]|$)', text):
        return 20
    
    # 其他 -> 0
    return 0
```

**设计理念：**
- 中文数字通常表示一级章节 (如 "一、前言")
- 简单阿拉伯数字表示次级章节 (如 "1. 背景")
- 复杂阿拉伯数字表示更深层级 (如 "1.1 研究方法")

#### 3.3 排序与层级分配

```python
# 排序规则：字号↓ -> 居中↓ -> Pattern分数↓
sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], k[1], k[2]), reverse=True)

# 示例排序结果：
# Level 1: (21.0, False, 0)   - 大标题
# Level 2: (14.0, False, 0)   - 次大标题
# Level 3: (10.5, True, 0)    - 居中标题
# Level 4: (9.0, True, 0)     - 居中小标题
# Level 5: (9.0, False, 30)   - 靠左, 中文序号
# Level 6: (9.0, False, 0)    - 靠左, 无序号
```

#### 3.4 层级合并

当检测到的层级组合数超过 `max_levels` 时，进行合并：

```python
def calc_dist(k1, k2):
    size_diff = abs(k1[0] - k2[0])
    center_diff = 1.0 if k1[1] != k2[1] else 0.0
    score_diff = abs(k1[2] - k2[2])
    
    # 权重：字号(1000) > 居中(100) > 分数(1)
    return size_diff * 1000 + center_diff * 100 + score_diff
```

**合并策略：**
- 保留前 `max_levels` 个层级
- 将剩余层级合并到距离最近的保留层级

---

### 4. `pipeline.py` - 主流程

```python
def build_doc_index(
    json_path: str,        # MinerU content_list.json 路径
    pdf_path: str,         # 原始 PDF 路径
    margin: float = 40.0,  # bbox 扩展边距
    line_tolerance: float = None  # 行高容差 (None=自适应)
) -> Tuple[List[MineruBlock], Dict[str, Any]]
```

**处理流程：**

```
1. 加载 MinerU JSON ─────────────────────────────────────────┐
                                                              │
2. 提取 PDF 字符信息 ─────────────────────────────────────────┤
                                                              │
3. 计算坐标缩放比例 ─────────────────────────────────────────┤
   scale_x = pdf_width / max_mineru_x                        │
   scale_y = pdf_height / max_mineru_y                       │
                                                              │
4. 字体匹配 ──────────────────────────────────────────────────┤
   for block in title_blocks:                                 │
       font_match = match_title_font(...)                     │
       block.font_size = font_match["font_size"]              │
       block.is_centered = font_match["is_centered"]          │
                                                              │
5. 聚类分级 ──────────────────────────────────────────────────┤
   classify_result = classify_title_levels(                   │
       title_blocks, max_levels=6)                            │
                                                              ▼
   返回 (blocks, classify_result)
```

---

## 参数速查表

### 字体匹配参数

| 参数             | 默认值            | 说明              |
| ---------------- | ----------------- | ----------------- |
| `margin`         | 40.0 pt           | bbox 区域扩展边距 |
| `line_tolerance` | `0.8 × font_size` | 同行字符聚合容差  |

### 居中检测参数

| 参数             | 默认值    | 说明               |
| ---------------- | --------- | ------------------ |
| `threshold`      | 60.0 pt   | 左右边距对称性阈值 |
| `fullness_ratio` | 0.8 (80%) | 满行判定阈值       |

### 聚类参数

| 参数                  | 默认值 | 说明             |
| --------------------- | ------ | ---------------- |
| `max_levels`          | 6      | 最大层级数       |
| `font_size_precision` | 0.5 pt | 字号四舍五入精度 |

### Pattern Score 权重

| 模式       | 分数 | 示例     |
| ---------- | ---- | -------- |
| 中文数字   | 30   | 一、前言 |
| 简单阿拉伯 | 20   | 1. 背景  |
| 复杂阿拉伯 | 10   | 1.1 方法 |
| 其他       | 0    | 参考文献 |

### 距离权重 (合并时使用)

| 维度          | 权重 |
| ------------- | ---- |
| 字号差        | 1000 |
| 居中差        | 100  |
| Pattern分数差 | 1    |

---

## 使用示例

```python
from docindex.pipeline import build_doc_index

# 构建文档索引
blocks, result = build_doc_index(
    json_path="content_list.json",
    pdf_path="document.pdf",
    margin=40.0
)

# 查看聚类结果
for cluster in result["clusters"]:
    print(f"Level {cluster['level']}: "
          f"{cluster['font_size']}pt, "
          f"centered={cluster['is_centered']}, "
          f"count={cluster['count']}")

# 应用层级到 blocks
from docindex.classifier import apply_cluster_levels
apply_cluster_levels(blocks, result["block_levels"])
```

---

## 设计决策说明

### 为什么使用对称性检测而非中点检测？

中点检测会导致长文本误判为居中（只要文本中心落在页面/栏目中心附近即可）。对称性检测要求左右边距相等，能有效排除这类误判。

### 为什么需要 Pattern Override？

编号子标题（如 `（一）xxx`）可能在版式上居中排列，但语义上是子标题。Pattern Override 机制确保这类标题不会被提升为大标题层级。

### 为什么 max_levels 默认为 6？

医学指南等复杂文档通常具有多达 5-6 级标题层级。将 max_levels 设为 6 可以保留足够的层级细粒度，避免强制合并造成的信息丢失。
