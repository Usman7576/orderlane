"""Azure AI Speech, short-audio REST API (works on the free F0 tier, up to 60 seconds).

WhatsApp voice notes are OGG/Opus, which this API accepts directly. Each language in
SPEECH_LANGUAGES is tried and the most confident transcript is kept, because shops mix
Urdu and English. Note: Urdu (ur-PK) comes back in Urdu script; the LLM step reads it fine,
but the rules fallback only understands Latin script."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

MAX_SECONDS = 60


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    confidence: float


class SpeechError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, mime_type: str) -> Transcript | None: ...


def _content_type(mime_type: str) -> str:
    if "ogg" in mime_type or "opus" in mime_type:
        return "audio/ogg; codecs=opus"
    if "wav" in mime_type:
        return "audio/wav; codecs=audio/pcm; samplerate=16000"
    raise SpeechError(f"unsupported audio type {mime_type}")


class AzureSpeech:
    def __init__(self, http: httpx.AsyncClient, *, key: str, region: str, languages: list[str]):
        self._http = http
        self._key = key
        self._url = (
            f"https://{region}.stt.speech.microsoft.com"
            "/speech/recognition/conversation/cognitiveservices/v1"
        )
        self._languages = languages

    async def _recognize(self, audio: bytes, content_type: str, language: str) -> Transcript | None:
        try:
            response = await self._http.post(
                self._url,
                params={"language": language, "format": "detailed"},
                headers={"Ocp-Apim-Subscription-Key": self._key, "Content-Type": content_type},
                content=audio,
            )
        except httpx.TransportError as exc:
            raise SpeechError(f"network error: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise SpeechError(
                f"Azure Speech {response.status_code}: {response.text[:200]}",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        data: dict[str, Any] = response.json()
        if data.get("RecognitionStatus") != "Success":
            return None
        best = (data.get("NBest") or [{}])[0]
        text = best.get("Display") or data.get("DisplayText") or ""
        return Transcript(text, language, float(best.get("Confidence", 0.0))) if text else None

    async def transcribe(self, audio: bytes, mime_type: str) -> Transcript | None:
        content_type = _content_type(mime_type)
        results = [await self._recognize(audio, content_type, lang) for lang in self._languages]
        found = [r for r in results if r is not None]
        return max(found, key=lambda r: r.confidence) if found else None
