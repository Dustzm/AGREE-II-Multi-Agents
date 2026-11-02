# 条目评价可靠性改造方案（Rule Engine 失败链路）

> 状态：**方案待评审，未实施**。仅 L1 引导层（结构化输出）已落地。
> 证据来源：`tmp/analysis/log-findings.md`、`tmp/analysis/validator-findings.md`、`tmp/analysis/json-robustness.md`、`tmp/analysis/context-adaptation-plan.md`，日志 `logs/backend.log`（run `1-2010-008E-1029195939276673`）。

## 0. 结论

那次运行 23 个条目 18 成功 / 5 失败（红：item_11、item_18、item_19、item_20、item_21），另 8 个条目是"规则引擎失败后回退到旧 appraiser 才成功"（item_1/2/3/4/5/7/16/20 中的回退记录，item_20 最终仍失败）。失败不是一个 bug，而是 **7 类缺陷叠加**：语义校验一刀切、重试无诊断、恢复预算共享、严格集合无回退、上下文估算失真、证据池污染、失败不可观测。

本方案按"单一职责分层 + 统一失败模型 + 组合根注入策略"改造，目标不是把某一类失败概率调低，而是让**任何失败都可分类、可恢复、可解释、可观测**，并且测试可回归。

非目标：不改 ADK 链路（另议）、不重写规则引擎评分语义、不改变现有评估 JSON 的既有字段语义。

## 1. 现状差距

| # | 缺陷 | 证据 | 位置 |
| --- | --- | --- | --- |
| 1 | 语义校验全弃：一个对象 `value_not_found_in_quote` 丢掉整批合法对象并抛 `ValueError` | 25 次 repair 中 21 次由它触发；31 条记录覆盖 14/23 条目；item_11/18/21 因此硬失败 | `core/rule_engine/extractors/llm.py:232-233` |
| 2 | 重试是"盲的"：提示词说"invalid JSON"、示例 `{"objects": []}`、不回显坏对象 | 3 条重试链路共 12/25 次返回空数组（静默丢证据） | `core/rule_engine/prompts/extraction.py:106-132` |
| 3 | 恢复预算 3 次全阶段共享，repair 与 transport 重试互相挤占 | item_18 第 4 批 repair 时 `allowance exhausted` | `core/llm/recovery.py:47-80`、`core/agents/appraiser.py:116` |
| 4 | strict 集合硬编码且失败即红；其余条目静默回退 | 5 个 strict 中 4 个失败；8 个条目回退无任何标记 | `core/agents/appraiser.py:235-260` |
| 5 | 上下文"自适应"实为字节估算（高估 4.2×），无 tokenizer、无输出预留 | 188264 / 190114 / 181736 / 193807 / 179459 五次 `ContextBudgetExceeded` | `core/llm/context.py:58-99` |
| 6 | 输出上限未纳入服务端真实上限 | max_tokens=12000，服务端 8192 截断；item_19 输出 24744 字符、item_20 19347 字符 | `core/llm/context.py:55-56`、`core/llm/gateway.py:125` |
| 7 | 证据池污染：修订/附件章节（label=Appendix，含 SIGN 50 手册示例）进入抽取；prompt 不渲染 label | item_11 抽到"扑热息痛治疗偏头痛"示例；263 个 Appendix chunk | `core/rule_engine/appraiser_adapter.py:436-446`、`core/rule_engine/prompts/extraction.py:30,102` |
| 8 | 分词/分批靠 overflow 二分触发 | item_13 触发 113 次抽取、item_7 69 次 | `core/rule_engine/extractors/llm.py:45-47` |
| 9 | JSON 语法修复阶梯只存在于原型 | `tmp/analysis/json_ladder.py` 11/11 通过但未并入 | `core/llm/json_output.py` |
| 10 | 失败不可观测：日志截断、无分类计数、无 run 级失败报告 | 现场只能靠 8MB 日志人肉对齐 | `core/agents/tools/appraiser_batch.py:284` |

