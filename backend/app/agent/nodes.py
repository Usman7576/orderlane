"""The agent's steps. Each takes the state and returns only the fields it changes."""

import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from decimal import Decimal
from functools import wraps
from typing import Any

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from app.agent import replies
from app.agent.prompts import (
    CHOOSE_SYSTEM,
    INTERPRET_SYSTEM,
    choose_user_prompt,
    interpret_user_prompt,
)
from app.agent.routing import (
    ANSWER,
    ASK_CUSTOMER,
    CANCELLED,
    HANDOFF,
    STAFF_REVIEW,
    decide_route,
)
from app.agent.schemas import Interpretation, ProductChoice
from app.agent.state import AgentDeps, AgentState, draft_from_state, draft_to_state
from app.integrations.llm import LLMError
from app.matching.lexicon import REMOVE_WORDS
from app.matching.matcher import Decision, MatchResult
from app.matching.normalize import clean
from app.matching.parse import ParsedLine, parse_lines, parse_quantity
from app.matching.rules_pipeline import Intent
from app.models.enums import OrderStatus
from app.ordering.draft import (
    AddLine,
    CancelOrder,
    Draft,
    DraftLine,
    Operation,
    RemoveLine,
    SetLine,
    apply_operations,
)

Node = Callable[[AgentState, Runtime[AgentDeps]], Awaitable[dict[str, Any]]]


