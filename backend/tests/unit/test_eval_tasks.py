"""The volunteer task list must stay consistent with the seed catalog."""

import csv
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.services.seeding import parse_units

ROOT = Path(__file__).resolve().parents[3]
TASKS = yaml.safe_load((ROOT / "evals/datasets/tasks.yaml").read_text(encoding="utf-8"))
with (ROOT / "backend/seed/catalog.csv").open(encoding="utf-8", newline="") as f:
    CATALOG = {row["sku"]: parse_units(row["units"]) for row in csv.DictReader(f)}

INTENTS = {"new_order", "add", "modify", "cancel", "question", "other"}


def test_task_ids_are_unique() -> None:
    ids = [t["id"] for t in TASKS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["id"])
def test_task_gold_matches_catalog(task: dict[str, Any]) -> None:
    gold = task["gold"]
    assert task["channel"] in {"text", "voice"}
    assert gold["intents"] and set(gold["intents"]) <= INTENTS
    for line in gold["final_lines"]:
        assert line["qty"] > 0
        if line["sku"] is None:
            assert gold.get("expected_route") == "staff_review"
            continue
        assert line["sku"] in CATALOG, f"unknown SKU {line['sku']}"
        assert line["unit"] in CATALOG[line["sku"]], f"{line['sku']} has no unit {line['unit']}"