## 2. 设计原则

1. **每层一个职责、一个失败责任**：引导（schema）只管形状；本地修复只管语法；校验器只管"对象是否合法"；策略层决定"非法对象怎么办"；预算层只管"还能不能重试"。
2. **纯函数优先**：校验器、打包器、token 估算、JSON 语法修复都是无 IO 纯函数，可单测、可复用、可被 legacy 路径复用。
3. **依赖单向**：`core/llm/*` 不得 import `core/rule_engine/*`；跨层只通过 Protocol 与冻结数据类交互；策略在组合根（appraiser）注入。
4. **失败是一等数据**：`FailureKind` + `FailureContext` 贯穿 校验→预算→回退→报告→UI，避免每层各写一套 if/else。
5. **默认安全、可回滚**：所有行为变化走配置开关，默认值选"不红但标记"，可一键回退旧行为。
6. **不破坏既有契约**：现有评估 JSON 字段只增不改；`RuleLLMClient` Protocol 只扩展不破坏。

## 3. 统一失败模型（新增 `core/llm/failures.py`）

```python
class FailureKind(str, Enum):
    TRANSPORT = "transport"            # 网络/超时/限流
    TRUNCATED = "truncated"            # finish_reason=length
    JSON_SYNTAX = "json_syntax"        # 非法 JSON（可本地修）
    SCHEMA_VIOLATION = "schema_violation"  # 形状/类型/枚举不符
    SEMANTIC_SPAN = "semantic_span"    # quote/value 不符（可修复）
    EMPTY_RESULT = "empty_result"      # 合法但零对象（要区分"真没有"与"修坏了"）
    BUDGET_EXCEEDED = "budget_exceeded"
    STAGE_CONTRACT = "stage_contract"  # 代码/契约 bug（必须暴露）
```

```python
@dataclass(frozen=True)
class ValidationIssue:
    code: str                 # value_not_found_in_quote / missing_quote / ...
    object_index: int
    field: str | None
    severity: Literal["warning", "error"]
    message: str
    object_type: str | None = None
    object_value: str | None = None
    quote: str | None = None
    hint: str | None = None    # 给修复提示词用的可操作建议

@dataclass(frozen=True)
class FailureContext:
    kind: FailureKind
    stage: str
    item_id: str
    attempt: int
    issues: tuple[ValidationIssue, ...] = ()
    bad_objects: tuple[dict, ...] = ()      # 上一轮坏对象（供回显）
    chunk_ids: tuple[str, ...] = ()
    budget: BudgetSnapshot | None = None
    error_text: str = ""

    def render_for_prompt(self) -> str: ...
```

`classify(error) -> FailureKind` 是唯一分类入口，供预算、回退、观测复用。

## 4. 分层设计

### L1 引导层（已落地，只需补齐）
- 已完成：`core/llm/structured_output.py`（模式阶梯 + 记忆 + 环境变量）、`core/llm/gateway.py`（extra 透传、extra_body 浅合并）、`core/rule_engine/litellm_client.py`（自动降级）、`core/rule_engine/extractors/schema.py` + 抽取器下发 schema。
- 本次补齐：抽取 schema 允许 `attributes` 按对象类型细分（可选，第二阶段）；repair 调用同样带 schema（已做）。
- 明确边界：引导层**不解决**语义不符与截断。
- 单数组兜底：`complete_json(..., list_wrapper="decisions")` 允许模型直接返回决策数组（`[{index,...}]` 或单个 `{index,...}`），由客户端归一到 `{"decisions": [...]}`；校验/分组/评分分类三处校验调用已启用，避免整条条目因外层包装缺失而回退。

