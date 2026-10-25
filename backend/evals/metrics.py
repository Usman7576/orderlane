"""Scoring a predicted conversation against its gold answer, and aggregating scores.

Quantities are compared in base units (pieces), so "half a carton" and "6 pieces" of a
12-per-carton product are the same answer."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from app.matching.catalog import CatalogIndex
from app.ordering.draft import Draft
from evals.cases import EvalCase

ORDER_INTENTS = {"new_order", "add", "modify"}


def base_qty(
    index: CatalogIndex, key: str | None, unit: str | None, qty: Decimal | None
) -> Decimal | None:
    if key is None or unit is None or qty is None or key not in index.by_key:
        return None
    per_unit = index.by_key[key].units.get(unit)
    return None if per_unit is None else qty * per_unit


@dataclass
class CaseScore:
    case_id: str
    source: str
    channel: str
    tags: list[str]
    intents_gold: list[str]
    intents_pred: list[str]
    intent_correct: int
    intent_total: int
    lines_gold: int  # gold lines with a known product
    lines_pred: int  # predicted lines with a product
    lines_correct: int  # product and quantity correct
    products_correct: int  # product correct, quantity may be wrong
    exact: bool  # whole final order correct, including routing for ambiguous requests
    routed_to_review: bool
    expects_review: bool
    auto_confirmed: bool  # would skip staff review and go straight to the customer
    predicted: list[dict[str, Any]] = field(default_factory=list)
    gold: list[dict[str, Any]] = field(default_factory=list)


def score_case(
    case: EvalCase, intents_pred: list[str], draft: Draft, index: CatalogIndex
) -> CaseScore:
    Pair = tuple[str | None, Decimal | None]
    gold_pairs: Counter[Pair] = Counter(
        (g.sku, base_qty(index, g.sku, g.unit, g.qty)) for g in case.gold.final_lines if g.sku
    )
    # Lines held for review because the product is ambiguous are the right answer to an
    # ambiguous request, so they don't count as wrong product guesses there.
    ambiguous_expected = sum(g.sku is None for g in case.gold.final_lines)
    pred_lines = []
    for line in draft.lines:
        if ambiguous_expected and "ambiguous_product" in line.issues:
            ambiguous_expected -= 1
        elif line.key:
            pred_lines.append(line)
    pred_pairs: Counter[Pair] = Counter(
        (line.key, base_qty(index, line.key, line.unit, line.qty)) for line in pred_lines
    )
    gold_products: Counter[str | None] = Counter(sku for sku, _ in gold_pairs.elements())
    pred_products: Counter[str | None] = Counter(key for key, _ in pred_pairs.elements())

    lines_correct = sum((gold_pairs & pred_pairs).values())
    products_correct = sum((gold_products & pred_products).values())
    intent_correct = sum(g == p for g, p in zip(case.gold.intents, intents_pred, strict=False))

    expects_review = case.gold.expected_route == "staff_review"
    routed_to_review = any(line.needs_review for line in draft.lines)
    if any(g.sku is None for g in case.gold.final_lines):
        # Correct when the uncertain request is held for staff and the clear lines are right.
        exact = (
            routed_to_review
            and not (gold_pairs - pred_pairs)
            and len(draft.lines) == len(case.gold.final_lines)
        )
    else:
        exact = gold_pairs == pred_pairs and len(draft.lines) == len(pred_lines)
    exact = exact and intent_correct == len(case.gold.intents) == len(intents_pred)

    final_intent = intents_pred[-1] if intents_pred else ""
    auto_confirmed = bool(draft.lines) and not routed_to_review and final_intent in ORDER_INTENTS

    return CaseScore(
        case_id=case.id,
        source=case.source,
        channel=case.channel,
        tags=case.tags,
        intents_gold=case.gold.intents,
        intents_pred=intents_pred,
        intent_correct=intent_correct,
        intent_total=len(case.gold.intents),
        lines_gold=sum(gold_pairs.values()),
        lines_pred=sum(pred_pairs.values()),
        lines_correct=lines_correct,
        products_correct=products_correct,
        exact=exact,
        routed_to_review=routed_to_review,
        expects_review=expects_review,
        auto_confirmed=auto_confirmed,
        predicted=[
            {
                "sku": line.key,
                "unit": line.unit,
                "qty": str(line.qty),
                "review": line.needs_review,
                "issues": list(line.issues),
                "text": line.source_text,
            }
            for line in draft.lines
        ],
        gold=[{"sku": g.sku, "unit": g.unit, "qty": str(g.qty)} for g in case.gold.final_lines],
    )


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(scores: list[CaseScore]) -> dict[str, Any]:
    n = len(scores)
    lines_pred = sum(s.lines_pred for s in scores)
    lines_gold = sum(s.lines_gold for s in scores)
    lines_correct = sum(s.lines_correct for s in scores)
    precision = _ratio(lines_correct, lines_pred)
    recall = _ratio(lines_correct, lines_gold)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else None
    auto = [s for s in scores if s.auto_confirmed]
    ambiguous = [s for s in scores if s.expects_review]
    return {
        "cases": n,
        "intent_accuracy": _ratio(
            sum(s.intent_correct for s in scores), sum(s.intent_total for s in scores)
        ),
        "line_precision": precision,
        "line_recall": recall,
        "line_f1": f1,
        "product_recall": _ratio(sum(s.products_correct for s in scores), lines_gold),
        "order_exact_match": _ratio(sum(s.exact for s in scores), n),
        "auto_confirm_rate": _ratio(len(auto), n),
        "auto_confirm_precision": _ratio(sum(s.exact for s in auto), len(auto)),
        "ambiguous_sent_to_review": _ratio(
            sum(s.routed_to_review for s in ambiguous), len(ambiguous)
        ),
    }


def breakdown(scores: list[CaseScore]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[CaseScore]] = defaultdict(list)
    for s in scores:
        groups[f"source:{s.source}"].append(s)
        groups[f"channel:{s.channel}"].append(s)
        for tag in s.tags:
            groups[f"tag:{tag}"].append(s)
    return {name: summarize(group) for name, group in sorted(groups.items())}


def as_dict(score: CaseScore) -> dict[str, Any]:
    return asdict(score)
