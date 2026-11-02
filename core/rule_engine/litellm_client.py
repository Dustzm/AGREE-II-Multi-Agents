"""
LiteLLM client adapter for rule-engine LLM calls.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.llm.gateway import LLMGateway
from core.llm.recovery import RecoveryBudget
from core.llm.structured_output import (
    JSON_OBJECT,
    build_structured_format,
    is_unsupported_structured_error,
    next_mode,
    remember_mode,
    resolve_mode,
)
from core.utils.llm_errors import InvalidModelJSON, is_transport_error, tag_stage

logger = logging.getLogger(__name__)


class LiteLLMRuleClient:
    """JSON-only adapter used by rule-engine stages; requests go through the task gateway."""

    def __init__(
        self,
        gateway: LLMGateway | None = None,
        *,
        recovery: RecoveryBudget | None = None,
        cancellation_check=None,
    ):
        self.gateway = gateway or LLMGateway.from_settings()
        self.recovery = recovery or RecoveryBudget()
        self.cancellation_check = cancellation_check or (lambda: None)
        self._structured_mode: str | None = None

    @property
    def budget(self):
        return self.gateway.budget

    def complete_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "rule_engine_response",
        strict: bool = True,
        list_wrapper: str | None = None,
    ) -> dict[str, Any]:
        """Schema repairs belong to the calling stage; this client replays transport failures only."""
        self.cancellation_check()
        max_tokens = self.gateway.output_limit(max_tokens)
        if json_schema is None or _is_deepseek_family_model(self.gateway.model):
            return self._complete_once(
                stage=stage,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                mode="json_object",
                json_schema=None,
                schema_name=schema_name,
                strict=strict,
                list_wrapper=list_wrapper,
            )
        mode = self._mode()
        while True:
            try:
                return self._complete_once(
                    stage=stage,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                    mode=mode,
                    json_schema=json_schema,
                    schema_name=schema_name,
                    strict=strict,
                    list_wrapper=list_wrapper,
                )
            except Exception as error:
                fallback = next_mode(mode)
                if fallback is None or not is_unsupported_structured_error(error):
                    raise
                logger.warning(
                    "部署不支持结构化输出模式，自动降级: stage=%s mode=%s -> %s error=%s",
                    stage, mode, fallback, error,
                )
                mode = fallback
                self._structured_mode = mode
                remember_mode(self.gateway.base_url, self.gateway.model, mode)

    def _mode(self) -> str:
        if self._structured_mode is None:
            self._structured_mode = resolve_mode(self.gateway.base_url, self.gateway.model)
        return self._structured_mode

    def _format(
        self,
        mode: str,
        json_schema: dict[str, Any] | None,
        schema_name: str,
        strict: bool,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if json_schema is None:
            return JSON_OBJECT, None
        return build_structured_format(mode, json_schema, schema_name=schema_name, strict=strict)

    def _complete_once(
        self,
        *,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        mode: str,
        json_schema: dict[str, Any] | None,
        schema_name: str,
        strict: bool,
        list_wrapper: str | None = None,
    ) -> dict[str, Any]:
        response_format, extra = self._format(mode, json_schema, schema_name, strict)
        try:
            result = self._request(
                stage, system_prompt, user_prompt, max_tokens, response_format, extra,
                list_wrapper=list_wrapper,
            )
            return self._complete_missing_decisions(
                result, json_schema, stage, system_prompt, user_prompt, max_tokens, mode, schema_name, strict,
                list_wrapper=list_wrapper,
            )
        except Exception as error:
            tag_stage(error, stage)
            raise

    def _request(
        self,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        response_format: dict[str, Any] | None,
        extra: dict[str, Any] | None = None,
        *,
        list_wrapper: str | None = None,
    ) -> dict[str, Any]:
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

        def call() -> dict[str, Any]:
            completed = self.gateway.complete(
                stage, messages, max_tokens=max_tokens, response_format=response_format, extra=extra,
            )
            payload = completed.json() if list_wrapper is None else completed.json(list_wrapper=list_wrapper)
            return _wrap_list_payload(payload, list_wrapper)

        return self.recovery.run(
            call,
            check_cancelled=self.cancellation_check,
            retryable=is_transport_error,
        )

    def _complete_missing_decisions(
        self,
        result: dict[str, Any],
        schema: dict[str, Any] | None,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        mode: str,
        schema_name: str,
        strict: bool,
        list_wrapper: str | None = None,
    ) -> dict[str, Any]:
        """Ask only for missing decision indexes; existing decisions are kept as returned."""
        decision_schema = (schema or {}).get("properties", {}).get("decisions", {})
        count = decision_schema.get("minItems")
        if not isinstance(count, int) or not isinstance(result.get("decisions"), list):
            return result
        expected = set(range(count))
        collected: dict[int, dict[str, Any]] = {}
        pending = result["decisions"]
        while True:
            for decision in pending:
                index = decision.get("index") if isinstance(decision, dict) else None
                if type(index) is not int or index not in expected or index in collected:
                    raise InvalidModelJSON("Invalid, duplicate or unknown decision index")
                collected[index] = decision
            missing = sorted(expected - collected.keys())
            if not missing:
                return {**result, "decisions": [collected[index] for index in range(count)]}
            self.recovery.note_repair(stage)
            repair_schema = json.loads(json.dumps(schema))
            repair_decisions = repair_schema["properties"]["decisions"]
            repair_decisions["minItems"] = len(missing)
            repair_decisions["maxItems"] = len(missing)
            repair_decisions["items"]["properties"]["index"] = {"type": "integer", "enum": missing}
            repair_format, repair_extra = self._format(mode, repair_schema, schema_name, strict)
            repaired = self._request(
                stage,
                system_prompt,
                user_prompt + "\nReturn ONLY missing decision indexes: " + json.dumps(missing),
                max_tokens,
                repair_format,
                repair_extra,
                list_wrapper=list_wrapper,
            )
            pending = repaired.get("decisions")
            if not isinstance(pending, list):
                raise InvalidModelJSON("Decision repair must return a decisions array")


def _wrap_list_payload(payload: dict[str, Any], list_wrapper: str | None) -> dict[str, Any]:
    """Fold a single bare decision object into the declared wrapper array."""
    if not list_wrapper or list_wrapper in payload:
        return payload
    if list_wrapper == "decisions" and type(payload.get("index")) is int:
        return {list_wrapper: [payload]}
    return payload


def _is_deepseek_family_model(model_name: str) -> bool:
    normalized = model_name.split("/", 1)[-1].lower()
    return normalized.startswith("deepseek-")