### L2 本地语法修复层（新增 `core/llm/json_repair.py`）
- 职责：把 `tmp/analysis/json_ladder.py` 迁移为纯函数 `repair_json_text(text) -> RepairOutcome`（fence 剥离、尾随逗号、非法转义、未转义换行、截断数组闭合）。
- 替换：`core/llm/json_output.py` 的 `parse_json_object` 内部先跑修复阶梯；修复失败才抛 `InvalidModelJSON`。
- 约束：**只修语法，不补事实**；截断（`TruncatedModelOutput`）不走修复，走 L6 打包修复。
- 测试：11 个真实坏样本 fixture（来自本轮生产日志）。

### L3 语义校验层（新增 `core/rule_engine/validation/`）
```
validation/
  issues.py      # ValidationIssue 定义（或复用 failures.py）
  validator.py   # class ObjectValidator(spec): validate(dict) -> tuple[ValidationIssue, ...]
  policies.py    # BatchPolicy Protocol + StrictAllOrNothing / KeepValidDropInvalid / Quarantine
  batch.py       # validate_batch(spec, payload, policy) -> BatchOutcome(objects, issues, dropped)
```
- `validator.py`：逐对象校验（type 枚举、quote/value 非空、`contains_source_span`、confidence、attributes），**不做批级决策、不抛异常**；归一化比较逻辑从 extractor 内联函数移到这里（单一事实源）。
- 引文必须出自所引 chunk：`validate_batch(..., sources=chunk_source_texts(chunks))` 在抽取阶段就检查 `quote ⊂ chunk.source_text()`（`unknown_quote_chunk` / `quote_not_found_in_chunk`），使这类对象进入回锚与 repair，而不是在 LLM 验证之后才被 `ObjectStore` 丢弃（2026-11-02 批次中验证通过的对象有 10% 死在这一步）。`ObjectStore` 与 rubric appraiser 共用 `EvidenceChunk.source_text()`，三处判定一致。
- 匹配变体：严格 → 版式无关（列表标记、断行连字符、MinerU 行内 LaTeX 如 `$\alpha$`/`$\geq$`/`$^{2}$` 渲染为 Unicode 并忽略符号两侧空格）→ 宽松；后一变体只增加匹配。
- 引文回锚（确定性，先于 repair，`validation/reanchor.py`）：`value_not_found_in_quote` 或 `quote_not_found_in_chunk` 的对象若其 `value` 足够具体（规范化后 ≥4 字符）且在所引 chunk 中**恰好出现一次**，用 `find_source_span(chunk.content, value, max_chars=600)` 取所在句/行的原文切片替换 `quote` 并重新校验；过短、重复出现或只能锚到整段的对象不回锚，交给 repair。这样可把"引文是复述、value 真实存在"的对象直接救回，又不会把对象钉到无关句子上。
- `policies.py`：
  - `StrictAllOrNothing`：保留现行为（用于回归对照）。
  - `KeepValidDropInvalid`（**新默认**）：合法对象保留，非法对象丢弃并把 issue 记入 `review_reasons`（severity=warning）；批不失败。
  - `Quarantine`：非法对象存 `dropped` 字段，供人工复核/报告展示。
- `extractors/llm.py::_parse_objects` 缩减为：shape 归一 → `validate_batch(...)` → 转 `ExtractionBatchResult`。**删除 :232 的 raise**（契约错误仍抛，由 `SCHEMA_VIOLATION`/`STAGE_CONTRACT` 表示）。
- 不变量：抽取 issue 只进 `review_reasons`，不得让 `rule_result.status="error"`（`core/rule_engine/engines/profiles.py` 仅 155/657 两处写 error，需加测试锁定）。
- 测试：`validator-findings.md` §5 的 31 条真实 (quote,value) 矩阵作为参数化用例。

