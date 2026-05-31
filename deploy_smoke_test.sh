#!/usr/bin/env bash
# deploy_smoke_test.sh — hit the four core endpoints and assert all return 200.
#
# Usage:
#   ./deploy_smoke_test.sh                       # defaults to http://localhost:8000
#   ./deploy_smoke_test.sh https://myapp.up.railway.app
#
# Exit code 0 = all passed, 1 = at least one failure.
# Compatible with macOS (BSD) and Linux (GNU) curl/head.

set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
PASS=0
FAIL=0
ERRORS=()
TMPFILE=$(mktemp)
trap 'rm -f "$TMPFILE"' EXIT

# ── Retry helper ──────────────────────────────────────────────────────────────
# wait_ready: poll /health until it returns 200 (up to 120s)
wait_ready() {
    echo "Waiting for $BASE_URL/health to be ready…"
    local max=24   # 24 × 5s = 120s
    local n=0
    while [ $n -lt $max ]; do
        code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health" 2>/dev/null || true)
        if [ "$code" = "200" ]; then
            echo "  → ready (waited $((n * 5))s)"
            return 0
        fi
        n=$((n + 1))
        echo "  → not ready (status=$code), retrying in 5s… ($n/$max)"
        sleep 5
    done
    echo "  → timed out after $((max * 5))s"
    return 1
}

# ── Single endpoint check ─────────────────────────────────────────────────────
check() {
    local label="$1"
    local method="$2"
    local path="$3"
    local body_arg="${4:-}"

    printf "  %-8s %-22s  " "$method" "$path"

    if [ "$method" = "POST" ]; then
        http_code=$(curl -s -o "$TMPFILE" -w "%{http_code}" -X POST \
            -H "Content-Type: application/json" \
            -d "$body_arg" \
            "$BASE_URL$path" 2>/dev/null)
    else
        http_code=$(curl -s -o "$TMPFILE" -w "%{http_code}" \
            "$BASE_URL$path" 2>/dev/null)
    fi

    body_out=$(cat "$TMPFILE")

    if [ "$http_code" = "200" ]; then
        echo "✓  HTTP $http_code"
        PASS=$((PASS + 1))
    else
        echo "✗  HTTP $http_code"
        snippet=$(echo "$body_out" | cut -c1-120)
        ERRORS+=("$label: expected 200, got $http_code — $snippet")
        FAIL=$((FAIL + 1))
    fi
}

# ── Validate /health JSON has required fields ─────────────────────────────────
check_health_fields() {
    printf "  %-8s %-22s  " "JSON" "/health fields"
    body=$(curl -s "$BASE_URL/health" 2>/dev/null)
    if echo "$body" | grep -q '"status"' && echo "$body" | grep -q '"orders"'; then
        echo "✓  {status, orders} present"
        PASS=$((PASS + 1))
    else
        echo "✗  missing required fields"
        ERRORS+=("health JSON: expected {status, orders} in: $body")
        FAIL=$((FAIL + 1))
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

echo "════════════════════════════════════════════════════════════"
echo "  Restaurant Ops Copilot — Deployment Smoke Test"
echo "  Target: $BASE_URL"
echo "════════════════════════════════════════════════════════════"
echo ""

wait_ready

echo ""
echo "Running checks…"
echo ""

check   "health"    GET  "/health"
check_health_fields
check   "forecast"  GET  "/forecast"
check   "inventory" GET  "/inventory"
check   "draft"     POST "/draft-order" "{}"

echo ""
echo "────────────────────────────────────────────────────────────"

if [ "${#ERRORS[@]}" -gt 0 ]; then
    echo "  Failures:"
    for err in "${ERRORS[@]}"; do
        echo "    ✗ $err"
    done
    echo ""
fi

echo "  Results: $PASS passed, $FAIL failed"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "  SMOKE TEST FAILED"
    echo "════════════════════════════════════════════════════════════"
    exit 1
else
    echo "  ALL SMOKE TESTS PASSED"
    echo "════════════════════════════════════════════════════════════"
    exit 0
fi
