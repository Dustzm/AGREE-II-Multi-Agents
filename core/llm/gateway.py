"""任务内唯一的模型请求入口：参数拼装、预算检查、并发闸门、用量统计与输出提取。

每次调用只发送一个请求；重试由调用方通过 RecoveryBudget 决定。

请求一律以流式发送、读完后还原为完整响应：超时只约束相邻两段数据的间隔，长输出只要模型持续产出
就不会因整体耗时被打断，真正卡住的连接仍按 timeout 及时失败。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import litellm

from core.agents.base.model_config import (
    get_compatible_reasoning_kwargs,
    get_compatible_sampling_kwargs,
    get_provider_config_for,
    get_provider_kwargs_for,
)
from core.llm.context import RequestBudget, current_llm_settings
from core.llm.json_output import check_finish_reason, parse_json_object
from core.utils.cancellation import raise_if_active_cancelled
from core.utils.llm_errors import InvalidModelJSON
from core.utils.llm_limiter import llm_async_slot, llm_sync_slot
from core.utils.token_tracker import add_litellm_usage
from core.utils.unicode_sanitizer import sanitize_unicode, strip_surrogates

logger = logging.getLogger(__name__)

JSON_OBJECT = {"type": "json_object"}
_PREVIEW_LIMIT = 1200
_THINK = re.compile(r"<think>.*?</think>", flags=re.DOTALL)

# LiteLLM 默认的 aiohttp 异步传输把读超时同时设为总超时，会让异步流式请求退回按整体耗时超时；
# 改用 httpx 传输，使同步与异步请求的超时语义一致（均为两次读取之间的间隔）。
litellm.disable_aiohttp_transport = True


def preview_text(text: str, limit: int = _PREVIEW_LIMIT) -> str:
    """日志预览，避免超长内容压垮日志与 I/O。"""
    text = strip_surrogates(text or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [截断，原始长度 {len(text)} 字符]"


def _message_text(value) -> str:
    """兼容字符串与分段 content。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item.strip())
            elif isinstance(item, dict):
                parts.append(next((item[key].strip() for key in ("text", "content", "value")
                                   if isinstance(item.get(key), str)), ""))
        return "\n".join(part for part in parts if part).strip()
    return ""


_RESERVED_REQUEST_KEYS = frozenset({"model", "api_key", "base_url", "messages", "timeout", "stream"})


def _merge_request_extra(request: dict[str, Any], extra: dict[str, Any] | None) -> None:
    """合并调用方补充参数；extra_body 做浅合并，避免覆盖 provider 默认值。"""
    for key, value in (extra or {}).items():
        if key in _RESERVED_REQUEST_KEYS:
            logger.warning("忽略 extra 中的保留请求字段: %s", key)
            continue
        if key == "extra_body" and isinstance(request.get("extra_body"), dict) and isinstance(value, dict):
            merged = dict(request["extra_body"])
            merged.update(value)
            request["extra_body"] = merged
        else:
            request[key] = value


def _assemble(chunks: list, messages: list):
    response = litellm.stream_chunk_builder(chunks, messages=messages)
    if response is None:
        raise InvalidModelJSON("Model returned an empty stream")
    return response


def _collect(stream, messages: list):
    """读完流式响应并还原为完整响应；已是完整响应（如测试替身）时原样返回。"""
    if hasattr(stream, "choices"):
        return stream
    return _assemble(list(stream), messages)


async def _acollect(stream, messages: list):
    if hasattr(stream, "choices"):
        return stream
    return _assemble([chunk async for chunk in stream], messages)


@dataclass(frozen=True)
class LLMReply:
    """一次模型响应的正文、推理内容和结束原因。"""

    text: str
    reasoning: str | None
    finish_reason: str | None

    @classmethod
    def from_response(cls, response) -> "LLMReply":
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise InvalidModelJSON("Model returned no choices")
        choice = choices[0]
        message = getattr(choice, "message", None)
        text = strip_surrogates(_THINK.sub("", _message_text(getattr(message, "content", None))).strip())
        # vLLM reasoning-parser 使用 reasoning，旧版本使用 reasoning_content。
        reasoning = _message_text(getattr(message, "reasoning", None) or getattr(message, "reasoning_content", None))
        return cls(text, strip_surrogates(reasoning) or None, getattr(choice, "finish_reason", None))

    def json(self, *, list_wrapper: str | None = None) -> dict:
        return parse_json_object(self.text, self.finish_reason, list_wrapper=list_wrapper)

    def checked_text(self) -> str:
        """非 JSON 调用也必须拒绝截断或未完成的输出。"""
        check_finish_reason(self.finish_reason)
        return self.text or (self.reasoning or "")