### L4 修复层（新增 `core/llm/repair.py` + `core/rule_engine/prompts/repair.py`）
- `RepairPlanner`（LLM 无关）：
  ```python
  class RepairPlanner:
      def plan(self, failure: FailureContext) -> tuple[RepairStep, ...]
  ```
  阶梯（成本递增，命中即停）：
  1. `LOCAL_REPAIR`：L2 语法修复（解析层已自动）。
  2. `RESEND_WITH_DIAGNOSIS`：把 `bad_objects` + `issues`（含 hint）回显给模型，要求"只修正以下对象/只补缺失字段"；提示词独立渲染器 `render_object_repair_prompt(spec, chunks, failure)`。
  3. `SCOPE_REDUCTION`：丢弃反复失败的对象或缩小 batch（复用并替换 `split_on_overflow` 的触发条件）。
- 提示词改造要点：
  - 删除 `output_example = {"objects": []}` 诱导；改为"确实无对象时返回 `{"objects": []}` 并在 `note` 说明"（note 不进 schema，仅日志）。
  - 错误摘要从裸字符串升级为 `FailureContext.render_for_prompt()`（逐对象列出字段/原因/原文片段）。
  - 空数组视为 `EMPTY_RESULT` 记录到 review_reasons（不静默），报告中可见"该批次未抽取到对象"。
- 解耦：extractor 只调 `planner.plan()` 并执行 step；prompt 渲染由 `prompts/repair.py` 负责，extractor 不再拼提示词。

### L5 预算层（重构 `core/llm/recovery.py`）
- 现状问题：一个 `RecoveryBudget(remaining=3)` 被 evidence/抽取/校验/评分/报告/传输共享，repair 与 transport 互相挤占（item_18 因此红）。
- 新模型：
  ```python
  @dataclass(frozen=True)
  class LedgerQuotas:
      transport: int = 2
      truncation: int = 2
      repair: int = 2
      schema: int = 1
      total: int = 5

  class RecoveryLedger:
      def reserve(self, kind: FailureKind, stage: str) -> Reservation
      def snapshot(self) -> BudgetSnapshot
  ```
- 兼容：保留 `RecoveryBudget` 作为 facade（`consume()` 映射到 ledger.transport），旧调用点无需一次性改完；新代码用 ledger。
- 替换点：`gateway` 传输重试 → `reserve(TRANSPORT)`；`note_repair` → `reserve(REPAIR)`；`split_on_overflow` → `reserve(TRUNCATED)`（抽取阶段例外：截断直接切批属结构性退避，`charge_overflow=False` 不消耗截断额度，切批树由 `_MAX_TRUNCATION_SPLITS=24` 限定，超出才抛 `TruncatedModelOutput`）。
- 配置：`LLM_RETRY_QUOTA_TRANSPORT/TRUNCATION/REPAIR/REPAIR_STAGE/TOTAL`。修复配额按阶段（抽取修复 / 校验修复 / 解释修复）再设单项上限，避免某一阶段（尤其抽取修复）耗尽后续阶段额度。

### L6 窗口层（新增 `core/llm/window.py` + `tokenizer.py` + `planner.py`）
```
window.py    # ModelProfile: context_window / max_input / max_output / tokenizer / fallback ratio
tokenizer.py # TokenEstimator: tiktoken（有则用）→ 否则 CJK/ASCII 加权上界 + margin
planner.py   # BatchPlanner.pack(chunks, prompt_skeleton, output_reserve) -> list[list[chunk]]
```
- `ModelProfile`：`settings.context_window` > `model_catalog`（`max_input_tokens + max_output_tokens` 推导）> provider 默认；`max_output` 默认 8192（实测服务端上限），`output_limit()` 取 min；仍未知时按未配置处理并给出明确报错。
- `TokenEstimator`：替换 `core/llm/context.py:58-99` 的字节估算（实测高估 4.2×）；tiktoken 可用时精确；不可用时回退 `ceil(宽字符数 + 其余字节/4.2)`，宽字符按约 1 token/字估算，避免中文长文本被低估。
- `BatchPlanner`：抽取前先按 token 预算切批（含 system prompt + 输出预留），单批必合法；固定模板本身超预算时回退到 `split_on_overflow` 兜底路径，而不是直接判条目失败。
- 测试：用真实 188k/193k 输入断言切批数 ≥2 且每批估算 < window；`max_output` 生效断言。
- 单次输出目标：`prompts/extraction.py::request_object_limits` 让每批提示里的各类上限之和不超过 `TARGET_OUTPUT_TOKENS(6000) / TOKENS_PER_OBJECT(150)`，超出时等比缩小（四舍五入、每类至少 1）；条目级上限仍由 `ObjectStore` 按 `max_objects` 执行。item_12（9 类合计 78 个、平均约 157 token/对象）此前单批满额就超过服务端 8192 输出上限，2026-11-02 批次 28 次截断中 15 次来自它。

