"""
Natural-language operations query module.

ask(question) → calls an LLM (via Gemini) to generate SQL → executes it
→ LLM explains.

The two-phase flow:
  1. The model receives the question + DB schema and calls execute_sql() with a SELECT.
  2. Python validates (SELECT-only) and executes the query.
  3. The model receives the raw results and produces a plain-English answer.

Safety:
  • Only SELECT statements are allowed (enforced before execution).
  • Forbidden keywords (DROP, DELETE, INSERT, UPDATE, …) block execution.
  • The query runs with the read-only credentials in DATABASE_URL.
  • Any instruction embedded in a question is treated as text, not SQL.
"""

from __future__ import annotations

import os
import re
from datetime import date

import psycopg2
import psycopg2.extras
from google import genai
from google.genai import types

DEFAULT_MODEL    = "gemini-3.5-flash"
DEFAULT_DATABASE = "postgresql://copilot:copilot@localhost:5432/restaurant_ops"

# ── DB schema (embedded in system prompt) ─────────────────────────────────────

_SCHEMA = """
Tables and columns:

restaurants(id INT PK, name TEXT, locality TEXT, cuisine TEXT, created_at TIMESTAMPTZ)

menu_items(id INT PK, restaurant_id INT FK→restaurants, name TEXT, price NUMERIC,
           category TEXT, active BOOL, created_at TIMESTAMPTZ)
  categories: main_course | biryani | bread | starter | side | dessert | beverage

orders(id BIGINT PK, restaurant_id INT FK, item_id INT FK→menu_items,
       qty SMALLINT, ordered_at TIMESTAMPTZ)
  Revenue = SUM(o.qty * mi.price)

bill_of_materials(id INT PK, dish_id INT FK→menu_items,
                  raw_material TEXT, qty_per_unit NUMERIC, unit TEXT)

inventory(id INT PK, restaurant_id INT FK, raw_material TEXT,
          current_qty NUMERIC, unit TEXT, reorder_point NUMERIC, updated_at TIMESTAMPTZ)

raw_material_catalog(id INT PK, name TEXT, instamart_product_id TEXT,
                     product_name TEXT, pack_size NUMERIC, unit TEXT,
                     price NUMERIC, category TEXT, in_stock BOOL)

forecasts(id BIGINT PK, restaurant_id INT FK, item_id INT FK→menu_items,
          forecast_date DATE, predicted_qty NUMERIC, model_version TEXT,
          created_at TIMESTAMPTZ)
"""

_SYSTEM_PROMPT = f"""\
You are a natural-language SQL query engine for Restaurant Ops Copilot \
(restaurant_id = 1, "Spice Junction", North Indian, Indiranagar, Bengaluru).

{_SCHEMA}

CONVENTIONS:
  • "revenue" = SUM(o.qty * mi.price)
  • "last week" = ordered_at BETWEEN date_trunc('week', NOW() - INTERVAL '7 days')
                                 AND date_trunc('week', NOW()) - INTERVAL '1 second'
  • "this week" = ordered_at >= date_trunc('week', NOW())
  • "today" / "yesterday" use CURRENT_DATE
  • "forecast" refers to the forecasts table with model_version = 'xgb_v1'
  • Always filter WHERE restaurant_id = 1 (or join via menu_items)
  • For inventory: use the inventory table (current_qty, unit, reorder_point)
  • Use ILIKE for case-insensitive text matching on ingredient names

RULES:
  1. Only generate SELECT statements — no INSERT, UPDATE, DELETE, DROP, CREATE, TRUNCATE.
  2. Call the execute_sql tool exactly once with a valid PostgreSQL SELECT.
  3. Keep SQL concise; use meaningful aliases.
  4. Limit results to ≤ 20 rows unless the question asks for more.
"""

_SQL_FUNCTION = types.FunctionDeclaration(
    name="execute_sql",
    description=(
        "Execute a read-only SQL SELECT query on the restaurant operations DB "
        "and return the results for answering the user's question."
    ),
    parameters={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A valid PostgreSQL SELECT statement.",
            },
            "explanation": {
                "type": "string",
                "description": "One sentence: what this query does.",
            },
        },
        "required": ["sql", "explanation"],
    },
)
_SQL_TOOL = types.Tool(function_declarations=[_SQL_FUNCTION])

