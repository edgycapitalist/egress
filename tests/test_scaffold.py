"""Scaffold smoke tests.

These assert the repository skeleton and its boundary contract exist and import
cleanly. They are intentionally trivial — real engine, agent, and gateway tests
arrive with their phases. The point now is that `make test` and CI are green on
an empty skeleton.
"""

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

PACKAGES = [
    "agents",
    "agents.scenario_author",
    "agents.archetypes",
    "agents.analyst",
    "agents.critic",
    "agents.common",
    "engine",
    "engine.orderbook",
    "engine.population",
    "engine.stats",
    "engine.metrics",
    "engine.replay",
    "mcp",
    "mcp.market_data",
    "mcp.news",
    "memory",
    "gateway",
]

REQUIRED_FILES = [
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    ".env.example",
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "LICENSE",
    "SECURITY.md",
    "docs/contracts.md",
]


@pytest.mark.parametrize("module", PACKAGES)
def test_packages_import(module: str) -> None:
    assert importlib.import_module(module) is not None


@pytest.mark.parametrize("relpath", REQUIRED_FILES)
def test_required_files_exist(relpath: str) -> None:
    assert (REPO_ROOT / relpath).is_file(), f"missing {relpath}"


def test_env_example_uses_vertex_ai_not_ai_studio() -> None:
    """The Vertex-AI-only auth rule must stay documented in .env.example."""
    env = (REPO_ROOT / ".env.example").read_text()
    assert "GOOGLE_GENAI_USE_VERTEXAI=true" in env
    assert "GOOGLE_API_KEY" not in env.replace("# Do NOT set GOOGLE_API_KEY", "")