### L7 证据层（路由 + 渲染）
- 提示渲染：`prompts/extraction.py:30,102` 的 chunk 头改为 `[chunk_001][Recommendation] ## title`（`EvidenceChunk.metadata["label"]` 已存在，`appraiser_adapter.py:436-446`）。
- 路由过滤：`core/rule_engine/routing/weighted.py` 已实现：全局 `RULE_ROUTING_EXCLUDED_LABELS`（默认仅 `Irrelevant`）+ 标题判定 `core/preprocessor/section_titles.py`（去掉编号/附录前缀后**整题**等于 References/参考文献/修订历史/索引等才排除，不做子串匹配，避免误伤 "preferences"、"Body Mass Index"、"Supporting information: search strategy" 等正文章节；附件标签器复用同一判定）；`Appendix` 不再整类剔除，是否参与由领域标签映射决定（DOMAIN_LABEL_MAPPING 已把 Appendix 纳入领域 2/3/5/6），并按 `RULE_APPENDIX_SCORE_FACTOR` 降权、按 `RULE_APPENDIX_MAX_CHUNKS` 限数。支持 `ItemSpec.chunk_routing.excluded_labels` 条目级追加（与全局取并集，是“追加”不是“覆盖”）；排除命中时以 INFO 记录 kept/total。
- 配置：全局默认 + `ItemSpec.chunk_routing.excluded_labels` 追加（少数 item 需要额外排除某类章节）。
- 测试：真实 evidence pool 断言 item_11 路由结果不含 chunk_604-606（SIGN 手册示例），且 prompt 渲染含 label。

### L8 编排策略层（`core/agents/appraiser.py` + `appraiser_adapter.py`）
- 现状：`strict_rule_items` 硬编码、失败即 raise、其余静默回退（8 个条目无痕）。
- 新：组合根注入 `RuleFailurePolicy`：
  ```python
  class RuleFailurePolicy:
      def decide(self, *, item_id: str, kind: FailureKind, strict: bool) -> Decision
      # Decision = Fatal | Fallback(degraded_reason) | Retry
  ```
  默认规则：只有 `STAGE_CONTRACT`（我方代码/数据契约错误，含 KeyError/TypeError/AttributeError）→ Fatal；`SCHEMA_VIOLATION/SEMANTIC_SPAN/JSON_SYNTAX/EMPTY_RESULT/TRUNCATED/BUDGET_EXCEEDED/TRANSPORT`（模型或环境问题）→ Fallback 且标记 degraded（strict item 也允许回退，但强制标记，不再"红"）。
- 回退结果写入 `evaluation_meta`：`engine=rule|legacy_fallback`、`fallback_reason`、`failure_kind`、`attempts`；批量汇总把 degraded 单列；报告与前端红/黄徽标读取该字段（前端只在 tooltip 展示原因，主视图不变）。
- 兼容：`rule_engine_fallback_on_error` 保留为总开关；新增 `RULE_STRICT_ITEMS`、`RULE_FALLBACK_POLICY=legacy|degraded`。
- 条目超时：`appraiser_batch` 为每个条目建子令牌（`CancellationToken.linked_to(batch_token)`），超时即取消该令牌；`run_iterative_evaluation` 用 `bind_cancellation` 把令牌绑定到条目线程上下文，`LLMGateway.complete/acomplete` 在拿到并发槽后检查，线程在下一次模型调用前退出，不再跑完整条目、占着串行槽让重试空等。