def traced(name: str) -> Callable[[Node], Node]:
    """Record each step's timing and output in the run's trace (shown in the dashboard)."""

    def decorate(fn: Node) -> Node:
        @wraps(fn)
        async def wrapper(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
            started = time.time()
            output = await fn(state, runtime)
            runtime.context.record(name, started, output)
            return output

        return wrapper

    return decorate


def _draft_labels(draft: Draft, deps: AgentDeps) -> list[str]:
    index = deps.matcher.index
    labels = []
    for line in draft.lines:
        name = index.by_key[line.key].name if line.key in index.by_key else line.source_text
        labels.append(f"{line.qty} {line.unit} {name}")
    return labels


def _rules_interpretation(text: str, draft: Draft, deps: AgentDeps) -> dict[str, Any]:
    """Rules-only reading of the message, in the same shape the LLM returns."""
    lines = parse_lines(text)
    matches = [deps.matcher.match(line, draft.keys) for line in lines]
    matches = [m for m in matches if m.decision is not Decision.NONE or m.line.qty is not None]
    intent = deps.rules.classify(text, lines, matches, bool(draft.lines))
    removing = any(f" {w} " in f" {clean(text)} " for w in REMOVE_WORDS)
    operation = {
        Intent.MODIFY: "remove" if removing else "set",
    }.get(intent, "add")
    instructions = [
        {
            "operation": operation,
            "product_mention": m.line.product_text,
            "quantity": None if m.line.qty is None else str(m.line.qty),
            "unit": m.line.unit,
        }
        for m in matches
    ]
    if intent is Intent.MODIFY and not instructions:
        qty, unit = parse_quantity(text)
        if qty is not None:
            instructions.append(
                {"operation": "set", "product_mention": "", "quantity": str(qty), "unit": unit}
            )
    topic = None
    if intent is Intent.QUESTION:
        cleaned = clean(text)
        topic = (
            "balance"
            if any(w in cleaned for w in ("balance", "baqi", "baki", "udhar", "owe"))
            else "stock"
            if any(w in cleaned for w in ("stock", "available", "milega", "mil jayega"))
            else "price"
        )
    return {
        "intent": intent.value,
        "confidence": "medium",
        "question_topic": topic,
        "instructions": instructions,
    }


@traced("interpret")
async def interpret(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    deps = runtime.context
    draft = await deps.tools.load_draft(state["customer_id"])
    base = {"draft": draft_to_state(draft), "degraded": False}
    if deps.llm is None:
        return {**base, **_rules_interpretation(state["text"], draft, deps)}
    try:
        result = await deps.llm.structured(
            task="interpret",
            system=INTERPRET_SYSTEM,
            user=interpret_user_prompt(state["text"], _draft_labels(draft, deps)),
            schema=Interpretation,
        )
    except LLMError:
        return {**base, **_rules_interpretation(state["text"], draft, deps), "degraded": True}
    deps.usage.append(result.usage)
    value = result.value
    return {
        **base,
        "intent": value.intent,
        "confidence": value.confidence,
        "question_topic": value.question_topic,
        "instructions": [
            {
                "operation": line.operation,
                "product_mention": line.product_mention,
                "quantity": None if line.quantity is None else str(line.quantity),
                "unit": line.unit,
            }
            for line in value.lines
        ],
    }


def _parsed_mention(instruction: dict[str, Any]) -> ParsedLine:
    """Turn an instruction into one ParsedLine for the matcher."""
    mention = instruction["product_mention"] or ""
    parts = parse_lines(mention)
    line = ParsedLine(raw=mention, words=[], product_text="")
    for part in parts:
        line.words += part.words
        line.sizes += part.sizes
        line.numbers += part.numbers
        line.qty = line.qty or part.qty
        line.unit = line.unit or part.unit
        line.product_text = f"{line.product_text} {part.product_text}".strip()
    if instruction.get("quantity") is not None:
        line.qty = Decimal(str(instruction["quantity"]))
    if instruction.get("unit"):
        line.unit = instruction["unit"]
    return line


async def _choose(
    deps: AgentDeps, match: MatchResult, message: str
) -> tuple[MatchResult, str | None]:
    """Ask the LLM to pick among close candidates. Its answer must be one of them."""
    if deps.llm is None or len(match.candidates) < 2:
        return match, None
    index = deps.matcher.index
    candidates = match.candidates[: deps.settings.choose_candidates]
    try:
        result = await deps.llm.structured(
            task="choose",
            system=CHOOSE_SYSTEM,
            user=choose_user_prompt(
                match.line.raw, message, [(c.key, index.by_key[c.key].name) for c in candidates]
            ),
            schema=ProductChoice,
        )
    except LLMError:
        return match, None
    deps.usage.append(result.usage)
    chosen = result.value.candidate_id
    if chosen is None or chosen not in {c.key for c in candidates}:
        return match, None  # null, or an id we never offered: keep it ambiguous
    ordered = sorted(candidates, key=lambda c: c.key != chosen)
    match.candidates = ordered
    match.decision = Decision.ACCEPTED
    match.issues = [i for i in match.issues if i != "ambiguous_product"]
    return match, chosen


@traced("resolve_products")
async def resolve_products(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    deps = runtime.context
    prefer = draft_from_state(state.get("draft", [])).keys
    resolved = []
    for instruction in state.get("instructions", []):
        if not instruction["product_mention"]:
            resolved.append(
                {
                    **instruction,
                    "key": None,
                    "decision": "none",
                    "issues": [],
                    "candidates": [],
                    "method": None,
                    "confidence": None,
                }
            )
            continue
        match = deps.matcher.match(_parsed_mention(instruction), prefer)
        chosen = None
        if match.decision is Decision.AMBIGUOUS:
            match, chosen = await _choose(deps, match, state["text"])
        best = match.best
        resolved.append(
            {
                **instruction,
                "key": best.key if best else None,
                "quantity": None if match.qty is None else str(match.qty),
                "unit": match.unit,
                "decision": match.decision.value,
                "issues": match.issues,
                "candidates": [c.key for c in match.candidates],
                "method": "llm" if chosen else (best.method if best else None),
                "confidence": min(best.score, 1.0) if best else None,
                "source_text": match.line.raw,
            }
        )
    return {"resolved": resolved}


def _operations(intent: str, resolved: list[dict[str, Any]]) -> list[Operation]:
    if intent == "cancel":
        return [CancelOrder()]
    ops: list[Operation] = []
    for r in resolved:
        qty = None if r["quantity"] is None else Decimal(str(r["quantity"]))
        needs_review = r["decision"] != "accepted" or any(
            i in r["issues"] for i in ("unclear_quantity", "unit_not_sold", "unit_assumed")
        )
        if r["operation"] == "remove" and r["key"]:
            ops.append(RemoveLine(r["key"]))
        elif r["operation"] == "set":
            ops.append(SetLine(r["key"], qty, r["unit"], r.get("source_text", "")))
        elif r["operation"] == "add" and (r["key"] or qty is not None):
            ops.append(
                AddLine(
                    DraftLine(
                        key=r["key"],
                        qty=qty,
                        unit=r["unit"],
                        source_text=r.get("source_text", ""),
                        needs_review=needs_review,
                        issues=tuple(r["issues"]),
                        candidates=tuple(r["candidates"]),
                        method=r["method"],
                        confidence=r["confidence"],
                    )
                )
            )
    return ops


@traced("update_draft")
async def update_draft(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    draft = draft_from_state(state.get("draft", []))
    result = apply_operations(draft, _operations(state["intent"], state.get("resolved", [])))
    return {"draft": draft_to_state(result.draft), "issues": result.warnings}


@traced("check_business_rules")
async def check_business_rules(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    """Prices, stock, credit and order size. All numbers come from the database."""
    deps = runtime.context
    customer = await deps.tools.customer(state["customer_id"])
    draft = draft_from_state(state.get("draft", []))
    issues = [i for i in state.get("issues", []) if not i.startswith("set_unknown_line")]
    lines, total = [], Decimal(0)
    for line in draft.lines:
        line_issues = list(line.issues)
        unit_price = line_total = None
        if line.key and line.unit and line.qty is not None:
            unit_price = await deps.tools.price(line.key, line.unit, customer.price_tier)
            if unit_price is None:
                line_issues.append("no_price")
            else:
                line_total = (unit_price * line.qty).quantize(Decimal("1"))
                total += line_total
            per_unit = deps.matcher.index.by_key[line.key].units.get(line.unit, 0)
            available = await deps.tools.available_base(line.key)
            if available is not None and line.qty * per_unit > available:
                line_issues.append("out_of_stock")
        review = line.needs_review or bool({"no_price", "out_of_stock"} & set(line_issues))
        lines.append((line, unit_price, line_total, tuple(dict.fromkeys(line_issues)), review))
        issues += [i for i in line_issues if i in ("no_price", "out_of_stock")]

    if draft.lines:
        if customer.balance + total > customer.credit_limit:
            issues.append("over_credit_limit")
        if customer.is_new:
            issues.append("first_order")
        if total > deps.settings.review_above_total:
            issues.append("large_order")
    if state.get("confidence") == "low":
        issues.append("low_confidence")
    if state.get("degraded"):
        issues.append("llm_unavailable")

    priced = Draft(
        tuple(
            replace(line, unit_price=p, line_total=t, issues=i, needs_review=r)
            for line, p, t, i, r in lines
        )
    )
    issues = list(dict.fromkeys(issues))
    route = decide_route(state["intent"], priced, issues)
    return {"draft": draft_to_state(priced), "total": str(total), "issues": issues, "route": route}


_STATUS = {
    STAFF_REVIEW: OrderStatus.PENDING_REVIEW,
    ASK_CUSTOMER: OrderStatus.AWAITING_CUSTOMER,
    CANCELLED: OrderStatus.CANCELLED,
    HANDOFF: OrderStatus.DRAFT,
}


@traced("save_order")
async def save_order(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    deps = runtime.context
    saved = await deps.tools.save_draft(
        state["customer_id"],
        draft_from_state(state.get("draft", [])),
        status=_STATUS[state["route"]],
        total=Decimal(state.get("total", "0")),
        issues=state.get("issues", []),
        event={
            "message_id": state.get("message_id"),
            "intent": state["intent"],
            "route": state["route"],
            "issues": state.get("issues", []),
        },
    )
    return {"order_id": saved.order_id, "order_number": saved.number}


@traced("staff_review")
async def staff_review(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    """Pause until staff approve or reject in the dashboard. The state is saved in
    PostgreSQL meanwhile, so a restart loses nothing."""
    decision = interrupt({"order_id": state.get("order_id"), "issues": state.get("issues", [])})
    deps = runtime.context
    approved = isinstance(decision, dict) and decision.get("action") == "approve"
    # Staff edits are applied to the saved order before resuming; reload it.
    draft = await deps.tools.load_draft(state["customer_id"]) if approved else Draft()
    draft = Draft(tuple(_approved(line) for line in draft.lines))
    route = ASK_CUSTOMER if approved and draft.lines else CANCELLED
    await deps.tools.save_draft(
        state["customer_id"],
        draft,
        status=_STATUS[route],
        total=Decimal(state.get("total", "0")),
        issues=[],
        event={"staff_decision": decision},
    )
    return {
        "review": decision if isinstance(decision, dict) else None,
        "route": route,
        "draft": draft_to_state(draft),
    }


def _approved(line: DraftLine) -> DraftLine:
    return replace(line, needs_review=False, method=line.method or "staff")


@traced("answer_question")
async def answer_question(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    deps = runtime.context
    customer = await deps.tools.customer(state["customer_id"])
    topic = state.get("question_topic") or "price"
    if topic == "balance":
        return {"reply": replies.balance_answer(customer.balance), "route": ANSWER}
    index = deps.matcher.index
    keys = [r["key"] for r in state.get("resolved", []) if r.get("key")]
    # "ghee ka rate?" is ambiguous on purpose: answer for every close candidate.
    for r in state.get("resolved", []):
        if r.get("decision") == "ambiguous":
            keys += r["candidates"][:3]
    keys = list(dict.fromkeys(keys))
    if not keys:
        return {"reply": replies.HANDOFF, "route": HANDOFF}
    if topic == "stock":
        rows_stock = []
        for key in keys:
            available = await deps.tools.available_base(key)
            rows_stock.append((index.by_key[key].name, bool(available and available > 0)))
        return {"reply": replies.stock_answer(rows_stock), "route": ANSWER}
    rows = []
    for key in keys:
        unit = index.by_key[key].default_unit
        price = await deps.tools.price(key, unit, customer.price_tier)
        if price is not None:
            rows.append((index.by_key[key].name, unit, price))
    return {"reply": replies.price_answer(rows) if rows else replies.HANDOFF, "route": ANSWER}


@traced("compose_reply")
async def compose_reply(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    route = state["route"]
    if route == ASK_CUSTOMER:
        index = runtime.context.matcher.index
        draft = draft_from_state(state.get("draft", []))
        total = sum((line.line_total or Decimal(0) for line in draft.lines), Decimal(0))
        summary = replies.order_summary(draft, index, total)
        return {"reply": replies.confirmation(state.get("order_number"), summary)}
    if route == STAFF_REVIEW:
        return {"reply": replies.REVIEW_HOLD}
    if route == CANCELLED:
        return {"reply": replies.CANCELLED}
    return {"reply": replies.HANDOFF}


@traced("hold_reply")
async def hold_reply(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    """Tell the customer we're checking, before pausing for staff."""
    return {"reply": replies.REVIEW_HOLD}


@traced("handoff")
async def handoff(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    return {"route": HANDOFF, "reply": replies.HANDOFF}
