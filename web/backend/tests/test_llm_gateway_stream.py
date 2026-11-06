"""模型网关流式请求：超时只约束相邻两段数据的间隔，不约束整体耗时。

用本地 SSE 服务模拟 OpenAI 兼容端点，验证真实的 LiteLLM/httpx 超时行为。
"""
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.llm import gateway as gateway_module
from core.llm.context import RequestBudget
from core.llm.gateway import LLMGateway
from core.pipeline.outcome import iter_exception_chain
from core.utils.llm_errors import InvalidModelJSON, is_timeout_error

TIMEOUT = 1
GAP = 0.25
PIECES = ['{"evidence"', ': ', '[]', '}'] + [" "] * 4  # 8 段，总耗时约 2 秒，超过 TIMEOUT


def _chunk(model: str, delta: dict, finish=None, usage=None) -> bytes:
    body = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": model,
            "choices": [] if usage else [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n".encode()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(body)
        model = body["model"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for index, piece in enumerate(PIECES):
            if model == "stall" and index == 2:
                time.sleep(TIMEOUT * 2)
            delta = {"role": "assistant", "content": piece} if index == 0 else {"content": piece}
            self.wfile.write(_chunk(model, delta))
            self.wfile.flush()
            time.sleep(GAP)
        self.wfile.write(_chunk(model, {}, finish="stop"))
        self.wfile.write(_chunk(model, {}, usage={"prompt_tokens": 7, "completion_tokens": 8, "total_tokens": 15}))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


@pytest.fixture
def endpoint():
    handler = type("Handler", (_Handler,), {"requests": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", handler.requests
    finally:
        server.shutdown()
        server.server_close()


def _gateway(base_url: str, model: str) -> LLMGateway:
    return LLMGateway(model=f"openai/{model}", base_url=base_url, api_key="test", timeout=TIMEOUT,
                      provider_kwargs={}, budget=RequestBudget(32000, f"openai/{model}"))


MESSAGES = [{"role": "user", "content": "extract"}]


def test_long_output_outlives_timeout_while_data_keeps_arriving(endpoint):
    base_url, requests = endpoint
    started = time.monotonic()
    reply = _gateway(base_url, "slow").complete("test", MESSAGES, max_tokens=64)
    assert time.monotonic() - started > TIMEOUT
    assert reply.json() == {"evidence": []}
    assert reply.finish_reason == "stop"
    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}


def test_async_long_output_outlives_timeout(endpoint):
    base_url, _ = endpoint
    reply = asyncio.run(_gateway(base_url, "slow").acomplete("test", MESSAGES, max_tokens=64))
    assert reply.json() == {"evidence": []}


@pytest.mark.parametrize("use_async", [False, True])
def test_stalled_stream_still_times_out(endpoint, use_async):
    base_url, _ = endpoint
    gateway = _gateway(base_url, "stall")
    with pytest.raises(Exception) as caught:
        if use_async:
            asyncio.run(gateway.acomplete("test", MESSAGES, max_tokens=64))
        else:
            gateway.complete("test", MESSAGES, max_tokens=64)
    assert any(is_timeout_error(error) for error in iter_exception_chain(caught.value))


def test_usage_is_taken_from_stream(endpoint, monkeypatch):
    base_url, _ = endpoint
    recorded = []
    monkeypatch.setattr(gateway_module, "add_litellm_usage", lambda response: recorded.append(response.usage))
    _gateway(base_url, "slow").complete("test", MESSAGES, max_tokens=64)
    assert (recorded[0].prompt_tokens, recorded[0].completion_tokens) == (7, 8)


def test_empty_stream_is_invalid_output(monkeypatch):
    monkeypatch.setattr(gateway_module.litellm, "completion", lambda **kwargs: iter(()))
    with pytest.raises(InvalidModelJSON, match="empty stream"):
        _gateway("http://unused/v1", "m").complete("test", MESSAGES, max_tokens=64)
