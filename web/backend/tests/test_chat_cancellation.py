"""单任务终止：解析挂起时停止请求须立即生效，会话快照用于界面校正。"""

import asyncio
from types import SimpleNamespace

import core.agents
import core.parsers
import core.parsers.mineru_cache
from core.agents.tools import task_manifest
from core.llm import context as llm_context
from core.utils.cancellation import CancellationToken
from web.backend.app.api.routes import chat
from core.pipeline import runner as pipeline_runner
from web.backend.app.services.agent_runner import AppraisalRunner


class InlineRuntime:
    async def run_blocking(self, stage, function, *args, serialization_key=None, **kwargs):
        return function(*args, **kwargs)


class HangingParser:
    """模拟 MinerU 服务健康但解析请求一直不返回。"""

    started: asyncio.Event
    interrupted = False

    def __init__(self):
        self.server_url = "http://mineru.test"

    async def health_check(self):
        return True

    async def parse_pdf_to_file(self, **kwargs):
        HangingParser.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            HangingParser.interrupted = True
            raise


def _stub_until_parse(monkeypatch, tmp_path):
    task_dir = tmp_path / "task-stop"
    prep_dir = task_dir / ".ama" / "prep"
    prep_dir.mkdir(parents=True)
    paths = {"task_dir": str(task_dir), "source": str(task_dir / "source.md"), "prep_dir": str(prep_dir), "weaver_dir": str(task_dir / ".ama")}
    settings = SimpleNamespace(mineru=SimpleNamespace(server_url="http://mineru.test", parse_retry_attempts=3, parse_retry_delay=0))

    monkeypatch.setattr(llm_context, "current_llm_settings", lambda: SimpleNamespace(context_window=8192, model_name="test"))
    monkeypatch.setattr(core.agents, "init_task_directory", lambda *args, **kwargs: {"status": "success", "task_id": "task-stop", "paths": paths})
    monkeypatch.setattr(task_manifest, "ensure_task_manifest", lambda *args: None)
    monkeypatch.setattr(core.parsers.mineru_cache, "restore_document_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(core.parsers, "MinerUParser", HangingParser)
    monkeypatch.setattr(pipeline_runner, "get_settings", lambda: settings)


def test_stop_interrupts_hanging_mineru_parse(monkeypatch, tmp_path):
    pdf = tmp_path / "guide.pdf"
    pdf.write_bytes(b"%PDF")
    cancelled, errors = [], []
    runner = AppraisalRunner(
        on_cancelled=cancelled.append,
        on_error=errors.append,
        runtime=InlineRuntime(),
        cancellation_token=CancellationToken(),
        refresh_model_config_on_start=False,
    )
    _stub_until_parse(monkeypatch, tmp_path)

    async def scenario():
        HangingParser.started = asyncio.Event()
        HangingParser.interrupted = False
        run = asyncio.create_task(runner.run("task-stop", str(pdf)))
        await asyncio.wait_for(HangingParser.started.wait(), timeout=2)
        runner.cancel()
        # 修复前要等满 MinerU 解析超时（线上为 2500s）才会响应停止
        await asyncio.wait_for(run, timeout=2)

    asyncio.run(scenario())

    assert cancelled == ["task-stop"]
    assert errors == []
    assert HangingParser.interrupted is True


def test_stop_before_run_starts_is_not_reset():
    cancelled, errors = [], []
    runner = AppraisalRunner(
        on_cancelled=cancelled.append,
        on_error=errors.append,
        cancellation_token=CancellationToken(),
        refresh_model_config_on_start=False,
    )
    runner.cancel()

    asyncio.run(runner.run("task-early-stop", "/missing.pdf"))

    assert cancelled == ["task-early-stop"]
    assert errors == []


def test_session_state_reflects_active_task(monkeypatch):
    monkeypatch.setattr(chat, "active_task_handles", {})
    assert chat.session_state_message("session-a") == {"type": "session_state", "task_id": None, "state": "idle"}

    async def scenario():
        pending = asyncio.create_task(asyncio.Event().wait())
        chat.active_task_handles["session-a"] = chat.SessionTaskHandle(task_id="task-a", runner=None, asyncio_task=pending, state="stopping")
        active = chat.session_state_message("session-a")
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        return active, chat.session_state_message("session-a")

    active, finished = asyncio.run(scenario())

    assert active == {"type": "session_state", "task_id": "task-a", "state": "stopping"}
    assert finished["state"] == "idle"
