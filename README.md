# AMA (AGREE II Multi-Agents)

**English** | [简体中文](README.zh-CN.md)

AMA appraises clinical practice guidelines (PDF) with the AGREE II instrument. A multi-agent pipeline parses the guideline, scores the 23 items, and writes a report in which each judgement links back to the sentences it is based on.

## Features

- **Full AGREE II coverage.** 6 domains and 23 items, with scaled domain scores computed by the official AGREE II formula.
- **Three appraisal engines.** `llm` scores an item by iterative reading. `rule` extracts structured facts and scores them with item rules. `hybrid` (default) chooses one of the two paths for each item with a trained routing model.
- **Traceable evidence.** Reports quote the source sentences and locate them in the original PDF.
- **Configurable items.** Each item is a YAML Skill (`core/skills/item_<n>.yaml`) with its scoring principles, editable from the web UI.
- **Web platform.** Single appraisal with live progress, batch appraisal, report browsing, comparison with human scores, model configuration, and user management. The interface is available in English and Chinese.
- **MCP endpoint.** Other agents can submit a PDF and fetch the appraisal result over the Model Context Protocol.

## How it works

```
PDF ──► Parsing ──► Evidence pool ──► Item appraisal (23 items) ──► Report
        MinerU      chunks, labels,    hybrid router                Reporter
                    attachments        ├─ rule engine               domain scores,
                                       └─ LLM appraiser             cited evidence
```

1. **Parsing.** MinerU converts the PDF to structured Markdown. Results are cached per document.
2. **Evidence pool.** The preprocessor splits the document into chunks, labels them by AGREE II domain, builds the heading hierarchy, and reads attachments (PDF, Word, Excel, PowerPoint, URLs).
3. **Item appraisal.** Items run concurrently. In `hybrid` mode the router picks the rule engine or the LLM appraiser per item. Item 17 always uses the LLM path. A failed rule path can fall back to the LLM path and is marked as degraded.
4. **Report.** The Reporter computes domain scores and writes `appraisal_report.json` and `appraisal_report.md`.

The pipeline lives in `core/pipeline/` and only emits events. The web UI, the batch worker, and the MCP server are three entry points to the same pipeline.

## Quick start

### Requirements

