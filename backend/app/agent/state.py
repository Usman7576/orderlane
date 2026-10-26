"""Agent state and the objects every step can use.

The state is what LangGraph checkpoints (saved in PostgreSQL while an order waits for
staff review), so it holds only plain JSON values. Database sessions, the LLM client
and the catalog are passed separately as the run's context (AgentDeps)."""

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypedDict

from app.agent.tools import OrderTools
from app.integrations.llm import LLM, Usage
from app.matching.matcher import Matcher
from app.matching.rules_pipeline import RulesPipeline
from app.ordering.draft import Draft, DraftLine


class AgentState(TypedDict, total=False):
    business_id: int
    customer_id: int
    message_id: int | None
    text: str
    # interpret
    intent: str
    confidence: str
    question_topic: str | None
    instructions: list[dict[str, Any]]
    degraded: bool  # the LLM failed and rules were used instead
    # resolve
    resolved: list[dict[str, Any]]
    # draft and checks
    draft: list[dict[str, Any]]
    total: str
    issues: list[str]
    route: str  # ask_customer | staff_review | answer | handoff | cancelled
    order_id: int | None
    order_number: int | None
    review: dict[str, Any] | None  # staff decision after a review pause
    reply: str | None
    chat_kind: str  # for non-order messages: greeting, thanks, ack, complaint, other


@dataclass(frozen=True)
class AgentSettings:
    review_above_total: Decimal = Decimal(100_000)
    choose_candidates: int = 5


@dataclass
class AgentDeps:
    tools: OrderTools
    matcher: Matcher
    llm: LLM | None  # None: rules only
    settings: AgentSettings = field(default_factory=AgentSettings)
    trace: list[dict[str, Any]] = field(default_factory=list)
    usage: list[Usage] = field(default_factory=list)

    @property
    def rules(self) -> RulesPipeline:
        return RulesPipeline(self.matcher)

    def record(self, node: str, started: float, output: dict[str, Any]) -> None:
        now = time.time()
        self.trace.append(
            {
                "node": node,
                "started_at": datetime.fromtimestamp(started, UTC),
                "ended_at": datetime.fromtimestamp(now, UTC),
                "output": _summarize(output),
            }
        )


def _summarize(output: dict[str, Any]) -> dict[str, Any]:
    """Keep traces small: long lists are cut, everything becomes JSON-friendly."""

    def clean(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, list):
            return [clean(v) for v in value[:20]]
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        return value

    return {k: clean(v) for k, v in output.items()}


def draft_to_state(draft: Draft) -> list[dict[str, Any]]:
    return [
        {
            "key": line.key,
            "qty": None if line.qty is None else str(line.qty),
            "unit": line.unit,
            "source_text": line.source_text,
            "needs_review": line.needs_review,
            "issues": list(line.issues),
            "candidates": list(line.candidates),
            "method": line.method,
            "confidence": line.confidence,
            "unit_price": None if line.unit_price is None else str(line.unit_price),
            "line_total": None if line.line_total is None else str(line.line_total),
        }
        for line in draft.lines
    ]


def draft_from_state(lines: list[dict[str, Any]]) -> Draft:
    def dec(value: Any) -> Decimal | None:
        return None if value is None else Decimal(str(value))

    return Draft(
        tuple(
            DraftLine(
                key=line["key"],
                qty=dec(line["qty"]),
                unit=line["unit"],
                source_text=line.get("source_text", ""),
                needs_review=line.get("needs_review", False),
                issues=tuple(line.get("issues", ())),
                candidates=tuple(line.get("candidates", ())),
                method=line.get("method"),
                confidence=line.get("confidence"),
                unit_price=dec(line.get("unit_price")),
                line_total=dec(line.get("line_total")),
            )
            for line in lines
        )
    )
