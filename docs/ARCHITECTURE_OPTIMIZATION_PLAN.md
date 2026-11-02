# AMA 架构与性能优化开发方案

> 状态：待评审。范围：后端服务、解析链路、任务执行模型、前端进度对接、交付工程化。
> 评审已定：**不做** MinerU 真实推理就绪检查与熔断；**清理**死代码。
> 方案由 Claude 分析代码后起草，Codex（gpt-6-astra）独立只读审阅仓库并给出设计，两份结论逐条核实后合并而成。

## 0. 结论

可用性问题归结为三条：

1. **没有端到端的截止时间**。各层分别设了很长的超时，叠起来要几个小时才会失败，期间没有进度信号。
2. **取消只能停止等待，不能停止工作**。请求、线程、子进程在"取消"之后还在运行，并继续占着共享资源。
3. **没有隔离**。最重、最容易卡住的工作（PDF 渲染、图片编码、MinerU 请求、整条评价流水线）跑在 API 进程或长寿命 worker 的事件循环和线程里，一处卡住，全部受影响。

目标架构的核心是：**统一的持久化任务模型 + supervisor 式 worker + 每次执行一个可终止的子进程**。存储继续用单机 SQLite，先解决执行失控和恢复语义，再优化吞吐。

| 目标 | 验收标准 |
|---|---|
| 解析不再无声卡住 | 任一阶段超过"无进展时限"就以明确原因失败；不存在只能靠外层大超时兜底的路径 |
| 停止立即生效 | 点击停止后，宽限期内（默认 10s）进入终态；执行进程已退出，没有遗留的 MinerU 或 LLM 在途请求，槽位已释放 |
| API 只做轻活 | 解析满载时，API 的 p99 延迟低于 200ms，事件循环延迟告警为 0 |
| 一套任务模型 | Web 单任务、批量、MCP 共用任务存储、调度、事件回放、停止和续跑语义 |
| 可诊断 | 任何卡住都能拿到线程栈、最后进展时间、当前阶段和在途请求数 |

**可直接复用的现有资产**（不推倒重来）：

- 任务级 LLM 配置快照（[context.py:11](../core/llm/context.py)）和请求预算 `RequestBudget`
- 标签检查点（[label_checkpoint.py](../core/preprocessor/label_checkpoint.py)）
- 批量的事件表和租约机制（[batch_jobs.py:69](../web/backend/app/services/batch_jobs.py)）
- MCP 的执行代次 `run_token`（[mcp/repository.py:48](../mcp/repository.py)）
- `ExecutionRuntime` 的阶段并发控制
- `/ama/ready` 端点

## 1. 现状与问题定位

### 1.1 进程与执行路径

| 入口 | 跑在哪 | 状态存在哪 | 停止与恢复 |
|---|---|---|---|
| Web 单任务（Chat 页） | API 进程内的 asyncio 任务（[chat.py:130](../web/backend/app/api/routes/chat.py)） | 内存：任务句柄、离线消息队列 | 协作式标志；重启即丢 |
| 批量 | batch-worker 进程，同一时间只跑一个 job（[batch_worker.py:516](../web/backend/app/batch_worker.py)） | `batch_jobs` / `batch_job_items` / `batch_job_events`，带租约和心跳 | 令牌 + 租约 + 停止标记，可续跑 |
| MCP | mcp-worker 进程，最多 4 个并发 job | `mcp_jobs`，另有一套租约实现 | 令牌 + 租约 + run_token |

三条路径都通过 `AppraisalRunner` 执行流水线，差别在外围的生命周期。批量和 MCP 之间用一段跨表 SQL 做准入互斥（[batch_jobs.py:262](../web/backend/app/services/batch_jobs.py)），Web 单任务完全绕过这层准入。编排代码 `AppraisalRunner` 放在 Web 层，并直接依赖 Web 的国际化模块（[agent_runner.py:32](../web/backend/app/services/agent_runner.py)），属于依赖倒置。