### L9 观测层（新增 `core/observability/appraisal.py`）
- `FailureRecorder`：记录每次 attempt（stage/kind/耗时/是否降级），run 结束汇总写 `output/<run>/failure_report.json`：
  ```json
  {"run_id": "...", "items": [{"item_id": "item_11", "result": "success",
    "engine": "rule", "attempts": 2, "kinds": ["semantic_span"], "repairs": 1}]}
  ```
- 日志一行摘要：`item=item_11 attempts=2 kinds=[semantic_span] repair=1 fallback=no result=success`。
- 指标：按 kind 聚合计数，写入 run summary；前端失败 tooltip 显示 kind（progress payload 增 `reason` 可选字段）。
- 测试：断言 JSON 结构与计数。

## 5. 依赖方向与组合根

```
core/agents/appraiser.py（组合根：策略 + 装配）
 ├─ RuleFailurePolicy（L8）
 ├─ RuleEngineAppraiserAdapter
 │   ├─ WeightedChunkRouter（L7 过滤）
 │   ├─ RuleObjectExtractor
 │   │    ├─ BatchPlanner(L6) / TokenEstimator(L6)
 │   │    ├─ ObjectValidator + BatchPolicy(L3)
 │   │    └─ RepairPlanner(L4) → prompts/repair.py
 │   └─ LiteLLMRuleClient(ledger L5) → LLMGateway(extra) → litellm
 └─ legacy appraiser（ADK，保持不变）

core/llm/*：structured_output / json_output(+json_repair) / window+tokenizer+planner / failures / recovery(ledger) / gateway
core/rule_engine/*：models / validation / routing / extractors / prompts / litellm_client / appraiser_adapter
```
规则：`core/llm` 不知道 item/spec/对象类型；`core/rule_engine` 通过 Protocol 消费 llm 能力；appraiser 是唯一决定"失败怎么办"的地方。

## 6. 数据契约变更（只增不改）

| 对象 | 变更 | 兼容性 |
| --- | --- | --- |
| `EvidenceChunk` | 无（label 已在 metadata） | 兼容 |
| `ExtractionBatchResult` | 新增 `dropped: tuple[dict, ...]`、`issues: tuple[ValidationIssue, ...]`（默认空） | 兼容 |
| 条目结果 dict | 新增 `evaluation_meta`（engine/fallback_reason/failure_kind/attempts） | 兼容（可选字段） |
| run 产物 | 新增 `failure_report.json` | 兼容 |
| 配置 | 新增 `RULE_*` / `LLM_RETRY_QUOTA_*` / `RULE_ROUTING_EXCLUDED_LABELS` / `RULE_EXTRACTION_POLICY` | 兼容 |

## 7. 实施阶段（每阶段独立可上线、可回滚）

| 阶段 | 内容 | 改动文件 | 验收 |
| --- | --- | --- | --- |
| Phase 1（核心失败链路） | L3 校验 + L4 修复 + L2 语法阶梯 | `validation/*`(新)、`extractors/llm.py`、`prompts/extraction.py`、`prompts/repair.py`(新)、`core/llm/json_repair.py`(新) | item_11/18/21 不再因语义不符 fatal；repair 空数组率 <20%；31 条真实样本参数化测试通过 |
| Phase 2（预算与策略） | L5 ledger + L8 policy | `core/llm/recovery.py`、`core/agents/appraiser.py`、`tools/appraiser_batch.py`、`engines/profiles.py`(review 不落 error 测试) | item_18 类"配额耗尽"消失；degraded 条目在汇总单列；无静默回退 |
| Phase 3（上下文） | L6 profile/tokenizer/planner | `core/llm/context.py`、`window.py`/`tokenizer.py`/`planner.py`(新)、`extractors/llm.py`、`gateway.py` | 188k/193k 输入不再超限；item_19/20 截断消失；单条目抽取调用中位数 <4 |
| Phase 4（证据与观测） | L7 过滤 + L9 报告 + UI tooltip | `routing/weighted.py`、`prompts/extraction.py`、`appraiser_adapter.py`、`observability/*`(新)、web 进度字段 | item_11 证据 0 附录；每 run 产出 failure_report.json；失败 tooltip 可读 |

