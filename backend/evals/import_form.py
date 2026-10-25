"""Turn a Google Form export (CSV) into evaluation cases, with gold answers from tasks.yaml.

Usage (from backend/):
    uv run python -m evals.import_form ../evals/datasets/raw/form_a.csv form_b.csv

Writes evals/datasets/volunteer.jsonl. Text tasks only: voice notes become cases on Day 3,
once speech-to-text is wired up and their reference transcripts are written.
"""

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

import yaml

from evals.cases import DATASETS

TASK_COLUMN = re.compile(r"^([AB]\d{2})\.")
RESPONDENT_COLUMN = "Respondent code"
CONSENT_COLUMN = "Consent"
LANGUAGES_COLUMN = "Which languages do you usually use when texting a shop?"


def load_tasks(path: Path = DATASETS / "tasks.yaml") -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in yaml.safe_load(path.read_text(encoding="utf-8"))}


def _split_messages(answer: str, expected: int) -> list[str]:
    """Multi-message tasks are answered one message per line."""
    parts = [p.strip() for p in answer.replace("\r", "").split("\n") if p.strip()]
    if expected == 1:
        return [" ".join(parts)] if parts else []
    return parts


def _tags(task: dict[str, Any], messages: list[str]) -> list[str]:
    intents = task["gold"]["intents"]
    tags = []
    if len(intents) > 1:
        tags.append("multi_message")
    tags += [i for i in intents if i not in ("new_order", "add")]
    if len(task["gold"]["final_lines"]) > 1:
        tags.append("multi_item")
    if task["gold"].get("expected_route") == "staff_review":
        tags.append("ambiguous")
    if len(messages) != len(intents):
        tags.append("message_count_mismatch")  # check this case by hand
    return tags


def convert(csv_paths: list[Path], tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in csv_paths:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row_number, row in enumerate(csv.DictReader(f), start=1):
                if not row.get(CONSENT_COLUMN, "").strip():
                    continue  # no consent, skip the whole response
                author = (
                    re.sub(r"\W+", "", row.get(RESPONDENT_COLUMN, "")).lower() or f"r{row_number}"
                )
                for column, answer in row.items():
                    m = TASK_COLUMN.match(column or "")
                    if not m or not answer or not answer.strip():
                        continue
                    task = tasks[m.group(1)]
                    if task["channel"] != "text":
                        continue
                    messages = _split_messages(answer, len(task["gold"]["intents"]))
                    cases.append(
                        {
                            "id": f"vol-{author}-{task['id']}",
                            "source": "volunteer",
                            "author": author,
                            "task_id": task["id"],
                            "channel": "text",
                            "messages": messages,
                            "language": row.get(LANGUAGES_COLUMN) or None,
                            "gold": task["gold"],
                            "tags": _tags(task, messages),
                        }
                    )
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("csv", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=DATASETS / "volunteer.jsonl")
    args = parser.parse_args()

    cases = convert(args.csv, load_tasks())
    with args.out.open("w", encoding="utf-8", newline="\n") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    flagged = sum("message_count_mismatch" in c["tags"] for c in cases)
    print(f"Wrote {len(cases)} cases to {args.out}. {flagged} need a manual check (message count).")


if __name__ == "__main__":
    main()