### 1.2 问题清单

**A 解析链路**

| 编号 | 问题 | 证据 |
|---|---|---|
| A1 | 页面请求失败、被取消或提前退出时，不取消其余子任务，迭代版聚合器也一样；孤儿请求占着进程共享的 30 个请求槽和 30 条连接；`pool=None` 表示等连接不限时 | [utils.py:91](../mineru_vl_utils/vlm_client/utils.py)、[http_client.py:27/83/114/547](../mineru_vl_utils/vlm_client/http_client.py) |
| A2 | 超时层层叠加：部署配置中单请求读超时 1800s、单次解析 2500s（源码默认都是 600s），runner 再整篇重试 3 次（[agent_runner.py:802](../web/backend/app/services/agent_runner.py)），HTTP 传输层还有 `Retry(total=10)`。解析超时从拿到文档槽位后才开始计时，排队时间不计入（[mineru_parser.py:393](../core/parsers/mineru_parser.py)）。没有逐页进度 | 线上日志两次都正好卡满 2500s |
| A3 | 渲染在多线程进程中以 fork 方式启动进程池；pypdfium2（本地为 4.30，requirements 只约束 `>=`）非线程安全，却被父进程线程、并发渲染、MCP 上传校验多处同时调用。渲染自带的超时从 `wait()` 才开始，覆盖不到之前的打开 PDF 和提交任务 | [pdf_image_tools.py:103/140](../mineru/utils/pdf_image_tools.py)、[vlm_analyze.py:24](../mineru/backend/vlm/vlm_analyze.py)、[mcp/service.py:267](../mcp/service.py) |
| A4 | 整篇文档的 200dpi 页面图同时驻留内存，并通过进程间 pickle 传输 | [vlm_analyze.py:252](../mineru/backend/vlm/vlm_analyze.py) |
| A5 | MinerU 客户端是进程级单例，缓存键缺少超时、并发、认证、重试配置；按事件循环缓存异步客户端，清空缓存时不关闭旧客户端 | [vlm_analyze.py:50](../mineru/backend/vlm/vlm_analyze.py)、[http_client.py:126](../mineru_vl_utils/vlm_client/http_client.py) |

> 线上卡住的确切位置还没有证实：日志只能确定卡在渲染阶段，而渲染自带的超时又覆盖不到。阶段 0 补上栈转储后，下次复现即可定位。

**B 事件循环阻塞**

| 编号 | 问题 | 证据 |
|---|---|---|
| B1 | 在 async 函数里同步构造 MinerU 客户端，构造时同步 GET `/v1/models`；构造失败不会缓存 | [vlm_analyze.py:248](../mineru/backend/vlm/vlm_analyze.py)、[http_client.py:148](../mineru_vl_utils/vlm_client/http_client.py) |
| B2 | 在事件循环上执行 PNG 编码、base64、切图写盘、大 JSON 序列化 | [http_client.py:428](../mineru_vl_utils/vlm_client/http_client.py)、[vlm_analyze.py:273](../mineru/backend/vlm/vlm_analyze.py)、[mineru_parser.py:519](../core/parsers/mineru_parser.py) |
| B3 | 每个 HTTP 请求、每条 WS 消息都在事件循环上做 2 次 SQLite 读加 1 次写提交（会话 touch）；busy_timeout 30s，多进程写同一个库 | [auth.py:192-199](../web/backend/app/auth.py)、[database.py:424](../web/backend/app/database.py) |
| B4 | 多数路由在 `async def` 里做同步文件和数据库 IO；批量 WS 每秒同步轮询一次事件表，并且总是从最新事件开始推送，客户端无法按游标回放 | [files.py:180](../web/backend/app/api/routes/files.py)、[batch.py:420-443](../web/backend/app/api/routes/batch.py) |

**C 执行与状态**

