"""Phase 0 smoke test — verifies the project scaffold is importable."""

import importlib
import os


PACKAGES = [
    "synthetic_mcp",
    "data_gen",
    "forecasting",
    "procurement",
    "agent",
    "mcp_client",
    "api",
]


def test_packages_importable():
    for pkg in PACKAGES:
        mod = importlib.import_module(pkg)
        assert mod is not None


def test_env_example_exists():
    root = os.path.dirname(os.path.dirname(__file__))
    assert os.path.isfile(os.path.join(root, ".env.example"))


def test_docker_compose_exists():
    root = os.path.dirname(os.path.dirname(__file__))
    assert os.path.isfile(os.path.join(root, "docker-compose.yml"))


def test_db_schema_stub_exists():
    root = os.path.dirname(os.path.dirname(__file__))
    assert os.path.isfile(os.path.join(root, "db", "schema.sql"))