依赖顺序理由：先让"失败不再直接红"（Phase 1/2），再修"为什么慢/超限"（Phase 3），最后修"证据质量与可观测"（Phase 4）。

## 8. 验收指标（同一 PDF 重跑 + 单测）

- 硬失败条目：5 → 0（仅契约 bug 允许 fatal）
- 语义 issue 可修复率：repair 后合法对象恢复 ≥70%
- repair 空数组率：12/25 → <20%
- 单条目抽取调用次数中位数：13 → <4（item_13 113 次、item_7 69 次显著下降）
- 上下文超限（`ContextBudgetExceeded`）：6 → 0
- 截断（`TruncatedModelOutput`）：2 → 0
- item_11 证据中 Appendix 占比：>0 → 0
- degraded/fallback 条目：100% 在报告与 UI 可见
- 单测：新增 ~40 用例；全量回归保持 640+ 全绿

## 9. 配置与开关（已按评审决定落地）

```bash
# 结构化输出：auto（默认，按能力降级）/ json_schema / guided_json / json_object
LLM_STRUCTURED_OUTPUT=auto
# 校验策略：strict（旧行为）/ keep_valid（新默认）/ quarantine
RULE_EXTRACTION_POLICY=keep_valid
RULE_EXTRACTION_REPAIR_ATTEMPTS=1
# 严格条目（由设置驱动，不再硬编码；legacy 模式下才 fatal）
RULE_STRICT_ITEMS=item_11,item_12,item_18,item_19,item_21
RULE_ENGINE_FALLBACK_ON_ERROR=true
# 回退策略：legacy（旧行为，严格条目 fatal）/ degraded（新默认，允许回退但标记）
RULE_FALLBACK_POLICY=degraded
# 恢复配额（宽松档：修复总 6，单阶段修复 2，避免抽取修复挤掉校验/解释修复）
LLM_RETRY_QUOTA_TRANSPORT=3
LLM_RETRY_QUOTA_TRUNCATION=3
LLM_RETRY_QUOTA_REPAIR=6
LLM_RETRY_QUOTA_REPAIR_STAGE=2
LLM_RETRY_QUOTA_TOTAL=8
# 证据过滤（附录不默认排除，由领域标签映射决定是否参与）
RULE_ROUTING_EXCLUDED_LABELS=Irrelevant
# 观测落盘（评审决定：run 目录）
RULE_REPORT_FAILURES=true
```

## 10. 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 放宽校验导致"脏对象"进评分 | 默认 `keep_valid` 但 issue 全量入 review_reasons + 报告可见；`RULE_EXTRACTION_POLICY=strict` 一键回退 |
| 回退标记影响下游解析 | 只加可选字段，不改既有键；`RULE_FALLBACK_POLICY=legacy` 回退 |
| 附录过滤误伤（某些 item 需要附录） | 全局默认 + `ItemSpec.chunk_routing.excluded_labels` 覆盖；`RULE_ROUTING_EXCLUDED_LABELS=` 置空关闭 |
| tokenizer 引入新依赖 | 无 tiktoken 时用加权估算；`LLM_CONTEXT_ADAPTATION=off` 回退旧估算 |
| 配额改动导致重试变多/成本上升 | total 上限保持默认 8（可配）；failure_report 观测 kind 分布 |
| 提示词改造引入评分漂移 | Phase 1 先只改 repair 提示词（不动主抽取提示词）；用 golden 用例对比 item_1 等回归 |