# ── SQL safety guard ───────────────────────────────────────────────────────────

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)


def _validate_sql(sql: str) -> None:
    """Raise ValueError if the SQL is not a safe SELECT."""
    stripped = sql.strip()
    if not stripped.upper().startswith("SELECT"):
        raise ValueError(f"Only SELECT statements are allowed. Got: {stripped[:60]!r}")
    m = _FORBIDDEN.search(stripped)
    if m:
        raise ValueError(f"Forbidden keyword in SQL: {m.group()!r}")


# ── DB execution ───────────────────────────────────────────────────────────────


def _run_sql(sql: str, database_url: str | None = None) -> list[dict]:
    """Execute a validated SELECT and return rows as list-of-dicts."""
    db_url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE)
    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _fmt_rows(rows: list[dict]) -> str:
    if not rows:
        return "(no rows returned)"
    cols   = list(rows[0].keys())
    header = " | ".join(cols)
    sep    = "-" * len(header)
    lines  = [header, sep]
    for row in rows[:20]:
        lines.append(" | ".join(str(row[c]) for c in cols))
    if len(rows) > 20:
        lines.append(f"… ({len(rows)} rows total, showing first 20)")
    return "\n".join(lines)


# ── Fast path: common questions answered without an LLM call ───────────────────
#
# The Gemini free tier on this project is capped at a small number of
# requests/day (observed: 20/day, per model) — easy to exhaust with normal
# demo usage. These patterns cover the questions restaurant owners actually
# ask most often — revenue, stock levels, top dishes, forecasts — and
# answer them directly from SQL + Python formatting, so the common case
# never touches the LLM (or its quota, or the network) at all, and answers
# instantly. Anything that doesn't match falls through to the full
# Gemini-based flow below, unchanged.

_PERIODS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\blast week\b"), "last_week",
     "o.ordered_at BETWEEN date_trunc('week', NOW() - INTERVAL '7 days') "
     "AND date_trunc('week', NOW()) - INTERVAL '1 second'"),
    (re.compile(r"\bthis week\b"), "this_week",
     "o.ordered_at >= date_trunc('week', NOW())"),
    (re.compile(r"\bthis month\b"), "this_month",
     "o.ordered_at >= date_trunc('month', NOW())"),
    (re.compile(r"\byesterday\b"), "yesterday",
     "DATE(o.ordered_at) = CURRENT_DATE - INTERVAL '1 day'"),
    (re.compile(r"\btoday\b"), "today",
     "DATE(o.ordered_at) = CURRENT_DATE"),
]

_PERIOD_PHRASE = {
    "last_week":  "last week",
    "this_week":  "this week",
    "this_month": "this month",
    "yesterday":  "yesterday",
    "today":      "today",
}


def _detect_period(q: str, default: str) -> tuple[str, str]:
    """Return (period_label, sql_where_clause), falling back to `default`."""
    for pattern, label, clause in _PERIODS:
        if pattern.search(q):
            return label, clause
    return next((label, clause) for _, label, clause in _PERIODS if label == default)


def _list_raw_materials(database_url: str | None) -> list[str]:
    conn = psycopg2.connect(database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE))
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT raw_material FROM inventory WHERE restaurant_id = 1")
    materials = [r[0] for r in cur.fetchall()]
    conn.close()
    return materials


def _fast_path_stock(q: str, question: str, database_url: str | None) -> dict | None:
    if not re.search(r"\bstock\b|\binventory\b|how much .*(do we have|left|in stock)", q):
        return None

    # Longest label first — avoids a short word matching inside a longer,
    # more specific material name (e.g. "paste" inside "ginger garlic paste").
    for mat in sorted(_list_raw_materials(database_url), key=len, reverse=True):
        label = mat.replace("_", " ")
        if label in q:
            sql = (
                "SELECT raw_material, current_qty, unit, reorder_point "
                f"FROM inventory WHERE restaurant_id = 1 AND raw_material = '{mat}'"
            )
            rows = _run_sql(sql, database_url)
            if not rows:
                return None
            row = rows[0]
            qty, reorder, unit = float(row["current_qty"]), float(row["reorder_point"]), row["unit"]
            status = "above" if qty > reorder else "at or below"
            answer = (
                f"Current stock of {label} is {qty:,.1f} {unit}, "
                f"which is {status} the reorder point of {reorder:,.1f} {unit}."
            )
            return {
                "question":    question,
                "sql":         sql,
                "sql_explain": f"Look up current inventory level for {label}.",
                "raw_rows":    rows,
                "answer":      answer,
            }
    return None


