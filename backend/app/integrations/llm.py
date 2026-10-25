"""LLM access behind one small interface, so the provider can change with a setting.

Every call returns a typed Pydantic object (structured output), plus token usage and
latency for cost tracking. Implementations:
- AzureLLM: Azure OpenAI chat completions with JSON-schema structured output.
- ScriptedLLM: returns prepared answers; used in tests.
- CachedLLM: wraps another LLM and stores answers on disk, so re-running the
  evaluation costs nothing.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncAzureOpenAI
from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class Usage:
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cached: bool = False


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    usage: Usage


class LLMError(Exception):
    """The LLM failed or returned something unusable. The agent falls back to rules."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLM(Protocol):
    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], large: bool = False
    ) -> LLMResult[T]: ...


class AzureLLM:
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        api_version: str,
        deployment_small: str,
        deployment_large: str = "",
        timeout_s: float = 30.0,
        # An httpx-compatible async client; tests pass one with a mock transport.
        http_client: Any = None,
    ) -> None:
        self._client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout_s,
            max_retries=2,  # the SDK retries 429s and 5xx with backoff
            http_client=http_client,
        )
        self._small = deployment_small
        self._large = deployment_large or deployment_small

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], large: bool = False
    ) -> LLMResult[T]:
        model = self._large if large else self._small
        started = time.perf_counter()
        try:
            completion = await self._client.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=schema,
                temperature=0,
            )
        except (APIConnectionError, APITimeoutError) as exc:
            raise LLMError(f"{task}: {exc}", retryable=True) from exc
        except APIStatusError as exc:
            raise LLMError(
                f"{task}: HTTP {exc.status_code}", retryable=exc.status_code >= 500
            ) from exc

        message = completion.choices[0].message
        if message.parsed is None:
            raise LLMError(f"{task}: no structured output ({message.refusal or 'empty'})")
        usage = completion.usage
        return LLMResult(
            message.parsed,
            Usage(
                model=model,
                tokens_in=usage.prompt_tokens if usage else 0,
                tokens_out=usage.completion_tokens if usage else 0,
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
        )


Handler = Callable[[str, str], BaseModel | dict[str, Any] | Exception]


class ScriptedLLM:
    """Test double: a handler receives (task, user prompt) and returns the answer."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str]] = []

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], large: bool = False
    ) -> LLMResult[T]:
        self.calls.append((task, user))
        answer = self._handler(task, user)
        if isinstance(answer, Exception):
            raise answer
        try:
            value = schema.model_validate(
                answer.model_dump() if isinstance(answer, BaseModel) else answer
            )
        except ValidationError as exc:
            raise LLMError(f"{task}: invalid output: {exc}") from exc
        return LLMResult(value, Usage(model="scripted", tokens_in=len(user) // 4, tokens_out=50))


class CachedLLM:
    """Stores answers as JSON files keyed by a hash of everything that affects the answer."""

    def __init__(self, inner: LLM, cache_dir: Path, model_label: str) -> None:
        self._inner = inner
        self._dir = cache_dir
        self._label = model_label
        self._dir.mkdir(parents=True, exist_ok=True)

    def _key(self, task: str, system: str, user: str, schema: type[BaseModel], large: bool) -> str:
        raw = json.dumps([self._label, task, system, user, schema.__name__, large])
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def structured[T: BaseModel](
        self, *, task: str, system: str, user: str, schema: type[T], large: bool = False
    ) -> LLMResult[T]:
        path = self._dir / f"{self._key(task, system, user, schema, large)}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            usage = Usage(**{**data["usage"], "cached": True})
            return LLMResult(schema.model_validate(data["value"]), usage)
        result = await self._inner.structured(
            task=task, system=system, user=user, schema=schema, large=large
        )
        payload = {"value": result.value.model_dump(mode="json"), "usage": result.usage.__dict__}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return result
