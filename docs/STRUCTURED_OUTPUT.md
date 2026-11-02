# 结构化输出（JSON Schema 引导解码）

## 结论先行：不是 ADK 版本问题

- 仓库里 `google-adk==1.21.0`（`requirements.txt:8-9`、`pyproject.toml:19-20`）**支持**结构化输出：
  `LlmAgent.output_schema`（`google/adk/agents/llm_agent.py:311`）→ `LlmRequest.config.response_schema`
  → `_to_litellm_response_format()`（`google/adk/models/lite_llm.py:1069`）→ `response_format={"type": "json_schema", ...}`，
  且 `_LITELLM_STRUCTURED_TYPES = {"json_object", "json_schema"}`（同文件 68 行）。
- 但本仓库**从未设置过 `output_schema`**（全仓 0 处引用），所以 ADK 这条链路一直没有开启引导解码。
- 规则引擎（item 评测真正失败的地方）**根本不经过 ADK**：`core/rule_engine/appraiser_adapter.py:429`
  直接构造 `LiteLLMRuleClient`，走 `core/llm/gateway.py` 的 LiteLLM 调用。
- 之前只有 grouping / verification / classifier / rubric 传了 `json_schema`（`core/rule_engine/grouping/llm.py:69,88` 等），
  唯独**物品抽取器（RuleObjectExtraction）没传**——item_11/18/21 的失败正出在这一步。

因此不需要升级 ADK；需要做的是把"能力探测 + Schema 下发 + 自动降级"补在规则引擎自己的调用链上。

## 实现

### 1. 能力模式与降级阶梯（`core/llm/structured_output.py`）

不同 OpenAI 兼容部署对 `response_format` 支持差异很大：

| 模式 | 请求形态 | 适用部署 |
| --- | --- | --- |
| `json_schema` | `response_format={"type": "json_schema", "json_schema": {"name", "strict", "schema"}}` | 新版 vLLM/SGLang/OpenAI |
| `guided_json` | `extra_body={"guided_json": <schema>}` + `response_format={"type": "json_object"}` | 旧版 vLLM/SGLang |
| `json_object` | `response_format={"type": "json_object"}` + 本地校验 | 最老部署 |

- 不做启动网络探测（会浪费 token、单测会打到真实服务）。
- 首次按 `json_schema` 请求；若抛出的异常属于"该部署不支持这种形态"（400/404/415/422/501 且消息含
  `response_format`/`json_schema`/`guided_json` 等标记），自动降级到 `guided_json`，再不支持则 `json_object`。
- 降级结果按 `(base_url, model)` 记入进程内缓存，同一部署只探测一次。
- 业务异常（例如 `value_not_found_in_quote` 这类 `ValueError`）不会触发降级，原样抛出。
- 环境变量 `LLM_STRUCTURED_OUTPUT=auto|json_schema|guided_json|json_object|off` 可强制指定。

### 2. 请求透传（`core/llm/gateway.py`）

`LLMGateway.complete()/acomplete()` 新增 `extra` 参数，`_merge_request_extra()` 负责把
`extra_body` 与 provider 默认的 `extra_body`（如 vLLM 的 `chat_template_kwargs.enable_thinking`）
**浅合并**，避免覆盖；`RequestBudget` 也把 schema 计入 token 估算。

### 3.  Rule client 统一入口（`core/rule_engine/litellm_client.py`）

所有规则引擎 LLM 调用都走 `LiteLLMRuleClient.complete_json()`：

- 传入 `json_schema` 时按当前模式构造 `response_format` + `extra`；
- 遇到不支持的结构化形态自动降级（见上）；
- `deepseek-*` 模型保持 `json_object`（DeepSeek 不支持 `json_schema` 引导）；
- 决策补充修复逻辑（原 `_complete_missing_decisions`）保留，并同样带 schema。

### 4. 抽取器接入（`core/rule_engine/extractors/schema.py` + `extractors/llm.py`）

`build_extraction_schema(spec)` 依据 `ItemSpec.object_specs` 生成抽取响应 Schema：

```json
{"objects": [{"type": {"enum": ["...对象类型..."]}, "value": "...", "quote": "...",
              "chunk_id": "...", "confidence": 0.0, "attributes": {}}]}
```

`RuleObjectExtraction` 与修复阶段 `RuleObjectExtractionRepair:<item>` 都带上它：
在 `json_schema` / `guided_json` 模式下类型枚举、必填字段、`confidence` 范围由解码器约束；
`json_object` 模式（含 deepseek 系列强制）下枚举不受约束，仍依赖本地校验与定向修复。

## 边界：哪些失败被修掉、哪些没修

| 失败类型 | 是否解决 |
| --- | --- |
| 非法 JSON（`Invalid \escape`、未转义换行、尾随逗号、fence） | ✅ 引导解码直接杜绝 |
| 对象类型/字段缺失/confidence 越界 | ✅ Schema 约束 |
| `quote`/`value` 语义不符（`value_not_found_in_quote`） | ❌ 需要重试提示词与校验策略配合（另项工作） |
| `max_tokens` 截断（`TruncatedModelOutput`） | ❌ 结构化输出不改变输出上限，仍需预算/分批 |

## 验证

```bash
conda activate ama
python -m pytest web/backend/tests/test_structured_output.py -q
python -m pytest web/backend/tests -q   # 全量用例应全绿（数量随迭代增长，不硬编码）
```

降级判定只认“能力类”错误（404/415/501，或 400/422 且明确提到不支持/未识别该字段），
自身 schema 写错（invalid schema / schema validation）不会触发降级，避免真实错误被静默吞掉。

部署侧可在 `.env` 里先跑 `LLM_STRUCTURED_OUTPUT=json_schema` 验证服务端是否支持；
日志出现 `部署不支持结构化输出模式，自动降级: stage=... mode=json_schema -> guided_json`
即说明降级链路生效。
