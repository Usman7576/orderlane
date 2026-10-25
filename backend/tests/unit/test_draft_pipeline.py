from decimal import Decimal
from pathlib import Path

import pytest

from app.matching.catalog import CatalogIndex
from app.matching.matcher import Matcher
from app.matching.rules_pipeline import Intent, RulesPipeline
from app.ordering.draft import (
    AddLine,
    CancelOrder,
    Draft,
    DraftLine,
    RemoveLine,
    SetLine,
    apply_operations,
)

CATALOG = Path(__file__).resolve().parents[2] / "seed" / "catalog.csv"


def _line(key: str, qty: int, unit: str = "carton") -> DraftLine:
    return DraftLine(key, Decimal(qty), unit)


class TestDraftOperations:
    def test_adding_the_same_product_and_unit_sums_quantities(self) -> None:
        result = apply_operations(Draft(), [AddLine(_line("A", 2)), AddLine(_line("A", 3))])
        assert [(line.key, line.qty) for line in result.draft.lines] == [("A", Decimal(5))]

    def test_different_units_stay_separate(self) -> None:
        result = apply_operations(
            Draft(), [AddLine(_line("A", 2)), AddLine(_line("A", 1, "piece"))]
        )
        assert len(result.draft.lines) == 2

    def test_set_and_remove(self) -> None:
        draft = Draft((_line("A", 2), _line("B", 1)))
        result = apply_operations(draft, [SetLine("A", Decimal(6)), RemoveLine("B")])
        assert [(line.key, line.qty, line.unit) for line in result.draft.lines] == [
            ("A", Decimal(6), "carton")
        ]

    def test_set_without_product_applies_to_single_line(self) -> None:
        result = apply_operations(Draft((_line("A", 2),)), [SetLine(None, Decimal(5))])
        assert result.draft.lines[0].qty == Decimal(5)

    def test_set_for_unknown_product_is_flagged(self) -> None:
        result = apply_operations(Draft((_line("A", 2),)), [SetLine("Z", Decimal(1))])
        assert result.warnings == ["set_unknown_line:Z"]
        assert result.draft.lines[-1].needs_review

    def test_cancel_clears_everything(self) -> None:
        result = apply_operations(Draft((_line("A", 2),)), [CancelOrder()])
        assert result.draft == Draft((), cancelled=True)


@pytest.fixture(scope="module")
def pipeline() -> RulesPipeline:
    return RulesPipeline(Matcher(CatalogIndex.from_csv(CATALOG)))


def _run(pipeline: RulesPipeline, *messages: str) -> tuple[list[Intent], Draft]:
    draft, intents = Draft(), []
    for message in messages:
        step = pipeline.process(message, draft)
        intents.append(step.intent)
        draft = step.draft
    return intents, draft


class TestRulesPipeline:
    def test_new_order_then_add(self, pipeline: RulesPipeline) -> None:
        intents, draft = _run(pipeline, "2 ctn pepsi 1.5", "aur 1 ctn pepsi 500 bhi")
        assert intents == [Intent.NEW_ORDER, Intent.ADD]
        assert {line.key for line in draft.lines} == {"PEP-1500", "PEP-500"}

    def test_modify_quantity_with_product(self, pipeline: RulesPipeline) -> None:
        intents, draft = _run(pipeline, "4 carton olpers", "olpers 6 carton kar do")
        assert intents == [Intent.NEW_ORDER, Intent.MODIFY]
        assert [(line.key, line.qty) for line in draft.lines] == [("OLP-MLK-1L", Decimal(6))]

    def test_modify_quantity_only(self, pipeline: RulesPipeline) -> None:
        _, draft = _run(pipeline, "3 ctn tapal 475", "5 kar do")
        assert [(line.key, line.qty, line.unit) for line in draft.lines] == [
            ("TAP-DAN-475", Decimal(5), "carton")
        ]

    def test_remove_item(self, pipeline: RulesPipeline) -> None:
        _, draft = _run(pipeline, "1 dz sunsilk aur 1 dz head and shoulders", "sunsilk hata do")
        assert [line.key for line in draft.lines] == ["HNS-SHP-360"]

    def test_cancel(self, pipeline: RulesPipeline) -> None:
        intents, draft = _run(pipeline, "2 ctn sprite", "order cancel kar dein")
        assert intents[-1] is Intent.CANCEL and draft.cancelled and not draft.lines

    @pytest.mark.parametrize(
        ("text", "intent"),
        [
            ("ghee ka rate kya hai?", Intent.QUESTION),
            ("nido 390 available hai?", Intent.QUESTION),
            ("kal wala oil ka tin leak tha", Intent.OTHER),
            ("Assalam o alaikum bhai, 3 peti olpers bhej dein", Intent.NEW_ORDER),
        ],
    )
    def test_intents(self, pipeline: RulesPipeline, text: str, intent: Intent) -> None:
        assert pipeline.process(text, Draft()).intent is intent

    def test_ambiguous_line_needs_review(self, pipeline: RulesPipeline) -> None:
        step = pipeline.process("1 dozen ariel", Draft())
        [line] = step.draft.lines
        assert line.needs_review and len(line.candidates) >= 2
