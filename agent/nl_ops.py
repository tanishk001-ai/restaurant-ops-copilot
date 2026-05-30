"""
Natural-language operations query module.

ask(question) → calls Claude to generate SQL → executes it → Claude explains.

The two-phase flow:
  1. Claude receives the question + DB schema and calls execute_sql() with a SELECT.
  2. Python validates (SELECT-only) and executes the query.
  3. Claude receives the raw results and produces a plain-English answer.

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

import anthropic
import psycopg2
import psycopg2.extras

DEFAULT_MODEL    = "claude-3-5-haiku-20241022"
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

_SQL_TOOL: dict = {
    "name": "execute_sql",
    "description": (
        "Execute a read-only SQL SELECT query on the restaurant operations DB "
        "and return the results for answering the user's question."
    ),
    "input_schema": {
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
}

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
            answer:      str   — plain-English answer from Claude
        }

    Raises:
        ValueError if Claude generates disallowed SQL.
        RuntimeError if Claude does not call execute_sql.
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    # ── Phase 1: Generate SQL ─────────────────────────────────────────────────
    r1 = client.messages.create(
        model       = model,
        max_tokens  = 1024,
        system      = _SYSTEM_PROMPT,
        messages    = [{"role": "user", "content": question}],
        tools       = [_SQL_TOOL],
        tool_choice = {"type": "any"},
    )

    sql         = ""
    sql_explain = ""
    tool_use_id = ""
    for block in r1.content:
        if block.type == "tool_use" and block.name == "execute_sql":
            sql         = block.input["sql"]
            sql_explain = block.input.get("explanation", "")
            tool_use_id = block.id
            break
    else:
        raise RuntimeError(
            "nl_ops: Claude did not call execute_sql. "
            f"Response: {r1.content}"
        )

    if verbose:
        print(f"  SQL: {sql}")

    # ── Safety validation ─────────────────────────────────────────────────────
    _validate_sql(sql)

    # ── Execute ───────────────────────────────────────────────────────────────
    rows = _run_sql(sql, database_url=database_url)
    rows_text = _fmt_rows(rows)

    # ── Phase 2: Explain results ──────────────────────────────────────────────
    r2 = client.messages.create(
        model      = model,
        max_tokens = 512,
        system     = _SYSTEM_PROMPT,
        messages   = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": r1.content},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": rows_text,
                    }
                ],
            },
        ],
        tools = [_SQL_TOOL],   # keep tools defined so the model doesn't error
    )

    answer = ""
    for block in r2.content:
        if hasattr(block, "text"):
            answer += block.text

    return {
        "question":    question,
        "sql":         sql,
        "sql_explain": sql_explain,
        "raw_rows":    rows,
        "answer":      answer.strip(),
    }