| 编号 | 问题 | 证据 |
|---|---|---|
| C1 | 三套任务生命周期；Web 单任务没有持久化，并与前端会话耦合 | 见 1.1 |
| C2 | 线程无法强杀；`run_blocking` 超时后仍占着槽位（这是合理的防重叠保护），但 `aclose()` 可能无限等待线程 | [execution.py:247/337](../core/runtime/execution.py) |
| C3 | 批量续租时不检查租约是否已过期，缺少 fencing | [batch_jobs.py:979](../web/backend/app/services/batch_jobs.py) |
| C4 | 进程级全局状态：进度回调是模块全局变量；导入时改写 `os.environ`；`_task_contexts` 只增不减 | [appraiser_batch.py:54](../core/agents/tools/appraiser_batch.py)、[model_config.py:43](../core/agents/base/model_config.py)、[public_tools.py:18](../core/agents/tools/public_tools.py) |
| C5 | LLM 调用有三套写法（litellm 同步、litellm 异步、ADK），超时、重试、预算、并发闸门分散；全局闸门用 50ms 轮询实现 | `core/rule_engine/litellm_client.py`、`core/preprocessor/labeler.py`、`core/utils/llm_limiter.py` |

**D 交付与运维**

| 编号 | 问题 | 证据 |
|---|---|---|
| D1 | `/ama/health` 恒返回 ok；认证关闭时 `/ama/ready` 不检查数据库；Web 进程没开 faulthandler；worker 健康检查关闭；容器没有内存和 PID 限制 | [health.py](../web/backend/app/api/routes/health.py)、`docker-compose.yml` |
| D2 | 无 CI，无 lint 和类型检查配置，无锁文件；52 个依赖中 40 个只写了 `>=` 下限 | `pyproject.toml`、`requirements.txt` |
| D3 | `mineru_vl_utils` 有两个来源：requirements 安装了 `mineru-vl-utils`（本地 0.1.19.1），但运行时被仓库内嵌的修改版遮蔽；`pyproject` 打包又排除了 `mineru*` | [requirements.txt:58](../requirements.txt)、[pyproject.toml:97](../pyproject.toml) |
| D4 | 大量死代码，见第 5 节 | 静态 import 图 + 运行时加载实测 |

## 2. 目标架构

```
 浏览器 / MCP 客户端
        │  REST：提交 / 停止 / 续跑（幂等键）
        │  SSE ：事件流（Last-Event-ID 回放）
        ▼
┌─────────────────────────────────────┐
│ api 进程（FastAPI）                   │  认证、上传、校验、入队、事件推送、MCP HTTP
│  · EventHub：每进程一个轮询器，按订阅者分发 │  不执行任何流水线代码
└──────────────────┬──────────────────┘
                   │ SQLite WAL：task_groups / tasks / task_runs / task_events / auth
┌──────────────────┴──────────────────┐
│ worker（supervisor）                  │  领取、续租、lane 调度、配额分配、
│  · 看门狗：观察实际进展                  │  无进展判定、取消与强杀、结果发布
└──────┬──────────────┬───────────────┘
       │ spawn        │ spawn            每次执行一个子进程（独立进程组）
┌──────┴──────┐ ┌─────┴───────┐
│ run 进程     │ │ run 进程     │  parse → preprocess → appraise → report
│ ParseService│ │ ...         │  ParseService ─► MinerU VLM
│ LLMGateway  │ │             │  LLMGateway  ─► LLM 服务
└─────────────┘ └─────────────┘  产物写入 output/<task_id>/runs/<run_token>/
```

设计原则：