def _fast_path_revenue(q: str, question: str, database_url: str | None) -> dict | None:
    if not re.search(r"\brevenue\b|\bdrove\b|\bbest.?sell", q):
        return None

    period_label, clause = _detect_period(q, default="last_week")
    sql = (
        "SELECT mi.name, SUM(o.qty * mi.price) AS total_revenue "
        "FROM orders o JOIN menu_items mi ON o.item_id = mi.id "
        f"WHERE o.restaurant_id = 1 AND {clause} "
        "GROUP BY mi.name ORDER BY total_revenue DESC LIMIT 1"
    )
    rows = _run_sql(sql, database_url)
    phrase = _PERIOD_PHRASE[period_label]
    if not rows:
        answer = f"No orders were recorded {phrase}."
    else:
        name, revenue = rows[0]["name"], float(rows[0]["total_revenue"])
        answer = f"{name} drove the most revenue {phrase}, generating ₹{revenue:,.2f}."
    return {
        "question":    question,
        "sql":         sql,
        "sql_explain": f"Find the top revenue-generating dish {phrase}.",
        "raw_rows":    rows,
        "answer":      answer,
    }


def _fast_path_top_dishes(q: str, question: str, database_url: str | None) -> dict | None:
    if "top" not in q or not re.search(r"\bdish|\bselling\b|\bsold\b", q):
        return None

    m = re.search(r"top\s+(\d+)", q)
    n = int(m.group(1)) if m else 3
    period_label, clause = _detect_period(q, default="this_month")
    sql = (
        "SELECT mi.name, SUM(o.qty) AS total_qty "
        "FROM orders o JOIN menu_items mi ON o.item_id = mi.id "
        f"WHERE o.restaurant_id = 1 AND {clause} "
        f"GROUP BY mi.name ORDER BY total_qty DESC LIMIT {n}"
    )
    rows = _run_sql(sql, database_url)
    phrase = _PERIOD_PHRASE[period_label]
    if not rows:
        answer = f"No orders were recorded {phrase}."
    else:
        listed = ", ".join(f"{r['name']} ({int(r['total_qty'])} units)" for r in rows)
        answer = f"Top {len(rows)} selling dishes {phrase}: {listed}."
    return {
        "question":    question,
        "sql":         sql,
        "sql_explain": f"Top {n} dishes by quantity sold {phrase}.",
        "raw_rows":    rows,
        "answer":      answer,
    }


def _fast_path_forecast(q: str, question: str, database_url: str | None) -> dict | None:
    if "forecast" not in q:
        return None

    if "week" in q:
        period_clause = (
            "f.forecast_date >= date_trunc('week', CURRENT_DATE) "
            "AND f.forecast_date < date_trunc('week', CURRENT_DATE) + INTERVAL '7 days'"
        )
        phrase = "this week's"
        # Multi-day window — sum per dish across the week so the same dish
        # doesn't show up several times (once per day) in the answer.
        sql = (
            "SELECT mi.name, SUM(f.predicted_qty) AS predicted_qty "
            "FROM forecasts f JOIN menu_items mi ON f.item_id = mi.id "
            f"WHERE f.restaurant_id = 1 AND f.model_version = 'xgb_v1' AND {period_clause} "
            "GROUP BY mi.name ORDER BY predicted_qty DESC LIMIT 10"
        )
    else:
        phrase = "tomorrow's"
        sql = (
            "SELECT mi.name, f.predicted_qty "
            "FROM forecasts f JOIN menu_items mi ON f.item_id = mi.id "
            "WHERE f.restaurant_id = 1 AND f.model_version = 'xgb_v1' "
            "AND f.forecast_date = CURRENT_DATE + INTERVAL '1 day' "
            "ORDER BY f.predicted_qty DESC LIMIT 10"
        )

    rows = _run_sql(sql, database_url)
    if not rows:
        answer = f"No {phrase} forecast is available yet."
    else:
        listed = ", ".join(f"{r['name']} ({float(r['predicted_qty']):.0f} units)" for r in rows[:3])
        answer = f"{phrase.capitalize()} forecast is led by {listed}."
    return {
        "question":    question,
        "sql":         sql,
        "sql_explain": f"Forecasted demand for {phrase}.",
        "raw_rows":    rows,
        "answer":      answer,
    }