class LLMGateway:
    """绑定一次任务的模型配置；所有 LiteLLM 调用参数在此处统一拼装。"""

    def __init__(self, *, model: str, base_url: str, api_key: str, timeout: int,
                 provider_kwargs: dict[str, Any], budget: RequestBudget):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.provider_kwargs = provider_kwargs
        self.budget = budget

    @classmethod
    def from_settings(cls, llm=None) -> "LLMGateway":
        llm = llm or current_llm_settings()
        provider = get_provider_config_for(llm.provider)
        model = f"{provider['prefix']}/{llm.model_name}"
        return cls(
            model=model,
            # 优先使用用户配置的 base_url，其次使用 providers.json 中的 default_base_url
            base_url=llm.base_url or provider.get("default_base_url", ""),
            api_key=llm.api_key or "dummy-key",
            timeout=llm.timeout,
            provider_kwargs=get_provider_kwargs_for(llm.provider),
            budget=RequestBudget.from_settings(llm, model),
        )

    def output_limit(self, requested: int) -> int:
        return self.budget.output_limit(requested)

    def _request(self, stage: str, messages: list, max_tokens: int, response_format,
                 extra: dict[str, Any] | None = None) -> dict[str, Any]:
        self.budget.check(
            messages, max_tokens,
            extra=[response_format, extra] if extra else response_format,
            stage=stage,
        )
        user = next((m.get("content") for m in reversed(messages) if m.get("role") == "user"), "")
        logger.info("=== [%s] LLM Input Preview ===\n%s", stage, preview_text(user if isinstance(user, str) else str(user)))
        request = dict(
            model=self.model,
            messages=sanitize_unicode(messages),
            base_url=self.base_url,
            api_key=self.api_key,
            max_tokens=max_tokens,
            timeout=self.timeout,
            stream=True,
            stream_options={"include_usage": True},
            num_retries=0,
            **get_compatible_reasoning_kwargs(self.model),
            **get_compatible_sampling_kwargs(self.model, temperature=0, top_p=0.1, seed=42),
            **self.provider_kwargs,
        )
        if response_format is not None:
            request["response_format"] = response_format
        _merge_request_extra(request, extra)
        return request

    def _reply(self, stage: str, response) -> LLMReply:
        add_litellm_usage(response)
        reply = LLMReply.from_response(response)
        usage = getattr(response, "usage", None)
        logger.info(
            "=== [%s] LLM Output === model=%s tokens=%s/%s output_chars=%s reasoning_chars=%s finish=%s\n%s",
            stage, getattr(response, "model", self.model),
            getattr(usage, "prompt_tokens", "N/A"), getattr(usage, "completion_tokens", "N/A"),
            len(reply.text), len(reply.reasoning or ""), reply.finish_reason, preview_text(reply.text),
        )
        if reply.reasoning:
            logger.info("=== [%s] Reasoning Preview ===\n%s", stage, preview_text(reply.reasoning))
        return reply

    def complete(self, stage: str, messages: list, *, max_tokens: int, response_format=JSON_OBJECT,
                 extra: dict[str, Any] | None = None) -> LLMReply:
        request = self._request(stage, messages, max_tokens, response_format, extra)
        with llm_sync_slot(stage):
            # 排队拿到并发槽后再检查：已超时/取消的条目不再发新请求
            raise_if_active_cancelled()
            response = _collect(litellm.completion(**request), request["messages"])
        return self._reply(stage, response)

    async def acomplete(self, stage: str, messages: list, *, max_tokens: int, response_format=JSON_OBJECT,
                        extra: dict[str, Any] | None = None) -> LLMReply:
        request = self._request(stage, messages, max_tokens, response_format, extra)
        async with llm_async_slot(stage):
            raise_if_active_cancelled()
            response = await _acollect(await litellm.acompletion(**request), request["messages"])
        return self._reply(stage, response)