- **命令和事件分离**：REST 负责发命令，SSE 负责推事件。事件按自增 id 持久化，断线后浏览器自动带上 `Last-Event-ID` 补读。前面修过的"终态消息丢失"和"会话 ID 不一致"这类问题，在这个模型下不会再出现。
- **API 无状态**：执行状态全部在数据库里，API 重启不影响任务。
- **执行隔离**：每次执行在独立子进程中运行。取消时先协作停止，超过宽限期就强杀整个进程组。线程无法强杀、内存碎片、pdfium 线程安全、全局状态串扰这几类问题，都由进程边界一次性兜住。
- **结构化并发**：进程内部用 Python 3.11 的 `asyncio.TaskGroup` 和 `asyncio.timeout`；任一子任务失败或被取消，兄弟任务一并取消。
- **核心与边界分离**：编排代码迁到 `core/`，只输出事件代码和参数；Web 和 MCP 在边界层负责国际化展示和协议适配。
- **外部依赖走适配层**：MinerU 只通过 `ParseService` 访问，LLM 只通过 `LLMGateway` 访问。

### 2.1 模块布局（目标）

```
core/
  jobs/            # TaskStore（仓储）、状态机、事件写入与回放游标、领取与 fencing
  pipeline/        # AppraisalPipeline：阶段编排、Deadline 预算、进度上报（由 AppraisalRunner._run 拆出）
  parsing/         # ParseService、MinerU 客户端、渲染器、逐页检查点
  llm/             # LLMGateway（扩展现有 context.py / json_output.py / limiter）
  runtime/         # ExecutionRuntime、Deadline、取消令牌
web/backend/app/
  worker/          # supervisor、run 进程入口、lane 调度、看门狗
  api/routes/tasks.py  # REST + SSE；chat.py / batch.py 先作为兼容适配层保留，前端切换后删除
  events.py        # EventHub
mcp/               # 只保留协议适配（上传、错误码映射），执行交给统一 worker
```

## 3. 关键设计

### 3.1 统一任务模型

一份 PDF 的一轮评价就是一个任务。批量是任务分组，带轮次依赖和并发配额。

| 表 | 核心字段 |
|---|---|
| `task_groups` | `id, owner_id, source(batch/mcp/web), rounds, concurrency, cancel_requested, created_at` |
| `tasks` | `id, group_id, owner_id, source, lane, round_no, depends_on, input_path, input_hash, config_hash, status, phase, deadline_at, next_run_at, cancel_requested, idempotency_key, result_ref, status_reason` |
| `task_runs` | `task_id, attempt, run_token, worker_id, pid, lease_until, heartbeat_at, progress_at, error_code, usage_json, started_at, ended_at` |
| `task_events` | `id INTEGER PRIMARY KEY AUTOINCREMENT, task_id, group_id, run_token, type, payload_json, created_at` |

索引：领取 `(status, lane, next_run_at)`、事件 `(task_id, id)` 和 `(group_id, id)`、`(owner_id, idempotency_key)` 唯一。配置快照只保存非敏感参数和凭据引用。

```
queued ──claim──► running ──► succeeded | partial | failed
   │                 ├──► retry_wait ──► queued        （可重试的失败；预算不重置）
   │                 └──► cancelling ──► cancelled     （进程确认退出后才进入）
   └──► cancelled
```

- **领取与 fencing**：领取用短事务加条件更新。心跳、进度、终态提交都必须同时匹配 `run_token`、未过期的租约和允许的当前状态（顺便修复 C3）。
- **至少一次执行**：产物写入 `output/<task_id>/runs/<run_token>/`，校验通过后，在受保护的事务里发布 `result_ref`，旧执行无法覆盖新结果。检查点（逐页解析、标签、条目评价）绑定输入和配置的指纹，重试时可以复用。缺少的条目保持 `partial`，不能伪装成完成。
- **多轮实验**：第 2 轮及以后是新任务（`depends_on` 指向第 1 轮），复用第 1 轮的预处理产物，不算作重试。
- **状态与事件同事务提交**。快照和游标在同一个读事务中获取，保证一致。
- **迁移**：新表增量创建；写一个幂等的导入脚本，把 `batch_*` 和 `mcp_jobs` 的历史导入新表。旧表保留一个版本、只读，之后删除。`mcp_jobs` 中的 MCP 专有字段（上传物、错误码、恢复次数）迁到 `mcp_task_meta`。

