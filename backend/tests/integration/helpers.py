"""Shared fakes and builders for database tests."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import build_graph
from app.agent.state import AgentSettings
from app.integrations.speech import Transcript
from app.integrations.whatsapp import WhatsAppError
from app.models.enums import Channel, MessageType
from app.schemas.inbound import InboundMessage
from app.services.inbound import save_inbound
from app.services.processing import CatalogCache, Runtime
from app.services.seeding import seed

Factory = async_sessionmaker[AsyncSession]
SEED_DIR = Path(__file__).resolve().parents[2] / "seed"
TRUSTED_PHONE = "923000000106"  # City Cash & Carry: tier B, high credit limit, not new
NEW_PHONE = "923000000108"  # New Star Kiryana: is_new, so first orders go to review


@dataclass
class FakeWhatsApp:
    """Records what would have been sent. `fail_with` makes every send raise."""

    read: list[str] = field(default_factory=list)
    sent: list[dict[str, Any]] = field(default_factory=list)
    fail_with: WhatsAppError | None = None

    def _record(self, **item: Any) -> str:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(item)
        return f"wamid.OUT{len(self.sent)}"

    async def send_text(self, to: str, text: str) -> str:
        return self._record(kind="text", to=to, text=text)

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> str:
        return self._record(kind="buttons", to=to, text=body, buttons=buttons)

    async def upload_media(self, content: bytes, mime_type: str, filename: str) -> str:
        assert content.startswith(b"%PDF")
        return "MEDIA-UP-1"

    async def send_document(self, to: str, media_id: str, filename: str, caption: str) -> str:
        return self._record(kind="document", to=to, filename=filename, text=caption)

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        return b"OggS-fake", "audio/ogg; codecs=opus"

    async def mark_as_read(self, message_id: str) -> None:
        self.read.append(message_id)


class FakeSpeech:
    def __init__(self, text: str) -> None:
        self.text = text

    async def transcribe(self, audio: bytes, mime_type: str) -> Transcript | None:
        return Transcript(self.text, "ur-PK", 0.9)


def make_runtime(tmp_path: Path, speech: FakeSpeech | None = None) -> Runtime:
    return Runtime(
        graph=build_graph(InMemorySaver()),
        catalog=CatalogCache(),
        llm=None,
        speech=speech,
        whatsapp=FakeWhatsApp(),  # type: ignore[arg-type]
        storage_dir=tmp_path,
        settings=AgentSettings(),
        reply_delay_s=0,
    )


def fake_whatsapp(runtime: Runtime) -> FakeWhatsApp:
    assert isinstance(runtime.whatsapp, FakeWhatsApp)
    return runtime.whatsapp


async def seeded_business(factory: Factory) -> int:
    async with factory() as session:
        result = await seed(
            session,
            SEED_DIR,
            wa_phone_number_id="PN-W",
            admin_email="a@test.test",
            admin_password_hash=None,
        )
    return result.business_id


_counter = {"n": 0}


async def save_message(
    factory: Factory,
    business_id: int,
    *,
    body: str | None = None,
    audio: str | None = None,
    button_id: str | None = None,
    sender: str = TRUSTED_PHONE,
    external_id: str | None = None,
) -> tuple[int, int]:
    _counter["n"] += 1
    if button_id is not None:
        kind = MessageType.INTERACTIVE
        raw: dict[str, Any] | None = {
            "interactive": {"type": "button_reply", "button_reply": {"id": button_id, "title": "x"}}
        }
    else:
        kind, raw = (MessageType.AUDIO if audio else MessageType.TEXT), None
    async with factory() as session:
        saved = await save_inbound(
            session,
            business_id,
            InboundMessage(
                channel=Channel.WHATSAPP,
                external_id=external_id or f"wamid.IN{_counter['n']}",
                sender=sender,
                sender_name="Shop",
                type=kind,
                body=body,
                media_ref=audio,
                raw=raw,
            ),
        )
        await session.commit()
    assert saved is not None
    return saved.message_id, saved.job_id


def worker_ctx(factory: Factory, runtime: Runtime, job_try: int = 1) -> dict[str, Any]:
    return {
        "session_factory": factory,
        "runtime": runtime,
        "whatsapp": runtime.whatsapp,
        "mark_as_read": True,
        "job_try": job_try,
    }