_FAST_PATHS = (_fast_path_stock, _fast_path_revenue, _fast_path_top_dishes, _fast_path_forecast)


def _try_fast_path(question: str, database_url: str | None) -> dict | None:
    q = question.lower().strip()
    for handler in _FAST_PATHS:
        try:
            result = handler(q, question, database_url)
        except Exception:
            return None   # any fast-path hiccup falls through to the LLM
        if result is not None:
            return result
    return None


# ── Main public function ───────────────────────────────────────────────────────


def ask(
    question:     str,
    database_url: str | None = None,
    model:        str        = DEFAULT_MODEL,
    verbose:      bool       = False,
) -> dict:
    """
    Answer a natural-language ops question about the restaurant.

    Returns:
        {
            question:    str   — original question
            sql:         str   — generated SELECT statement
            sql_explain: str   — what the query does
            raw_rows:    list  — raw DB result rows (list of dicts)
            answer:      str   — plain-English answer from the LLM
        }

    Raises:
        ValueError if the model generates disallowed SQL.
        RuntimeError if the model does not call execute_sql.
    """
    fast = _try_fast_path(question, database_url)
    if fast is not None:
        if verbose:
            print(f"  [fast-path, no LLM call] {fast['sql']}")
        return fast

    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
        # A hung DNS lookup or network stall would otherwise block this
        # process's whole event loop for the SDK's default timeout — every
        # other request (including the emergency-reorder button) queues up
        # behind it. Fail fast instead.
        http_options=types.HttpOptions(timeout=12_000),
    )

    # ── Phase 1: Generate SQL ─────────────────────────────────────────────────
    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=question)]),
    ]

    r1 = client.models.generate_content(
        model  = model,
        contents = contents,
        config = types.GenerateContentConfig(
            system_instruction = _SYSTEM_PROMPT,
            max_output_tokens  = 1024,
            tools              = [_SQL_TOOL],
            tool_config        = types.ToolConfig(
                function_calling_config = types.FunctionCallingConfig(
                    mode                   = "ANY",
                    allowed_function_names = ["execute_sql"],
                )
            ),
        ),
    )

    sql         = ""
    sql_explain = ""
    for call in r1.function_calls or []:
        if call.name == "execute_sql":
            args        = dict(call.args)
            sql         = args["sql"]
            sql_explain = args.get("explanation", "")
            break
    else:
        raise RuntimeError(
            "nl_ops: model did not call execute_sql. "
            f"Response: {r1}"
        )

    if verbose:
        print(f"  SQL: {sql}")

    # ── Safety validation ─────────────────────────────────────────────────────
    _validate_sql(sql)

    # ── Execute ───────────────────────────────────────────────────────────────
    rows = _run_sql(sql, database_url=database_url)
    rows_text = _fmt_rows(rows)

    # ── Phase 2: Explain results ──────────────────────────────────────────────
    contents.append(r1.candidates[0].content)
    contents.append(
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="execute_sql",
                        response={"result": rows_text},
                    )
                )
            ],
        )
    )
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part(text=(
                "Using only the query results above, answer the original "
                "question in one or two plain-English sentences. "
                "Do not call any tools and do not write tool-call syntax — "
                "reply with prose only."
            ))],
        )
    )

    r2 = client.models.generate_content(
        model  = model,
        contents = contents,
        config = types.GenerateContentConfig(
            system_instruction = _SYSTEM_PROMPT,
            max_output_tokens  = 512,
        ),
    )

    answer = r2.text or ""

    return {
        "question":    question,
        "sql":         sql,
        "sql_explain": sql_explain,
        "raw_rows":    rows,
        "answer":      answer.strip(),
    }