### 3.2 事件推送

- `GET /ama/api/tasks/{id}/events` 和 `GET /ama/api/groups/{id}/events` 使用 SSE；浏览器的 `EventSource` 自带重连和 `Last-Event-ID`。nginx 对这两个路径关闭 `proxy_buffering`。
- **EventHub**：每个 API 进程只有一个轮询器（数据库访问在专用的数据库线程上，每 0.5s 一次），把新事件分发给各订阅者的有界队列；慢连接队列满了就断开，让它重连补读。
- **事件分级**：页面进度、条目进度这类高频事件，只保留最新一条（覆盖式写入）；阶段切换、条目完成、终态完整保留。设置保留期，游标过期时先返回快照，再从当前位置继续推送。
- 订阅时校验所有权（`owner_id`），管理员可以查看全部任务。

### 3.3 截止时间、看门狗与真正的取消

- **Deadline**：根任务持久化一个绝对截止时间，进程内转成单调时钟预算，并通过 contextvar 往下传：

  `子调用截止时间 = min(父截止时间, 当前时间 + 阶段预算)`

  排队、信号量等待、退避、重试都要消耗预算，重试时不重置。解析超时改为把等待槽位的时间也计入。
- **看门狗**：运行在 supervisor 里，分别观察心跳和**实际进展**（已渲染页、已识别区块、已完成条目）。写日志、续租都不算进展。停滞超过阈值（解析默认 5 分钟），以 `*_stalled` 失败；最终的硬截止时间仍然保留。
- **取消**：supervisor 先通知 run 进程协作停止（停止派发、取消 HTTP 请求、等待子任务清理）；宽限期过后，对整个进程组发 SIGTERM，再不行就 SIGKILL 并回收。确认进程已退出，才标记 `cancelled` 并释放槽位。
- **线程只用于短阻塞操作**，不承担硬取消的保证；`run_blocking` 保留占槽语义，在运行时快照里暴露 `draining` 计数。

### 3.4 解析层重构（`core/parsing/`）

内嵌的 MinerU 代码只保留结果转 middle_json 和 Markdown 的纯函数部分，网络和调度由 `ParseService` 负责。

1. **请求清理**：修复 `gather_tasks` 和迭代版聚合器，在异常、取消、提前退出时取消并等待全部子任务；或者在适配层用 `TaskGroup` 重写调度。
2. **客户端生命周期**：每个 run 进程只有一个异步客户端，任务结束时显式关闭。构造和模型名校验改为异步，不再阻塞事件循环。过渡期如果保留缓存，缓存键必须包含 endpoint、model、认证配置版本、timeout、concurrency、retry 策略，且禁止跨事件循环共享。
3. **请求级超时**：`httpx.Timeout(connect=10, read=180, write=60, pool=30)`，外层再套一个由 `Deadline` 计算的 `asyncio.timeout`；遇到 429、5xx 或超时，带抖动重试，每个请求最多 2 次，并关闭 SDK 自带的隐式重试。
4. **逐页恢复**：按原始页码保存检查点，检查点绑定文件、模型和解析配置的指纹，只重试失败的页；页内仍然先做版面检测、再做内容识别。合并时保持全局 `block_id`、阅读顺序和跨页表格合并规则。删除 runner 层"整篇重试 3 次"的逻辑。
5. **渲染隔离**：pdfium 的打开文档、读页尺寸、裁页，全部放进显式 `spawn` 的有界渲染进程；父进程只传路径和页号，不传 bytes。MCP 上传校验也走这个渲染进程。
6. **内存控制**：建立有界流水线：分批渲染（例如每批 8 页）→ 识别 → 落盘 → 释放图片；限制页数、像素和在途字节数。
7. **CPU 卸载**：区块裁剪、缩放、PNG 编码和 base64、`result_to_middle_json`、大 JSON 写盘，放进专用的有界线程池（新增 `ExecutionStage.CPU`）。
8. **健康检查**：保留轻量的 `/v1/models` 检查，改为异步、短超时、短缓存。

