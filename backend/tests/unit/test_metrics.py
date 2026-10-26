"""Saved evaluation results are read for the Metrics page, newest per config."""

import json
from pathlib import Path

import pytest

from app.api.metrics import load_evaluations
from app.config import get_settings


def _result(config: str, timestamp: str) -> dict[str, object]:
    return {
        "meta": {"config": config, "timestamp": timestamp, "git_sha": "abc1234"},
        "summary": {"cases": 3, "order_exact_match": 1.0},
        "breakdown": {"source:dev": {"cases": 3}},
    }


@pytest.fixture
def results_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "eval_results_dir", tmp_path)
    return tmp_path


def test_newest_result_per_config(results_dir: Path) -> None:
    for name, config, ts in [
        ("2026-10-01-rules", "rules", "old"),
        ("2026-10-26-rules", "rules", "new"),
        ("2026-10-26-agent-rules", "agent-rules", "only"),
    ]:
        (results_dir / f"{name}.json").write_text(json.dumps(_result(config, ts)))

    evaluations = load_evaluations()

    assert [(e.config, e.timestamp) for e in evaluations] == [
        ("agent-rules", "only"),
        ("rules", "new"),
    ]


def test_broken_files_and_a_missing_folder_are_ignored(
    results_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (results_dir / "2026-10-26-rules.json").write_text("{not json")
    (results_dir / "2026-10-26-other.json").write_text(json.dumps({"meta": {}}))
    assert load_evaluations() == []

    monkeypatch.setattr(get_settings(), "eval_results_dir", results_dir / "missing")
    assert load_evaluations() == []


def test_the_real_results_folder_loads() -> None:
    folder = Path(__file__).resolve().parents[3] / "evals" / "results"
    settings = get_settings()
    original = settings.eval_results_dir
    settings.eval_results_dir = folder
    try:
        configs = {e.config for e in load_evaluations()}
    finally:
        settings.eval_results_dir = original
    assert "rules" in configs
