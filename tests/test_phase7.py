"""
Phase 7 acceptance tests — deployment artefacts.

Always-run (no DB, no Docker required):
  Deployment files — Dockerfile, docker-compose.yml, railway.toml,
                     entrypoint.sh, deploy_smoke_test.sh all exist
                     and contain the expected directives
  /health endpoint  — returns 503 + {status:error} when DB is unreachable,
                     returns the right JSON shape when DB is up

DB-required:
  /health with real DB — status=ok, orders > 0 (uses seeded_db fixture)
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Deployment file checks (no runtime needed)
# ─────────────────────────────────────────────────────────────────────────────


def test_dockerfile_exists():
    assert (ROOT / "Dockerfile").exists(), "Dockerfile not found"


def test_dockerfile_uses_python_312():
    text = (ROOT / "Dockerfile").read_text()
    assert "python:3.12" in text, "Dockerfile must use python:3.12 base image"


def test_dockerfile_exposes_8000():
    text = (ROOT / "Dockerfile").read_text()
    assert "EXPOSE 8000" in text


def test_dockerfile_has_entrypoint():
    text = (ROOT / "Dockerfile").read_text()
    assert "ENTRYPOINT" in text, "Dockerfile must use ENTRYPOINT (not just CMD)"
    assert "entrypoint.sh" in text


def test_entrypoint_script_exists_and_is_executable():
    ep = ROOT / "entrypoint.sh"
    assert ep.exists(), "entrypoint.sh not found"
    mode = ep.stat().st_mode
    assert mode & stat.S_IXUSR, "entrypoint.sh is not executable (chmod +x it)"


def test_entrypoint_runs_seeder_then_uvicorn():
    text = (ROOT / "entrypoint.sh").read_text()
    assert "data_gen.seed" in text, "entrypoint.sh must run the seeder"
    assert "uvicorn" in text, "entrypoint.sh must start uvicorn"
    assert "8000" in text or "PORT" in text, "entrypoint.sh must reference port 8000 or $PORT"


def test_docker_compose_has_two_services():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "pgvector/pgvector:pg16" in text, "docker-compose must use pgvector:pg16 image"
    assert "build: ." in text or "build:" in text, "docker-compose must build the app"
    assert "service_healthy" in text, "app must wait for DB healthcheck"


def test_docker_compose_injects_database_url():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "DATABASE_URL" in text, "docker-compose must set DATABASE_URL env var"
    assert "@db:" in text, "DATABASE_URL must point to the 'db' service host"


def test_docker_compose_app_depends_on_db():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "depends_on" in text


def test_railway_toml_exists():
    assert (ROOT / "railway.toml").exists(), "railway.toml not found"


def test_railway_toml_healthcheck():
    text = (ROOT / "railway.toml").read_text()
    assert "/health" in text, "railway.toml must set healthcheckPath = '/health'"
    assert "DOCKERFILE" in text, "railway.toml must use DOCKERFILE builder"


def test_smoke_test_script_exists_and_executable():
    script = ROOT / "deploy_smoke_test.sh"
    assert script.exists(), "deploy_smoke_test.sh not found"
    mode = script.stat().st_mode
    assert mode & stat.S_IXUSR, "deploy_smoke_test.sh is not executable"


def test_smoke_test_covers_all_four_endpoints():
    text = (ROOT / "deploy_smoke_test.sh").read_text()
    for endpoint in ["/health", "/forecast", "/inventory", "/draft-order"]:
        assert endpoint in text, f"deploy_smoke_test.sh missing check for {endpoint}"


# ─────────────────────────────────────────────────────────────────────────────
# /health endpoint — always-run (mocked DB failure)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def api_client():
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql://copilot:copilot@localhost:5432/restaurant_ops",
    )
    from api.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_health_returns_503_when_db_unreachable(api_client):
    """When the DB connection fails, /health must return 503 (not 200)."""
    import psycopg2
    with patch("api.main._conn", side_effect=psycopg2.OperationalError("connection refused")):
        r = api_client.get("/health")
    assert r.status_code == 503, (
        f"Expected 503 when DB is down, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["status"] == "error"
    assert "detail" in body


def test_health_response_shape_when_db_ok(api_client):
    """When DB is reachable, /health returns {status, orders, forecasts}."""
    import psycopg2
    try:
        conn = psycopg2.connect(
            os.environ.get(
                "DATABASE_URL",
                "postgresql://copilot:copilot@localhost:5432/restaurant_ops",
            ),
            connect_timeout=2,
        )
        conn.close()
        db_up = True
    except Exception:
        db_up = False

    if not db_up:
        pytest.skip("DB not reachable — skipping live health check")

    r = api_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "orders" in body
    assert "forecasts" in body
    assert isinstance(body["orders"], int)


# ─────────────────────────────────────────────────────────────────────────────
# /health with seeded DB
# ─────────────────────────────────────────────────────────────────────────────


def test_health_orders_nonzero_after_seeding(seeded_db):
    """After seeding, /health must report orders > 0."""
    os.environ["DATABASE_URL"] = seeded_db

    from api.main import app, _reset_state
    _reset_state()
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["orders"] > 0, "Expected seeded orders, got 0"