- Python 3.11 and Node.js 20 or later
- An LLM service. Supported providers: vLLM, OpenAI-compatible, OpenAI, Anthropic, DeepSeek, Gemini, DashScope, Zhipu
- A [MinerU](https://github.com/opendatalab/MinerU) VLM server for PDF parsing

### 1. Install

```bash
conda create -n ama python=3.11
conda activate ama
pip install -r requirements.txt

cd web/frontend && npm install
```

### 2. Configure

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME` | Appraisal model. Can also be set on the Config page |
| `LLM_CONTEXT_WINDOW` | Context window of the deployed model in tokens. Can also be set on the Config page |
| `MINERU_SERVER_URL`, `MINERU_API_KEY` | MinerU VLM server |
| `ANALYSIS_ENGINE` | `hybrid` (default), `rule`, or `llm` |
| `AMA_AUTH_ENABLED` | Web login, `true` by default |
| `AMA_AUTH_SECRET`, `LLM_CONFIG_SECRET` | Session signing key and model-config encryption key. Set both in production |
| `AMA_MCP_ENABLED`, `AMA_MCP_API_KEY` | MCP endpoint, off by default |

`.env.example` also covers concurrency limits, rule-engine policies, and MCP limits.

When login is enabled, create the first accounts with a seed file. On first start with an empty database, AMA imports `users.json` from the project root:

```json
{
  "users": [
    { "username": "admin", "password": "<your-password>", "display_name": "Admin", "role": "admin" }
  ]
}
```

Roles are `admin` and `user`. Passwords are stored hashed in `data/ama.db`. Keep `users.json` out of version control. More accounts can be added later on the Users page.

### 3. Run

```bash
./service.sh start     # backend, batch worker, MCP worker (if enabled), frontend
./service.sh status
./service.sh stop
./service.sh restart
```

The script expects a conda environment named `ama`. To start the processes by hand:

```bash
uvicorn web.backend.app.main:app --host 0.0.0.0 --port 8011
python -m web.backend.app.batch_worker
cd web/frontend && npm run dev
```

| Service | URL |
|---|---|
| Web UI | http://localhost:8012/ama |
| API docs (Swagger) | http://localhost:8011/ama/api-docs |
| Health check | http://localhost:8011/ama/health |

### 4. First appraisal

1. Open **Config** and check that the model is reachable and the context window is set.
2. Open **Appraisal**, upload or select a guideline PDF, and start.
3. Open **Reports** to read the scores and the cited evidence.

See the [user manual](docs/USER_MANUAL_EN.md) for each page.

## Docker

`docker-compose.yml` runs prebuilt images. Build them first:

```bash
docker build -f web/backend/Dockerfile -t ama-backend:0.1 .
docker build -t ama-frontend:0.1 web/frontend
docker compose up -d
```

The frontend is served at http://localhost:8012/ama and the backend at port 8011. Add `--profile mcp` to start the MCP worker.

## MCP

Set `AMA_MCP_ENABLED=true` and `AMA_MCP_API_KEY`. The backend then serves a Streamable HTTP endpoint at `/ama/mcp/` and an upload endpoint at `/ama/mcp/uploads/` on port 8011. Both use `Authorization: Bearer <API key>`.

| Tool | Purpose |
|---|---|
| `submit_pdf_appraisal` | Queue a PDF for appraisal |
| `get_appraisal_status` | Poll progress |
| `get_appraisal_result` | Fetch the finished report |
| `cancel_appraisal` | Cancel a queued or running job |

Details are in [docs/AMA_MCP_Integration_Guide.pdf](docs/AMA_MCP_Integration_Guide.pdf).

## AGREE II domains

| Domain | Items | Name |
|---|---|---|
| 1 | 1–3 | Scope and Purpose |
| 2 | 4–6 | Stakeholder Involvement |
| 3 | 7–14 | Rigour of Development |
| 4 | 15–17 | Clarity of Presentation |
| 5 | 18–21 | Applicability |
| 6 | 22–23 | Editorial Independence |

## Model context window and failure recovery

- New appraisals need the context window of the deployed model. Set it on the Config page or with `LLM_CONTEXT_WINDOW`. When the model ID matches the bundled model catalog, a suggested value is filled in.
- A running document keeps the model configuration it started with.
- All model requests go through `core/llm/gateway.py`, which applies the token budget, the concurrency limits, and usage accounting. Inputs that exceed the window are split and merged by stage-specific rules. An input that cannot be split fails explicitly, and no evidence is dropped silently.
- Each item has a recovery quota shared by transport retries, truncation retries, and JSON repair (`LLM_RETRY_QUOTA_*`, 8 attempts in total by default).
- A missing judgement, an invalid citation, or a parse failure is reported as a failure and is never converted into a low score. Failures are expressed as `FailureCode` values in `core/pipeline/`, and each entry point maps them to its own messages.

## Project structure

```
core/                  Appraisal engine, independent of the web layer
  pipeline/            Orchestration of one appraisal: parse → evidence → items → report
  agents/              Appraiser, Reporter, Explorer (attachments), Portal (chat)
  rule_engine/         Fact extraction, verification, and item scoring rules
  hybrid_router/       Per-item choice between the rule engine and the LLM path
  skills/              The 23 AGREE II item definitions (YAML)
  llm/                 Gateway, context budget, structured output, recovery
  preprocessor/        Chunking, domain labels, evidence pool
  attachment/          Attachment and URL parsing
  parsers/  prompts/  config/  runtime/  observability/  utils/
web/backend/           FastAPI service (port 8011, prefix /ama), SQLite at data/ama.db
web/frontend/          React 19 + Vite + Tailwind CSS (port 8012, base /ama/)
mcp/                   MCP server, upload endpoint, and worker
mineru/  mineru_vl_utils/   MinerU client for PDF parsing
docindex/              Heading hierarchy detection
docvlm/                Standalone VLM-based PDF parser (CLI)
benchmark/             Agreement metrics against human scores
docs/                  User manuals and design notes
service.sh             Start, stop, and status for local services
docker-compose.yml     Container deployment
```

Each task writes to `output/<file name>-<timestamp>/`:

```
source.md                Parsed guideline
appraisal_report.json    Final report
appraisal_report.md
.ama/prep/               Evidence pool and preprocessing results
.ama/evaluations/        Per-item results
```

## Tech stack

| Layer | Technology |
|---|---|
| Agents | Google ADK, LiteLLM |
| Backend | FastAPI, Pydantic v2, SQLite |
| PDF parsing | MinerU (VLM HTTP client) |
| Routing model | scikit-learn |
| Integration | Model Context Protocol |
| Frontend | React 19, TypeScript 5, Vite 7, Tailwind CSS 4, react-router 7, i18next |

## Testing

```bash
python -m pytest -q web/backend/tests
cd web/frontend && npm test
```

The tests do not call a real model.

## Documentation

| Document | Content |
|---|---|
| [docs/USER_MANUAL_EN.md](docs/USER_MANUAL_EN.md), [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | User manual in English and Chinese |
| [docs/AMA_MCP_Integration_Guide.pdf](docs/AMA_MCP_Integration_Guide.pdf) | MCP integration |
| [docs/I18N.md](docs/I18N.md) | Interface messages and language rules |
| [docs/STRUCTURED_OUTPUT.md](docs/STRUCTURED_OUTPUT.md) | Structured model output |
| [docs/RULE_ENGINE_RELIABILITY_PLAN.md](docs/RULE_ENGINE_RELIABILITY_PLAN.md) | Rule-engine failure handling |
| [docs/ARCHITECTURE_OPTIMIZATION_PLAN.md](docs/ARCHITECTURE_OPTIMIZATION_PLAN.md) | Architecture and performance design |

## License

[MIT](LICENSE)
