"""
Phase 6 acceptance tests — testing & agent eval harness.

Always-run (no API key, no DB):
  Eval harness   — scorecard shape, metric targets, results.md written
  Injection      — 5 prompt injection attack vectors all blocked

All tests in this file must pass in CI with no ANTHROPIC_API_KEY and no
running Postgres instance.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Eval harness
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def scorecard():
    """Run the eval harness once and return the AgentScorecard."""
    from tests.agent_eval.eval_harness import run_eval
    return run_eval()


def test_eval_harness_runs_without_error(scorecard):
    """run_eval() completes without exceptions."""
    assert scorecard is not None


def test_eval_harness_scenario_count(scorecard):
    """Harness must exercise at least 5 distinct scenarios."""
    assert scorecard.total_scenarios >= 5, (
        f"Expected ≥ 5 scenarios, got {scorecard.total_scenarios}"
    )


def test_eval_harness_success_rate(scorecard):
    """At least 50 % of scenarios should pass the verifier (4/8 default scenarios)."""
    assert scorecard.success_rate >= 0.5, (
        f"Success rate {scorecard.success_rate:.1%} < 50%"
    )


def test_eval_harness_over_order_rate(scorecard):
    """Over-ordering rate must be ≤ 20 % of all line items."""
    assert scorecard.over_order_rate <= 0.20, (
        f"Over-ordering rate {scorecard.over_order_rate:.1%} > 20%"
    )


def test_eval_harness_budget_pass_rate(scorecard):
    """At least 70 % of scenarios must stay under the ₹10,000 budget cap."""
    assert scorecard.budget_pass_rate >= 0.70, (
        f"Budget pass rate {scorecard.budget_pass_rate:.1%} < 70%"
    )


def test_eval_harness_avg_iterations(scorecard):
    """Mean verifier iterations to converge must be ≤ 2.5."""
    assert scorecard.avg_iterations <= 2.5, (
        f"Avg iterations {scorecard.avg_iterations:.2f} > 2.5"
    )


def test_eval_harness_prediction_accuracy(scorecard):
    """Verifier predictions must match expected_pass ≥ 80% of the time."""
    assert scorecard.prediction_accuracy >= 0.80, (
        f"Prediction accuracy {scorecard.prediction_accuracy:.1%} < 80%"
    )


def test_eval_harness_hallucinated_scenario_detected(scorecard):
    """The hallucinated_item scenario must be detected by the harness."""
    halluc_results = [
        r for r in scorecard.results
        if r.scenario.name == "hallucinated_item"
    ]
    assert halluc_results, "hallucinated_item scenario not found in results"
    assert halluc_results[0].hallucinated_ids, (
        "Hallucinated product ID not detected in hallucinated_item scenario"
    )


def test_eval_harness_scorecard_text_renders(scorecard):
    """format_scorecard() must return a non-empty multi-line string."""
    from tests.agent_eval.eval_harness import format_scorecard

    text = format_scorecard(scorecard)
    assert len(text) > 200
    assert "Scorecard" in text
    assert "AGGREGATE METRICS" in text
    assert "Success rate" in text


def test_eval_harness_writes_results_md(scorecard, tmp_path):
    """write_results_md() must write a valid Markdown file."""
    from tests.agent_eval.eval_harness import write_results_md

    out = tmp_path / "results.md"
    write_results_md(scorecard, out)

    assert out.exists(), "results.md was not created"
    content = out.read_text(encoding="utf-8")
    assert "Agent Eval Results" in content
    assert "| Metric |" in content
    assert "Success rate" in content
    assert "Budget pass rate" in content
    assert len(content.splitlines()) >= 15


def test_eval_harness_results_md_written_to_default_path():
    """Running main() writes tests/agent_eval/results.md to the package path."""
    from tests.agent_eval.eval_harness import RESULTS_MD, run_eval, write_results_md

    sc = run_eval()
    write_results_md(sc, RESULTS_MD)

    assert RESULTS_MD.exists(), f"results.md not written to {RESULTS_MD}"
    content = RESULTS_MD.read_text(encoding="utf-8")
    assert "Agent Eval Results" in content


# ─────────────────────────────────────────────────────────────────────────────
# Injection tests (re-imported from agent_eval package for phase acceptance)
# ─────────────────────────────────────────────────────────────────────────────

# Import all injection tests so pytest counts them as part of this phase.
from tests.agent_eval.test_injection import (  # noqa: E402
    test_budget_override_in_cart_data_ignored,
    test_fake_tool_call_in_plan_steps_blocked,
    test_poison_description_does_not_clear_cart,
    test_poison_product_name_does_not_inflate_cart,
    test_sql_injection_in_nl_query_blocked,
)

# ─────────────────────────────────────────────────────────────────────────────
# Scorecard printed to stdout when the acceptance test runs
# ─────────────────────────────────────────────────────────────────────────────


def test_scorecard_printed_to_stdout(scorecard, capsys):
    """Running format_scorecard and printing produces visible output."""
    from tests.agent_eval.eval_harness import format_scorecard

    print(format_scorecard(scorecard))
    captured = capsys.readouterr()
    assert "Scorecard" in captured.out
    assert "AGGREGATE METRICS" in captured.out
