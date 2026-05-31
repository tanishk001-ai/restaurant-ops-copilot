"""
Agent eval harness — Phase 6.

Scores the procurement agent on synthetic test scenarios.
No LLM or DB required — all scenarios use pure-Python verifier + mock carts.

Metrics:
  success_rate        % of scenarios where verifier.verify() passes
  over_order_rate     % of line items where qty_ordered > 2× shortfall_qty
  hallucinated_count  line items whose product_id is not in the Instamart catalog
  budget_pass_rate    % of scenarios with total_cost ≤ ₹10,000
  avg_iterations      mean verifier iterations to converge (simulated)

Run as a script:
    python -m tests.agent_eval.eval_harness
    python tests/agent_eval/eval_harness.py

Or import run_eval() / write_results_md() for use in pytest.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agent.verifier import BUDGET_CAP_DEFAULT, VerificationResult, verify

RESULTS_MD = Path(__file__).parent / "results.md"

# Product IDs that exist in the real Instamart catalog (matches synthetic seed data)
VALID_CATALOG_IDS: frozenset[str] = frozenset({
    "IM_001", "IM_002", "IM_003", "IM_004", "IM_005",
    "IM_006", "IM_007", "IM_008", "IM_009", "IM_010",
    "IM_011", "IM_012", "IM_013", "IM_014", "IM_015",
    "IM_016", "IM_017", "IM_018", "IM_019", "IM_020",
    "IM_021",
})


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class EvalScenario:
    name: str
    description: str
    cart: dict
    shortfalls: list[dict]
    expected_pass: bool
    simulated_iterations: int = 1   # planner iterations needed to converge


@dataclass
class ScenarioResult:
    scenario: EvalScenario
    vr: VerificationResult
    over_ordered_items: list[str]    # product names with qty_ordered > 2× shortfall
    hallucinated_ids: list[str]      # product_ids not in VALID_CATALOG_IDS
    iterations_used: int
    correct_prediction: bool          # verifier result matched expected_pass


@dataclass
class AgentScorecard:
    results: list[ScenarioResult]
    timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )

    @property
    def total_scenarios(self) -> int:
        return len(self.results)

    @property
    def passed_scenarios(self) -> int:
        return sum(1 for r in self.results if r.vr.passed)

    @property
    def success_rate(self) -> float:
        return self.passed_scenarios / self.total_scenarios if self.total_scenarios else 0.0

    @property
    def prediction_accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.correct_prediction) / self.total_scenarios

    @property
    def _all_line_items(self) -> list[dict]:
        items: list[dict] = []
        for r in self.results:
            items.extend(r.scenario.cart.get("line_items", []))
        return items

    @property
    def over_order_rate(self) -> float:
        items = self._all_line_items
        if not items:
            return 0.0
        over = sum(
            1 for li in items
            if li.get("qty_ordered", 0) > 2.0 * li.get("shortfall_qty", float("inf"))
        )
        return over / len(items)

    @property
    def hallucinated_item_count(self) -> int:
        return sum(len(r.hallucinated_ids) for r in self.results)

    @property
    def budget_pass_rate(self) -> float:
        if not self.results:
            return 0.0
        under = sum(
            1 for r in self.results
            if r.scenario.cart.get("total_cost", float("inf")) <= BUDGET_CAP_DEFAULT
        )
        return under / self.total_scenarios

    @property
    def avg_iterations(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.iterations_used for r in self.results) / self.total_scenarios


# ── Synthetic test scenarios ────────────────────────────────────────────────────


def _li(
    mat: str,
    pid: str,
    name: str,
    shortfall: float,
    pack_size: float,
    packs: int,
    price: float,
    unit: str = "g",
) -> dict:
    qty_ordered = round(packs * pack_size, 4)
    return {
        "raw_material":         mat,
        "instamart_product_id": pid,
        "product_name":         name,
        "shortfall_qty":        shortfall,
        "pack_size":            pack_size,
        "unit":                 unit,
        "packs_needed":         packs,
        "qty_ordered":          qty_ordered,
        "unit_price":           price,
        "subtotal":             round(packs * price, 2),
    }


def _cart(line_items: list[dict], uncatalogued: list[str] | None = None) -> dict:
    total = round(sum(li["subtotal"] for li in line_items), 2)
    return {
        "line_items":   line_items,
        "total_items":  len(line_items),
        "total_packs":  sum(li["packs_needed"] for li in line_items),
        "total_cost":   total,
        "currency":     "INR",
        "uncatalogued": uncatalogued or [],
        "note":         "DRAFT — awaiting human approval",
    }


SCENARIOS: list[EvalScenario] = [

    # ── 1. Green path ─────────────────────────────────────────────────────────
    EvalScenario(
        name="green_path",
        description="Normal demand day — 3 catalogued items, well under budget",
        cart=_cart([
            _li("tomato",  "IM_001", "Fresh Tomatoes 1kg",      5000.0, 1000.0, 5, 120.0),
            _li("onion",   "IM_002", "Red Onions 1kg",          3000.0, 1000.0, 3,  80.0),
            _li("chicken", "IM_007", "Chicken Breast 500g",     2000.0,  500.0, 4, 250.0),
        ]),
        shortfalls=[
            {"raw_material": "tomato",  "shortfall_qty": 5000.0, "unit": "g"},
            {"raw_material": "onion",   "shortfall_qty": 3000.0, "unit": "g"},
            {"raw_material": "chicken", "shortfall_qty": 2000.0, "unit": "g"},
        ],
        expected_pass=True,
        simulated_iterations=1,
    ),

    # ── 2. Budget exceeded → verifier fails ───────────────────────────────────
    EvalScenario(
        name="budget_exceeded",
        description="High-value mutton order pushes total above ₹10k cap",
        cart=_cart([
            _li("mutton", "IM_008", "Mutton Shoulder 1kg", 15000.0, 1000.0, 15, 750.0),
        ]),
        shortfalls=[
            {"raw_material": "mutton", "shortfall_qty": 15000.0, "unit": "g"},
        ],
        expected_pass=False,
        simulated_iterations=2,
    ),

    # ── 3. Over-ordering (>100 packs, sanity_qty failure) ─────────────────────
    EvalScenario(
        name="over_ordering",
        description="Planner orders 150 packs of garlic (>100-pack sanity limit)",
        cart=_cart([
            _li("garlic", "IM_003", "Garlic 250g", 200.0, 250.0, 150, 45.0),
        ]),
        shortfalls=[
            {"raw_material": "garlic", "shortfall_qty": 200.0, "unit": "g"},
        ],
        expected_pass=False,
        simulated_iterations=3,
    ),

    # ── 4. Hallucinated product ID (not in catalog) ───────────────────────────
    EvalScenario(
        name="hallucinated_item",
        description="Cart has a made-up product_id (IM-SAF-FAKE-999) not in catalog",
        cart=_cart([
            _li("saffron", "IM-SAF-FAKE-999", "Premium Saffron 1g",
                5.0, 1.0, 5, 500.0),
            _li("oil", "IM_012", "Sunflower Oil 1L",
                2000.0, 1000.0, 2, 180.0, unit="ml"),
        ]),
        shortfalls=[
            {"raw_material": "saffron", "shortfall_qty": 5.0,    "unit": "g"},
            {"raw_material": "oil",     "shortfall_qty": 2000.0, "unit": "ml"},
        ],
        expected_pass=True,   # verifier passes (valid math/budget); harness flags halluc
        simulated_iterations=1,
    ),

    # ── 5. Perfect minimal plan ───────────────────────────────────────────────
    EvalScenario(
        name="minimal_perfect",
        description="Single item, exact ceiling quantity, far under budget",
        cart=_cart([
            _li("rice", "IM_010", "Basmati Rice 1kg", 3500.0, 1000.0, 4, 150.0),
        ]),
        shortfalls=[
            {"raw_material": "rice", "shortfall_qty": 3500.0, "unit": "g"},
        ],
        expected_pass=True,
        simulated_iterations=1,
    ),

    # ── 6. Duplicate product IDs → no_duplicates failure ─────────────────────
    EvalScenario(
        name="duplicate_products",
        description="Same Instamart product_id appears for two different materials",
        cart=_cart([
            _li("flour",      "IM_011", "Atta Wheat Flour 5kg", 8000.0, 5000.0, 2, 320.0),
            _li("wheat_flour","IM_011", "Atta Wheat Flour 5kg", 4000.0, 5000.0, 1, 320.0),
        ]),
        shortfalls=[
            {"raw_material": "flour",       "shortfall_qty": 8000.0, "unit": "g"},
            {"raw_material": "wheat_flour", "shortfall_qty": 4000.0, "unit": "g"},
        ],
        expected_pass=False,
        simulated_iterations=2,
    ),

    # ── 7. Coverage gap → verifier fails ─────────────────────────────────────
    EvalScenario(
        name="coverage_gap",
        description="Paneer shortfall exists but no matching cart line",
        cart=_cart(
            [_li("tomato", "IM_001", "Fresh Tomatoes 1kg", 1000.0, 1000.0, 1, 120.0)],
            uncatalogued=[],   # paneer is catalogued but MISSING from cart
        ),
        shortfalls=[
            {"raw_material": "tomato", "shortfall_qty": 1000.0, "unit": "g"},
            {"raw_material": "paneer", "shortfall_qty": 2000.0, "unit": "g"},
        ],
        expected_pass=False,
        simulated_iterations=2,
    ),

    # ── 8. Weekend restock — multi-ingredient, all valid ──────────────────────
    EvalScenario(
        name="weekend_restock",
        description="Weekend spike restock — 4 items, all valid, ₹3,490 total",
        cart=_cart([
            _li("paneer",   "IM_004", "Fresho Fresh Paneer 200g", 2500.0,  200.0, 13, 75.0),
            _li("cream",    "IM_009", "Amul Fresh Cream 200ml",   1800.0,  200.0,  9, 55.0, "ml"),
            _li("ginger",   "IM_003", "Fresh Ginger 200g",         800.0,  200.0,  4, 40.0),
            _li("coriander","IM_005", "Fresh Coriander 100g",      350.0,  100.0,  4, 25.0),
        ]),
        shortfalls=[
            {"raw_material": "paneer",    "shortfall_qty": 2500.0, "unit": "g"},
            {"raw_material": "cream",     "shortfall_qty": 1800.0, "unit": "ml"},
            {"raw_material": "ginger",    "shortfall_qty":  800.0, "unit": "g"},
            {"raw_material": "coriander", "shortfall_qty":  350.0, "unit": "g"},
        ],
        expected_pass=True,
        simulated_iterations=1,
    ),
]


# ── Scoring ─────────────────────────────────────────────────────────────────────


def score_scenario(scenario: EvalScenario) -> ScenarioResult:
    """Run verifier on one scenario and collect per-item metrics."""
    vr = verify(
        scenario.cart,
        shortfalls=scenario.shortfalls,
        budget_cap=BUDGET_CAP_DEFAULT,
    )

    items = scenario.cart.get("line_items", [])

    over_ordered = [
        li["product_name"]
        for li in items
        if li.get("qty_ordered", 0) > 2.0 * li.get("shortfall_qty", float("inf"))
    ]

    hallucinated = [
        li["instamart_product_id"]
        for li in items
        if li.get("instamart_product_id") not in VALID_CATALOG_IDS
    ]

    iterations = 1 if vr.passed else scenario.simulated_iterations

    return ScenarioResult(
        scenario=scenario,
        vr=vr,
        over_ordered_items=over_ordered,
        hallucinated_ids=hallucinated,
        iterations_used=iterations,
        correct_prediction=(vr.passed == scenario.expected_pass),
    )


def run_eval(
    scenarios: list[EvalScenario] | None = None,
) -> AgentScorecard:
    """Run all scenarios and return a populated AgentScorecard."""
    if scenarios is None:
        scenarios = SCENARIOS
    return AgentScorecard(results=[score_scenario(s) for s in scenarios])


# ── Formatting ──────────────────────────────────────────────────────────────────

W = 80


def _bar(value: float, width: int = 18) -> str:
    """ASCII progress bar [████░░░░]."""
    clamped = max(0.0, min(1.0, value))
    filled = round(clamped * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def format_scorecard(sc: AgentScorecard) -> str:
    lines: list[str] = []
    sep = "=" * W

    lines += [
        sep,
        "  Restaurant Ops Copilot — Agent Eval Scorecard  (Phase 6)".center(W),
        f"  {sc.timestamp}".center(W),
        sep,
        "",
        "SCENARIO RESULTS",
        "-" * W,
        f"  {'Scenario':<22} {'Description':<32} {'Status':<7} {'Iters'}  Flags",
        "-" * W,
    ]

    for r in sc.results:
        status = "PASS" if r.vr.passed else "FAIL"
        flags: list[str] = []
        if r.hallucinated_ids:
            flags.append(f"halluc({len(r.hallucinated_ids)})")
        if r.over_ordered_items:
            flags.append(f"overorder({len(r.over_ordered_items)})")
        for f in r.vr.failures:
            flags.append(f.check)
        desc = textwrap.shorten(r.scenario.description, width=32, placeholder="…")
        lines.append(
            f"  {r.scenario.name:<22} {desc:<32} {status:<7} {r.iterations_used}"
            + (f"  [{', '.join(flags)}]" if flags else "")
        )

    lines += ["", "  Prediction accuracy (expected vs actual verifier outcome):"]
    for r in sc.results:
        tick = "✓" if r.correct_prediction else "✗"
        exp = "PASS" if r.scenario.expected_pass else "FAIL"
        got = "PASS" if r.vr.passed else "FAIL"
        lines.append(f"    {tick} {r.scenario.name:<22} expected={exp}  got={got}")

    lines += ["-" * W, "", "AGGREGATE METRICS", "-" * W]

    targets = [
        ("Success rate",       f"{sc.success_rate*100:.1f}%",
         sc.success_rate,     "≥ 50%",  sc.success_rate >= 0.5),
        ("Over-ordering rate", f"{sc.over_order_rate*100:.1f}%",
         1 - sc.over_order_rate, "≤ 20%", sc.over_order_rate <= 0.20),
        ("Hallucinated items", str(sc.hallucinated_item_count),
         1.0 if sc.hallucinated_item_count == 0 else 0.0,
         "= 0",   sc.hallucinated_item_count == 0),
        ("Budget pass rate",   f"{sc.budget_pass_rate*100:.1f}%",
         sc.budget_pass_rate, "≥ 70%",  sc.budget_pass_rate >= 0.70),
        ("Avg verifier iters", f"{sc.avg_iterations:.2f}",
         max(0.0, 1 - (sc.avg_iterations - 1) / 3),
         "≤ 2.5",  sc.avg_iterations <= 2.5),
        ("Prediction accuracy",f"{sc.prediction_accuracy*100:.1f}%",
         sc.prediction_accuracy, "≥ 80%", sc.prediction_accuracy >= 0.80),
    ]

    lines.append(f"  {'Metric':<24} {'Value':<10} {'Bar':<20} {'Target':<10} {'OK'}")
    lines.append(f"  {'-'*24} {'-'*10} {'-'*20} {'-'*10} {'--'}")
    for name, val, bar_v, target, ok in targets:
        tick = "YES" if ok else "NO"
        lines.append(f"  {name:<24} {val:<10} {_bar(bar_v):<20} {target:<10} {tick}")

    lines += ["-" * W, ""]
    issues = [name for name, _, _, _, ok in targets if not ok]
    if not issues:
        lines.append("  OVERALL: ALL METRICS WITHIN TARGET".center(W))
    else:
        lines.append(
            f"  OVERALL: NEEDS ATTENTION — {', '.join(issues)}".center(W)
        )
    lines.append(sep)
    return "\n".join(lines)


# ── results.md writer ──────────────────────────────────────────────────────────


def write_results_md(sc: AgentScorecard, path: Path = RESULTS_MD) -> None:
    """Write the scorecard as a Markdown file."""
    md: list[str] = [
        "# Agent Eval Results — Phase 6",
        "",
        f"Generated: `{sc.timestamp}`",
        "",
        "## Scenario Results",
        "",
        "| Scenario | Description | Verifier | Iters | Flags |",
        "|---|---|:---:|:---:|---|",
    ]

    for r in sc.results:
        status = "PASS" if r.vr.passed else "FAIL"
        flags: list[str] = []
        if r.hallucinated_ids:
            flags.append(f"hallucinated: `{', '.join(r.hallucinated_ids)}`")
        if r.over_ordered_items:
            flags.append(f"over-ordered: {len(r.over_ordered_items)} item(s)")
        for f in r.vr.failures:
            flags.append(f"`{f.check}`")
        md.append(
            f"| `{r.scenario.name}` | {r.scenario.description} | {status} | "
            f"{r.iterations_used} | {'; '.join(flags) or '—'} |"
        )

    md += [
        "",
        "## Aggregate Metrics",
        "",
        "| Metric | Value | Target | Pass |",
        "|---|---|---|:---:|",
        f"| Success rate | {sc.success_rate*100:.1f}% | ≥ 50% | {'YES' if sc.success_rate >= 0.5 else 'NO'} |",
        f"| Over-ordering rate | {sc.over_order_rate*100:.1f}% | ≤ 20% | {'YES' if sc.over_order_rate <= 0.20 else 'NO'} |",
        f"| Hallucinated items | {sc.hallucinated_item_count} | = 0 | {'YES' if sc.hallucinated_item_count == 0 else 'NO'} |",
        f"| Budget pass rate | {sc.budget_pass_rate*100:.1f}% | ≥ 70% | {'YES' if sc.budget_pass_rate >= 0.70 else 'NO'} |",
        f"| Avg verifier iterations | {sc.avg_iterations:.2f} | ≤ 2.5 | {'YES' if sc.avg_iterations <= 2.5 else 'NO'} |",
        f"| Prediction accuracy | {sc.prediction_accuracy*100:.1f}% | ≥ 80% | {'YES' if sc.prediction_accuracy >= 0.80 else 'NO'} |",
        "",
        "---",
        "",
        "*Generated by `tests/agent_eval/eval_harness.py`*",
    ]

    path.write_text("\n".join(md) + "\n", encoding="utf-8")


# ── CLI entry point ────────────────────────────────────────────────────────────


def main() -> None:
    sc = run_eval()
    text = format_scorecard(sc)
    print(text)
    write_results_md(sc)
    print(f"\n  Results written → {RESULTS_MD.resolve()}")


if __name__ == "__main__":
    main()