### 3.5 API 与数据库访问

- **数据库线程**：每个进程一个专用的数据库执行线程，连接和完整事务都在这个线程上完成，async 代码通过它访问 SQLite。API 路径的 busy_timeout 缩短到 5s，并有限重试，超限返回 503。
- **认证**：每次请求仍然校验会话（保证撤销立即生效），但校验放到数据库线程上执行；`last_seen` 按时间间隔更新（每个会话 60s 最多写一次）。SSE 和 WS 不再逐条消息查库，撤销由现有的 `session_registry` 主动踢下线。
- **路由**：IO 密集的路由改为同步 `def`（交给线程池）或显式 `run_in_threadpool`；上传改为分块流式写入。
- **事件循环延迟监控**：后台协程每秒测一次调度延迟，超过 200ms 告警并输出线程栈。

### 3.6 LLM 网关与全局状态

```python
# core/llm/gateway.py
class LLMGateway:
    def complete(self, request: LLMRequest, *, context: CallContext) -> LLMResponse: ...
    async def acomplete(self, request: LLMRequest, *, context: CallContext) -> LLMResponse: ...
    def adk_model(self, context: CallContext) -> LiteLlm: ...   # Reporter 等 ADK 智能体使用
```

- `CallContext` 统一携带：配置快照、Deadline、取消令牌、请求预算与恢复预算、用量记录。ADK 适配层的每次请求都要执行同一套策略。
- 关闭 SDK 自带的隐式重试，重试只在网关里做一次。规则引擎、labeler、appraiser、reporter 依次迁移到网关。
- **跨进程配额**：并发上限会随进程数成倍放大，所以 supervisor 按 run 进程分配保守的 LLM 和 MinerU 配额，例如全局上限除以并发执行数，交互 lane 保底；进程内的闸门改为阻塞式信号量，不再轮询。
- **全局状态**：
  - 删除全局进度回调，改为显式传入 `ProgressReporter`。
  - 删除导入时对 `os.environ` 的改写，凭据按调用显式传入。
  - `_task_contexts` 改为由 task_id 推导路径。
  - 客户端和网关的生命周期由进程上下文统一管理。

### 3.7 可观测性与运维

- API、supervisor、run 进程都开启 `faulthandler`，注册 `SIGUSR1` 用于输出全部线程栈；镜像内置 `py-spy`。
- 结构化日志：`task_id`、`run_token`、`stage` 通过 contextvar 自动注入。
- 指标写入事件和状态接口：各阶段耗时、最后进展时间、池等待、在途请求数、RSS、draining 计数。
- 健康检查：
  - `/health` 保持存活语义，加入事件循环延迟检查。
  - `/ready` 无论认证是否开启，都检查数据库和 worker 心跳；不请求模型推理。
  - 新增 `/ama/api/system/status`，展示队列深度、各 lane 占用、卡住的任务。
- Compose：设置内存和 PID 上限、`stop_grace_period`、`init: true`（回收子进程）、日志轮转；worker 用心跳时间戳做健康检查。

## 4. 分阶段实施

每个阶段可独立上线、独立回滚。阶段 0 和 1 不改对外接口。

