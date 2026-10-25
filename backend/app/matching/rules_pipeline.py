"""The rules-only order pipeline: keyword intent + parser + matcher + draft operations.

It is the baseline the LLM agent is measured against, and the agent's fallback when the
LLM is unavailable."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.matching.lexicon import (
    CANCEL_WORDS,
    COMPLAINT_WORDS,
    MODIFY_WORDS,
    QUESTION_WORDS,
    REMOVE_WORDS,
)
from app.matching.matcher import Decision, Matcher, MatchResult
from app.matching.normalize import clean
from app.matching.parse import ParsedLine, parse_lines, parse_quantity
from app.ordering.draft import (
    AddLine,
    ApplyResult,
    CancelOrder,
    Draft,
    DraftLine,
    Operation,
    RemoveLine,
    SetLine,
    apply_operations,
)


class Intent(StrEnum):
    NEW_ORDER = "new_order"
    ADD = "add"
    MODIFY = "modify"
    CANCEL = "cancel"
    QUESTION = "question"
    OTHER = "other"


@dataclass
class StepResult:
    intent: Intent
    matches: list[MatchResult]
    operations: list[Operation]
    draft: Draft
    warnings: list[str] = field(default_factory=list)


def _has(text: str, words: list[str]) -> bool:
    padded = f" {text} "
    return any((f" {w} " in padded) if w.isalnum() or " " in w else (w in text) for w in words)


class RulesPipeline:
    def __init__(self, matcher: Matcher) -> None:
        self.matcher = matcher

    def classify(
        self, text: str, lines: list[ParsedLine], matches: list[MatchResult], has_draft: bool
    ) -> Intent:
        cleaned = clean(text)
        has_products = any(m.decision is not Decision.NONE for m in matches)
        # Only an explicit number counts: "the oil tin leaked" mentions a tin, not an order.
        # "nido 390 available?" has a number, but it's the pack size, not a quantity.
        has_quantity = parse_quantity(text)[0] is not None and any(
            m.qty is not None for m in matches
        )

        if _has(cleaned, CANCEL_WORDS) and not (has_draft and has_products):
            return Intent.CANCEL
        if _has(cleaned, QUESTION_WORDS) and not has_quantity:
            return Intent.QUESTION
        if _has(cleaned, COMPLAINT_WORDS) and not has_quantity:
            return Intent.OTHER
        if has_draft and (_has(cleaned, MODIFY_WORDS) or _has(cleaned, REMOVE_WORDS)):
            return Intent.MODIFY
        if has_products:
            return Intent.ADD if has_draft else Intent.NEW_ORDER
        return Intent.OTHER

    def process(self, text: str, draft: Draft) -> StepResult:
        lines = parse_lines(text)
        prefer = draft.keys
        matches = [self.matcher.match(line, prefer) for line in lines]
        # Chatter that names no product and has no quantity ("bohat shukriya") is not a line.
        matches = [m for m in matches if m.decision is not Decision.NONE or m.line.qty is not None]
        has_draft = bool(draft.lines)
        intent = self.classify(text, lines, matches, has_draft)

        operations: list[Operation] = []
        if intent is Intent.CANCEL:
            operations.append(CancelOrder())
        elif intent in (Intent.NEW_ORDER, Intent.ADD):
            operations += [AddLine(_to_draft_line(m)) for m in matches]
        elif intent is Intent.MODIFY:
            removing = _has(clean(text), REMOVE_WORDS)
            for m in matches:
                key = m.best.key if m.best else None
                if removing and key:
                    operations.append(RemoveLine(key))
                elif m.qty is not None:
                    operations.append(SetLine(key, m.qty, m.line.unit, m.line.raw))
            if not matches:
                qty, unit = parse_quantity(text)
                if qty is not None:
                    operations.append(SetLine(None, qty, unit, text))

        applied: ApplyResult = apply_operations(draft, operations)
        return StepResult(intent, matches, operations, applied.draft, applied.warnings)


def _to_draft_line(match: MatchResult) -> DraftLine:
    best = match.best
    return DraftLine(
        key=best.key if best else None,
        qty=match.qty,
        unit=match.unit,
        source_text=match.line.raw,
        needs_review=match.decision is not Decision.ACCEPTED
        or any(i in match.issues for i in ("unclear_quantity", "unit_not_sold")),
        issues=tuple(match.issues),
        candidates=tuple(c.key for c in match.candidates),
    )
