"""Run the evaluation.

Usage (from backend/):
    uv run python -m evals.run                      # rules baseline on every dataset file
    uv run python -m evals.run --config agent-rules # LangGraph agent, no LLM
    uv run python -m evals.run --config agent-llm   # LangGraph agent with Azure OpenAI (cached)
    uv run python -m evals.run --cases ../evals/datasets/examples.jsonl
    uv run python -m evals.run --show-failures

Writes evals/results/<date>-<config>.json and .md, and prints the summary table.
"""

import argparse
import asyncio
import json
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph, run_message
from app.agent.state import AgentDeps
from app.agent.tools import MemoryOrderTools
from app.config import get_settings
from app.integrations.llm import CachedLLM
from app.matching.catalog import CatalogIndex
from app.matching.matcher import MatchConfig, Matcher
from app.matching.rules_pipeline import RulesPipeline
from app.ordering.draft import Draft
from app.workers.main import make_llm
from evals.cases import REPO_ROOT, RESULTS, EvalCase, default_case_files, load_cases
from evals.metrics import CaseScore, as_dict, breakdown, score_case, summarize

SEED = REPO_ROOT / "backend" / "seed"
CATALOG = SEED / "catalog.csv"
LLM_CACHE = REPO_ROOT / "evals" / "cache"
# A trusted customer with room on their credit limit, so routing reflects understanding,
# not credit checks.
EVAL_CUSTOMER_ID = 6

# A runner turns one case into (predicted intent per message, final draft).
Runner = Callable[[EvalCase], Awaitable[tuple[list[str], Draft]]]


def rules_runner(index: CatalogIndex) -> Runner:
    """Day 2 baseline: the plain rules pipeline, no graph."""
    pipeline = RulesPipeline(Matcher(index, MatchConfig()))

    async def run(case: EvalCase) -> tuple[list[str], Draft]:
        draft, intents = Draft(), []
        for message in case.messages:
            step = pipeline.process(message, draft)
            intents.append(step.intent.value)
            draft = step.draft
        return intents, draft

    return run


def agent_runner(index: CatalogIndex, *, use_llm: bool) -> Runner:
    """The full LangGraph agent with in-memory tools; with or without the LLM."""
    llm = None
    if use_llm:
        settings = get_settings()
        inner = make_llm(settings)
        if inner is None:
            raise SystemExit("agent-llm needs AZURE_OPENAI_* settings in .env")
        llm = CachedLLM(inner, LLM_CACHE, settings.azure_openai_deployment_small)
    graph = build_graph(InMemorySaver())

    async def run(case: EvalCase) -> tuple[list[str], Draft]:
        tools = MemoryOrderTools.from_seed(SEED)
        deps = AgentDeps(tools=tools, matcher=Matcher(index), llm=llm)
        intents = []
        for i, message in enumerate(case.messages):
            outcome = await run_message(
                graph,
                deps,
                thread_id=f"{case.id}:{i}",
                business_id=1,
                customer_id=EVAL_CUSTOMER_ID,
                text=message,
            )
            intents.append(str(outcome.state.get("intent", "")))
        return intents, await tools.load_draft(EVAL_CUSTOMER_ID)

    return run


RUNNERS: dict[str, Callable[[CatalogIndex], Runner]] = {
    "rules": rules_runner,
    "agent-rules": lambda index: agent_runner(index, use_llm=False),
    "agent-llm": lambda index: agent_runner(index, use_llm=True),
}


async def evaluate(cases: list[EvalCase], runner: Runner, index: CatalogIndex) -> list[CaseScore]:
    scores = []
    for case in cases:
        if not case.messages:
            continue  # voice case without a reference transcript yet
        intents, draft = await runner(case)
        scores.append(score_case(case, intents, draft, index))
    return scores


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value:.1%}" if isinstance(value, float) else str(value)


METRIC_LABELS = {
    "cases": "Cases",
    "intent_accuracy": "Intent accuracy",
    "line_precision": "Line precision",
    "line_recall": "Line recall",
    "line_f1": "Line F1",
    "product_recall": "Product recall (any qty)",
    "order_exact_match": "Order exact match",
    "auto_confirm_rate": "Auto-confirm rate",
    "auto_confirm_precision": "Auto-confirm precision",
    "ambiguous_sent_to_review": "Ambiguous sent to review",
}


def to_markdown(
    config: str, summary: dict[str, Any], groups: dict[str, dict[str, Any]], meta: dict[str, Any]
) -> str:
    lines = [
        f"# Evaluation: `{config}`",
        "",
        f"Run {meta['timestamp']}, commit `{meta['git_sha']}`, {summary['cases']} cases",
        "",
        "| Metric | Value |",
        "|---|---|",
        *[f"| {label} | {_fmt(summary[key])} |" for key, label in METRIC_LABELS.items()],
        "",
        "## Breakdown",
        "",
        "| Group | Cases | Intent | Line P | Line R | Exact |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in groups.items():
        lines.append(
            f"| {name} | {s['cases']} | {_fmt(s['intent_accuracy'])} | {_fmt(s['line_precision'])} "
            f"| {_fmt(s['line_recall'])} | {_fmt(s['order_exact_match'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", choices=sorted(RUNNERS), default="rules")
    parser.add_argument("--cases", type=Path, nargs="*", help="JSONL files (default: all datasets)")
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--show-failures", action="store_true")
    args = parser.parse_args()

    index = CatalogIndex.from_csv(CATALOG)
    cases = load_cases(args.cases or default_case_files())
    scores = asyncio.run(evaluate(cases, RUNNERS[args.config](index), index))
    summary, groups = summarize(scores), breakdown(scores)
    meta = {
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "git_sha": _git_sha(),
        "config": args.config,
    }
    report = to_markdown(args.config, summary, groups, meta)
    print(report)

    if args.show_failures:
        for s in scores:
            if not s.exact:
                print(f"FAIL {s.case_id}  intents {s.intents_pred} (gold {s.intents_gold})")
                print(f"    predicted {s.predicted}")
                print(f"    gold      {s.gold}")

    if not args.no_save:
        args.out.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.now(UTC):%Y-%m-%d}-{args.config}"
        (args.out / f"{stem}.md").write_text(report, encoding="utf-8", newline="
")
        payload = {
            "meta": meta,
            "summary": summary,
            "breakdown": groups,
            "cases": [as_dict(s) for s in scores],
        }
        (args.out / f"{stem}.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8", newline="
"
        )
        print(f"Saved {args.out / stem}.md and .json")


if __name__ == "__main__":
    main()