| 阶段 | 内容 | 主要改动 | 验证 | 回滚 |
|---|---|---|---|---|
| **0 清理与诊断**（1–2 天） | 删除第 5 节死代码；统一 `mineru_vl_utils` 来源；栈转储、事件循环延迟监控、逐页进度日志 | 见第 5 节；三个进程入口；`mineru_parser.py` | 全量 pytest；三个进程冒烟；镜像体积对比 | git revert |
| **1 解析止损**（4–6 天） | 3.4 中的 1、2、3、5、6、7 项；Deadline 语义（含等槽时间）；无进展看门狗（进程内版本）；修复续租 fencing（C3） | `mineru_vl_utils/vlm_client/{utils,http_client}.py`、`mineru/utils/pdf_image_tools.py`、`mineru/backend/vlm/vlm_analyze.py`、`core/parsers/mineru_parser.py`、`core/runtime/`、`batch_jobs.py` | 用可控的假 VLM 服务做故障注入（挂起、500、慢响应、中途断开、渲染卡死）：在停滞阈值内以明确原因失败；取消后连接、进程、槽位全部恢复；两篇文档并发时没有 pdfium 并发调用；大 PDF 内存峰值有上界 | `PARSE_ENGINE=legacy\|v2` 开关；新任务灰度 |
| **2 统一任务与进程隔离**（2–3 周） | 3.1–3.3：新表与迁移脚本、TaskStore、supervisor + run 子进程、lane、SSE 与 EventHub、逐页检查点（3.4 第 4 项）；前端 Chat 页和 Batch 页切换；MCP 接入 | `core/jobs/`、`core/pipeline/`、`web/backend/app/worker/`、`api/routes/tasks.py`、`events.py`、前端 `pages/Chat.tsx`、`pages/Batch.tsx`、`services/`、`mcp/worker.py`、`mcp/repository.py` | 竞争领取、租约失效、kill -9 supervisor 和 run 进程后的恢复、API 重启、幂等提交、断线回放、所有者隔离；批量满载时交互任务的排队时间 | 按执行版本分流，禁止新旧 worker 重复消费；旧 `/chat`、`/batch` 接口保留到前端完全切换 |
| **3 调用与资源治理**（1 周） | 3.5、3.6：数据库线程、认证节流、路由线程化、上传流式写入、LLMGateway、跨进程配额、全局状态清理、编排代码与 i18n 解耦 | `core/llm/`、`core/rule_engine/litellm_client.py`、`core/preprocessor/labeler.py`、`core/agents/base/model_config.py`、`web/backend/app/{auth,database}.py`、各路由 | 混合长短 PDF 压测：API 延迟、事件延迟、RSS、取消耗时、总请求数；同一份 PDF 改造前后评分一致 | 按任务固定使用新或旧策略 |
| **4 交付固化**（2–3 天） | `uv` 锁定依赖（含传递依赖）；ruff + mypy（先覆盖 `core/runtime`、`core/parsing`、`core/jobs`）；GitHub Actions；Compose 资源配置；`/system/status` | `pyproject.toml`、锁文件、`.github/workflows/`、`docker-compose.yml`、Dockerfile | CI 全绿；用新镜像完整回归 | 回退到旧镜像 |

阶段 1 是止损，见效最快；阶段 2 是架构主体，风险最高，也是"统一模型 + 进程隔离"真正落地的地方。

## 5. 死代码与重复实现

判定依据：

- 从生产入口（`web.backend.app.main`、`web.backend.app.batch_worker`、`mcp/run_worker.py`、`cli.py`）出发，构建静态 import 图。
- 加上运行时实测：内嵌的 186 个 MinerU 源文件中，HTTP 解析路径只加载约 60 个。
- Codex 独立静态解析了 432 个 Python 文件，两份结论交叉核对。

**可直接删除**

