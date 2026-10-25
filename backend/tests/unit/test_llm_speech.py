import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agent.schemas import Interpretation, ProductChoice
from app.integrations.llm import AzureLLM, CachedLLM, LLMError, ScriptedLLM
from app.integrations.speech import AzureSpeech, SpeechError


def _completion(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(content)},
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


async def test_azure_llm_returns_typed_output_and_usage() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion({"candidate_id": "A", "reason": "size"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = AzureLLM(
            endpoint="https://example.openai.azure.com",
            api_key="k",
            api_version="2024-10-21",
            deployment_small="small-dep",
            http_client=http,
        )
        result = await llm.structured(task="choose", system="s", user="u", schema=ProductChoice)

    assert result.value == ProductChoice(candidate_id="A", reason="size")
    assert (result.usage.tokens_in, result.usage.tokens_out) == (120, 30)
    assert "/deployments/small-dep/" in seen[0].url.path
    body = json.loads(seen[0].content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["temperature"] == 0


async def test_azure_llm_errors_become_llm_error() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(400, json={"error": {"message": "bad"}})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        llm = AzureLLM(
            endpoint="https://example.openai.azure.com",
            api_key="k",
            api_version="2024-10-21",
            deployment_small="d",
            http_client=http,
        )
        with pytest.raises(LLMError) as info:
            await llm.structured(task="choose", system="s", user="u", schema=ProductChoice)
    assert info.value.retryable is False


async def test_scripted_llm_rejects_invalid_output() -> None:
    llm = ScriptedLLM(lambda _task, _user: {"intent": "dance"})
    with pytest.raises(LLMError):
        await llm.structured(task="interpret", system="", user="x", schema=Interpretation)


async def test_cached_llm_calls_the_model_once(tmp_path: Path) -> None:
    inner = ScriptedLLM(lambda _t, _u: ProductChoice(candidate_id="A", reason="r"))
    cached = CachedLLM(inner, tmp_path, "model-x")
    first = await cached.structured(task="choose", system="s", user="u", schema=ProductChoice)
    second = await cached.structured(task="choose", system="s", user="u", schema=ProductChoice)
    assert first.value == second.value
    assert len(inner.calls) == 1 and second.usage.cached


def _speech_handler(results: dict[str, dict[str, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Content-Type"] == "audio/ogg; codecs=opus"
        return httpx.Response(200, json=results[request.url.params["language"]])

    return httpx.MockTransport(handler)


async def test_speech_keeps_the_most_confident_language() -> None:
    transport = _speech_handler(
        {
            "ur-PK": {
                "RecognitionStatus": "Success",
                "NBest": [{"Display": "پانچ کارٹن ٹپال", "Confidence": 0.91}],
            },
            "en-US": {
                "RecognitionStatus": "Success",
                "NBest": [{"Display": "punch carton tap all", "Confidence": 0.42}],
            },
        }
    )
    async with httpx.AsyncClient(transport=transport) as http:
        speech = AzureSpeech(http, key="k", region="eastus", languages=["ur-PK", "en-US"])
        transcript = await speech.transcribe(b"OggS", "audio/ogg; codecs=opus")
    assert transcript is not None
    assert (transcript.language, transcript.confidence) == ("ur-PK", 0.91)


async def test_speech_no_match_returns_none() -> None:
    transport = _speech_handler({"en-US": {"RecognitionStatus": "NoMatch"}})
    async with httpx.AsyncClient(transport=transport) as http:
        speech = AzureSpeech(http, key="k", region="eastus", languages=["en-US"])
        assert await speech.transcribe(b"OggS", "audio/ogg") is None


async def test_speech_rejects_unsupported_audio() -> None:
    async with httpx.AsyncClient() as http:
        speech = AzureSpeech(http, key="k", region="eastus", languages=["en-US"])
        with pytest.raises(SpeechError):
            await speech.transcribe(b"x", "video/mp4")
