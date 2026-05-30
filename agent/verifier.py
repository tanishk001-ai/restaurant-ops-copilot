"""
Constraint verifier — runs WITHOUT the LLM.

verify() takes a draft cart and checks hard business rules.
If any check fails it returns structured ConstraintFailure objects
the planner can use as feedback to produce a revised plan.

Checks (all pure arithmetic — no AI calls):
  1. budget_cap       total_cost ≤ budget_cap  (default ₹10,000)
  2. sanity_qty       packs_needed ≤ max_packs per line item  (default 100)
  3. no_duplicates    each Instamart product_id appears exactly once
  4. coverage         every shortfall material has a cart line (or is uncatalogued)
  5. math             total_cost == Σ subtotals within tolerance
  6. nonzero_qty      packs_needed > 0 for every line item
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

# ── Defaults ───────────────────────────────────────────────────────────────────

BUDGET_CAP_DEFAULT: float   = 10_000.0   # INR
MAX_PACKS_DEFAULT:  int     = 100        # packs per line item
MATH_TOLERANCE:     float   = 0.02      # 2% relative or ₹1 absolute


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class ConstraintFailure:
    check:  str
    detail: str
    items:  list[str] = field(default_factory=list)


@dataclass
class VerificationResult:
    passed:   bool
    failures: list[ConstraintFailure] = field(default_factory=list)
    warnings: list[str]               = field(default_factory=list)

    def feedback_for_planner(self) -> str:
        """
        Structured plain-text feedback the planner includes in its next call to Claude
        when asking for a revised plan.
        """
        if self.passed:
            return "All constraints satisfied."
        lines = ["VERIFICATION FAILED — revise the plan to address these issues:"]
        for f in self.failures:
            lines.append(f"  [{f.check.upper()}] {f.detail}")
            for item in f.items:
                lines.append(f"    • {item}")
        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  ! {w}")
        return "\n".join(lines)


# ── Core verifier ──────────────────────────────────────────────────────────────


def verify(
    cart: dict,
    shortfalls: list[dict] | None = None,
    budget_cap: float = BUDGET_CAP_DEFAULT,
    max_packs:  int   = MAX_PACKS_DEFAULT,
) -> VerificationResult:
    """
    Check the draft cart against hard constraints.

    Args:
        cart:       output of procurement.cart.draft_procurement_cart()
        shortfalls: output of procurement.shortfall.compute_shortfall()
                    (required for coverage check)
        budget_cap: max total cost in INR
        max_packs:  max packs allowed per line item

    Returns:
        VerificationResult — .passed is True only if ALL checks pass.
    """
    failures: list[ConstraintFailure] = []
    warnings: list[str] = []

    line_items: list[dict] = cart.get("line_items", [])
    total_cost: float      = float(cart.get("total_cost", 0))

    # ── 1. Budget cap ─────────────────────────────────────────────────────────
    if total_cost > budget_cap:
        failures.append(ConstraintFailure(
            check  = "budget_cap",
            detail = (
                f"Total ₹{total_cost:,.2f} exceeds budget cap ₹{budget_cap:,.2f}. "
                f"Over by ₹{total_cost - budget_cap:,.2f}."
            ),
        ))

    # ── 2. Sanity quantity ────────────────────────────────────────────────────
    over_limit = [li for li in line_items if li.get("packs_needed", 0) > max_packs]
    if over_limit:
        failures.append(ConstraintFailure(
            check  = "sanity_qty",
            detail = f"{len(over_limit)} item(s) exceed the {max_packs}-pack-per-item sanity limit.",
            items  = [
                f"{li['product_name']}: {li['packs_needed']} packs "
                f"(shortfall {li['shortfall_qty']:.1f} {li['unit']}, "
                f"pack size {li['pack_size']} {li['unit']})"
                for li in over_limit
            ],
        ))

    # ── 3. No duplicate products ──────────────────────────────────────────────
    product_ids = [li.get("instamart_product_id") for li in line_items]
    dupes = [pid for pid, n in Counter(product_ids).items() if n > 1]
    if dupes:
        failures.append(ConstraintFailure(
            check  = "no_duplicates",
            detail = f"Product IDs appear more than once: {dupes}",
            items  = dupes,
        ))

    # ── 4. Coverage ────────────────────────────────────────────────────────────
    if shortfalls is not None:
        cart_mats      = {li["raw_material"] for li in line_items}
        uncatalogued   = set(cart.get("uncatalogued", []))
        uncovered = [
            s["raw_material"]
            for s in shortfalls
            if s["raw_material"] not in cart_mats
            and s["raw_material"] not in uncatalogued
        ]
        if uncovered:
            failures.append(ConstraintFailure(
                check  = "coverage",
                detail = f"{len(uncovered)} shortfall material(s) have no cart line.",
                items  = uncovered,
            ))

    # ── 5. Math (total == Σ subtotals) ────────────────────────────────────────
    computed = round(sum(float(li.get("subtotal", 0)) for li in line_items), 2)
    tolerance = max(1.0, MATH_TOLERANCE * computed)
    if abs(total_cost - computed) > tolerance:
        failures.append(ConstraintFailure(
            check  = "math",
            detail = (
                f"Cart total ₹{total_cost} does not match Σ subtotals ₹{computed} "
                f"(diff ₹{abs(total_cost - computed):.2f}, tolerance ₹{tolerance:.2f})."
            ),
        ))

    # ── 6. Non-zero quantities ────────────────────────────────────────────────
    zero_qty = [li for li in line_items if li.get("packs_needed", 0) <= 0]
    if zero_qty:
        failures.append(ConstraintFailure(
            check  = "nonzero_qty",
            detail = f"{len(zero_qty)} item(s) have zero or negative packs_needed.",
            items  = [li.get("product_name", "?") for li in zero_qty],
        ))

    # ── Warning: empty cart ───────────────────────────────────────────────────
    if not line_items:
        warnings.append("Draft cart is empty — no shortfalls to replenish.")

    return VerificationResult(
        passed   = len(failures) == 0,
        failures = failures,
        warnings = warnings,
    )
