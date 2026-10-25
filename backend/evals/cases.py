"""Evaluation cases: one customer conversation (1+ messages) with its correct answer."""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASETS = REPO_ROOT / "evals" / "datasets"
RESULTS = REPO_ROOT / "evals" / "results"


@dataclass(frozen=True)
class GoldLine:
    sku: str | None  # None: the request is ambiguous and should go to staff review
    unit: str
    qty: Decimal
    mention: str | None = None


@dataclass
class Gold:
    intents: list[str]
    final_lines: list[GoldLine]
    expected_route: str | None = None
    question_about: list[str] = field(default_factory=list)


@dataclass
class EvalCase:
    id: str
    source: str  # "dev" (written while building), "volunteer", "generated"
    channel: str  # "text" or "voice"
    messages: list[str]  # typed text, or reference transcripts for voice cases
    gold: Gold
    author: str | None = None
    task_id: str | None = None
    audio: list[str] = field(default_factory=list)
    language: str | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalCase":
        gold = data["gold"]
        return cls(
            id=data["id"],
            source=data["source"],
            channel=data.get("channel", "text"),
            messages=list(data.get("messages") or data.get("transcripts") or []),
            gold=Gold(
                intents=list(gold["intents"]),
                final_lines=[
                    GoldLine(
                        sku=line["sku"],
                        unit=line["unit"],
                        qty=Decimal(str(line["qty"])),
                        mention=line.get("mention"),
                    )
                    for line in gold["final_lines"]
                ],
                expected_route=gold.get("expected_route"),
                question_about=list(gold.get("question_about", [])),
            ),
            author=data.get("author"),
            task_id=data.get("task_id"),
            audio=list(data.get("audio", [])),
            language=data.get("language"),
            tags=list(data.get("tags", [])),
        )


def load_cases(paths: list[Path]) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in paths:
        with path.open(encoding="utf-8") as f:
            cases += [EvalCase.from_dict(json.loads(line)) for line in f if line.strip()]
    ids = [c.id for c in cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate case ids: {sorted(duplicates)}")
    return cases


def default_case_files() -> list[Path]:
    return sorted(p for p in DATASETS.glob("*.jsonl") if p.stat().st_size > 0)