## 11. 待拍板决策（评审重点，已全部拍板）

> 评审结论（2026-10-29）：1) keep_valid，但必须尽量避免丢弃非法对象（强化引导+修复，报告记录 dropped 数）；2) 允许回退，界面无需标注，仅日志体现；3) 所有 item 默认排除附录/修订历史/References，可按 ItemSpec 覆盖；4) 配额宽松档 3/3/3/8；5) 允许 tiktoken；6) failure_report.json 写 run 目录。

1. **默认校验策略**：`keep_valid`（推荐，非法对象丢弃但条目继续）还是 `quarantine`（保留非法对象供报告展示）？是否允许"丢弃非法对象后仍出分"？
2. **strict 语义**：保留 5 项"不允许静默回退"、但允许 degraded 回退（推荐），还是彻底取消 strict 统一回退+标记？
3. **附录过滤范围**：全 item 默认排除附录，还是只有"正文类" item 排除（appendix/checklist 类 item 保留）？
4. **配额数值**：transport 2 / truncation 2 / repair 2 / total 5 是否可接受？
5. **tokenizer**：允许引入 tiktoken（离线 wheel）还是必须零新依赖（用加权估算）？
6. **观测落盘**：`failure_report.json` 放 run 目录（推荐）还是进后端 DB/统计表？

## 12. 工作量估算

| 阶段 | 代码 | 测试 | 风险 |
| --- | --- | --- | --- |
| Phase 1 | ~350 行（新 3 文件 + 改 3 文件） | ~20 用例 | 中 |
| Phase 2 | ~200 行 | ~8 用例 | 中（涉及 appraiser 编排） |
| Phase 3 | ~250 行 | ~10 用例 | 中低 |
| Phase 4 | ~200 行 + 前端小改 | ~6 用例 | 低 |
| 合计 | ~1000 行 | ~44 用例 | — |

## 13. 实施状态（本轮已落地）

| 层 | 状态 | 落地位置 |
| --- | --- | --- |
| L1 引导 | 已落地 | `core/rule_engine/extractors/schema.py` + `litellm_client.complete_json` 模式阶梯 |
| L2 语法修复 | 已落地 | `core/llm/json_repair.py`（prose/fence、非法转义、全角、尾逗号、截断补全）|
| L3 语义校验 | 已落地 | `core/rule_engine/validation/{validator,batch,policies}.py`；`llm.py` 接入 keep_valid 并记录 `dropped`/`issues` |
| L4 修复 | 已落地 | `core/llm/repair.py` + `core/rule_engine/prompts/repair.py`；repair 提示词回显非法对象与 issue 提示 |
| L5 预算 | 已落地 | `core/llm/recovery.py`：`LedgerQuotas` 四桶（transport/truncation/repair/total），`RecoveryBudget` 门面兼容旧接口 |
| L6 窗口 | 已落地 | `core/llm/{tokenizer,window,planner}.py`；`context.py` 改用 token 估算；抽取按窗口预分批 |
| L7 证据 | 已落地 | `routing/weighted.py` 标签+标题启发式排除附录/参考文献类；抽取与修复提示词均渲染 `[chunk_id][label]` |
| L8 策略 | 已落地 | `core/rule_engine/failure_policy.py`；`appraiser.py` degraded 回退并在 payload 标注 `degraded/failure_kind/fallback_reason` |
| L9 观测 | 已落地 | `core/observability/appraisal.py`；`appraiser_batch` 汇总后写 `failure_report.json`（含失败的 kind/stage；degraded 不重复计入 succeeded） |

新增单测：`test_structured_output.py`、`test_rule_validation.py`、`test_llm_window_planner.py`、`test_rule_reliability_policy.py`；全量回归见本轮执行结果。
