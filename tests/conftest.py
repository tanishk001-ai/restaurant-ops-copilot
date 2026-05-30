"""Shared pytest fixtures for Restaurant Ops Copilot."""

import os
import pytest
import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://copilot:copilot@localhost:5432/restaurant_ops",
)


def db_available() -> bool:
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def seeded_db():
    """Apply schema + seed data once per test session. Skip if DB is not up."""
    if not db_available():
        pytest.skip("Postgres not reachable — start with: docker compose up db -d")

    from data_gen.seed import seed_database
    seed_database(DATABASE_URL)
    yield DATABASE_URL