| 路径 | 依据 |
|---|---|
| `web/backend/app/services/batch_runner.py`（840 行） | 唯一引用来自 `tests/test_batch_jobs.py:21`，与 `batch_worker_runner.py` 大量重复；把仍有价值的行为测试迁走后删除；`batch_identity.py` 保留 |
| `web/backend/app/api/routes/extract.py` | 占位实现，`main.py` 没有挂载，前端没有调用 |
| `core/agents/extractor.py`、`core/agents/validator.py` | 旧知识图谱智能体，无调用方，引用的工具已删除 |
| `core/agents/appraiser_deprecated.py`、`core/parsers/demo.py` | 没有任何入边；前者自己声明已弃用 |
| `core/agents/base/task_context.py` | 只被 `base/__init__.py` 重新导出，没有使用方 |
| `core/agents/base/schemas.py` 中的 `ExplorerInput`、`EvaluationOutput`、`SemanticLabelOutput` | 无引用；`ExtractorInput`、`ValidatorInput`、`AppraiserInput` 只被上面要删除的模块使用，一并删除 |
| `mineru/model/**`、`mineru/backend/pipeline/**` | 顶层直接 import torch，而生产依赖没有 torch，根本加载不了 |
| `mineru/utils/{run_async,format_utils,cli_parser}.py` | 没有使用方；`format_utils` 与在用的 `post_process/otsl2html.py` 重复 |
| `mineru_vl_utils/logits_processor/vllm_v0_no_repeat_ngram.py` | 无引用 |

**连同工厂分支一起裁剪**（服务限定为 HTTP 客户端模式）

| 路径 | 说明 |
|---|---|
| `mineru_vl_utils/vlm_client/{transformers,mlx,lmdeploy_engine,vllm_engine,vllm_async_engine}_client.py` | 被 `base_client.py:193` 的工厂分支引用，删除时连同工厂分支和 `vlm_analyze.py` 中加载本地模型的分支一起删 |
| `mineru/utils/models_download_utils.py`、`mineru/data/io/s3.py` 及其导入点 | 前者只服务本地模型下载，后者是 S3 读写；切断导入后可以移除 `modelscope`、`huggingface-hub`、`boto3` 依赖 |

**需要确认后处理**（独立工具，不属于服务链路）

| 路径 | 说明 |
|---|---|
| `mineru/cli/{client,fast_api,gradio_app,models_download,vlm_server}.py` | 有独立入口；`cli/common.py` 仍被 parser 使用，必须保留，或者先把需要的函数迁出 |
| `docvlm/` | 独立的解析工具和诊断脚本，与服务无关；确认没人使用后移出仓库 |
| `benchmark/__main__.py`、`benchmark/cli.py`、`experiments/` | 研究用 CLI 和脚本；移到 `tools/` 或单独的研究仓库，镜像构建时排除 |

**必须保留**：`docindex/`（预处理会调用）、批量兼容路由（前端 `services/batch.ts` 仍在调用，阶段 2 切换后再删）。

**依赖来源**：`mineru-vl-utils` 只保留一个来源。建议移除 pip 包，保留瘦身后的内嵌版本，并在 `pyproject` 中把它纳入打包。

每一项删除前用 `rg` 复核字符串引用和动态导入，删除后跑全量测试和三个进程的冒烟测试。死代码清理单独成一个 PR，不与功能改动混在一起。

## 6. 权衡与明确不做的事

- **继续用 SQLite**：单机 WAL 加短事务足够。将来需要跨机器部署 worker 时，再迁移到 PostgreSQL，同时解决产物共享问题；禁止通过网络文件系统共享 SQLite。
- **不引入 Redis、Celery、RQ**：现有数据库队列已经提供了领取、租约和事件回放；引入 broker 之后，业务状态和结果的一致性仍然要自己维护。
- **子进程启动有成本**：每次执行需要几秒的导入时间，相对于分钟级的评价可以接受。静态配额可能降低资源利用率，先用它换取故障隔离，之后根据压测再调整。
- **不做**：MinerU 真实推理就绪检查与熔断、K8s、Kafka、通用工作流平台。
- **边界说明**：强杀本地进程可以回收本地资源，但远端推理是否随连接断开而停止，取决于 MinerU 服务端。
- **阶段 2 风险最高**：通过执行版本分流、保留旧接口、契约测试、逐个页面切换来控制。
