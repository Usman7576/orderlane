import csv
from decimal import Decimal
from pathlib import Path

import pytest

from app.matching.catalog import CatalogIndex
from app.ordering.draft import Draft, DraftLine
from evals.cases import DATASETS, EvalCase, default_case_files, load_cases
from evals.import_form import convert, load_tasks
from evals.metrics import score_case, summarize
from evals.run import evaluate, rules_runner

CATALOG = Path(__file__).resolve().parents[2] / "seed" / "catalog.csv"


@pytest.fixture(scope="module")
def index() -> CatalogIndex:
    return CatalogIndex.from_csv(CATALOG)


def _case(
    lines: list[dict[str, object]], intents: list[str] | None = None, **gold: object
) -> EvalCase:
    return EvalCase.from_dict(
        {
            "id": "t",
            "source": "dev",
            "messages": ["x"] * len(intents or ["new_order"]),
            "gold": {"intents": intents or ["new_order"], "final_lines": lines, **gold},
        }
    )


class TestScoring:
    def test_quantities_compare_in_base_units(self, index: CatalogIndex) -> None:
        case = _case([{"sku": "NES-MPK-1L", "unit": "piece", "qty": 6}])
        draft = Draft((DraftLine("NES-MPK-1L", Decimal("0.5"), "carton"),))
        score = score_case(case, ["new_order"], draft, index)
        assert score.exact and score.lines_correct == 1

    def test_wrong_quantity_counts_product_but_not_line(self, index: CatalogIndex) -> None:
        case = _case([{"sku": "LUX-SOP-130", "unit": "dozen", "qty": 2}])
        draft = Draft((DraftLine("LUX-SOP-130", Decimal(3), "dozen"),))
        score = score_case(case, ["new_order"], draft, index)
        assert (score.lines_correct, score.products_correct, score.exact) == (0, 1, False)

    def test_ambiguous_request_is_correct_when_held_for_review(self, index: CatalogIndex) -> None:
        case = _case(
            [{"sku": None, "unit": "dozen", "qty": 1, "mention": "ariel"}],
            expected_route="staff_review",
        )
        held = DraftLine(
            "ARL-DET-1KG", Decimal(1), "dozen", needs_review=True, issues=("ambiguous_product",)
        )
        guessed = DraftLine("ARL-DET-1KG", Decimal(1), "dozen")
        assert score_case(case, ["new_order"], Draft((held,)), index).exact
        assert not score_case(case, ["new_order"], Draft((guessed,)), index).exact

    def test_auto_confirm_precision(self, index: CatalogIndex) -> None:
        good = score_case(
            _case([{"sku": "LUX-SOP-130", "unit": "dozen", "qty": 2}]),
            ["new_order"],
            Draft((DraftLine("LUX-SOP-130", Decimal(2), "dozen"),)),
            index,
        )
        bad = score_case(
            _case([{"sku": "LUX-SOP-130", "unit": "dozen", "qty": 2}]),
            ["new_order"],
            Draft((DraftLine("LFB-SOP-130", Decimal(2), "dozen"),)),
            index,
        )
        summary = summarize([good, bad])
        assert summary["auto_confirm_rate"] == 1.0
        assert summary["auto_confirm_precision"] == 0.5


def test_dataset_files_load_and_reference_real_products(index: CatalogIndex) -> None:
    cases = load_cases(default_case_files())
    assert len(cases) >= 30
    for case in cases:
        for line in case.gold.final_lines:
            if line.sku is not None:
                assert line.unit in index.by_key[line.sku].units, case.id


def test_rules_baseline_regression_guard(index: CatalogIndex) -> None:
    """Smoke evaluation for CI: the rules baseline must not silently get worse on the
    development examples. (These examples were used while writing the rules, so the
    real measurement is the volunteer test set, not this number.)"""
    cases = load_cases([DATASETS / "examples.jsonl"])
    summary = summarize(evaluate(cases, rules_runner(index), index))
    assert summary["order_exact_match"] >= 0.9
    assert summary["auto_confirm_precision"] == 1.0


def test_import_form_builds_cases_with_gold_answers(tmp_path: Path) -> None:
    export = tmp_path / "form_a.csv"
    with export.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Timestamp",
                "Respondent code",
                "Consent",
                "A01. Order 5 cartons of the big (950 gram) Tapal Danedar tea.",
                "A05. Order 4 cartons of Olpers 1-litre milk. Then send a second message.",
            ]
        )
        writer.writerow(["t", "Z K!", "I agree", "5 ctn tapal bara", "4 ctn olpers\n6 kar do"])
        writer.writerow(["t", "nc", "", "no consent", "skip me"])

    cases = convert([export], load_tasks())

    assert [c["id"] for c in cases] == ["vol-zk-A01", "vol-zk-A05"]
    assert cases[1]["messages"] == ["4 ctn olpers", "6 kar do"]
    assert cases[0]["gold"]["final_lines"][0]["sku"] == "TAP-DAN-950"
    assert "message_count_mismatch" not in cases[1]["tags"]
