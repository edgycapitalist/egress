"""Minimal `.env` loader and Vertex AI configuration.

The agents read their project, location, and model selection from the process
environment, populated from a `.env` file (see `.env.example`). We deliberately
avoid a hard dependency on `python-dotenv` so the offline test suite — which only
has the engine's core deps plus ADK — needs nothing extra to import this module.

Authentication rule (CLAUDE.md / AGENTS.md): Gemini is reached **only** through
Vertex AI with Application Default Credentials. ``GOOGLE_GENAI_USE_VERTEXAI`` must
be true and a project + location must be set. A ``GOOGLE_API_KEY`` (AI Studio) is
forbidden and actively rejected here so it can never leak into the live path.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = REPO_ROOT / ".env"

# Defaults match .env.example so behaviour is identical whether or not a .env
# file is present. Vertex is the product path; these are not AI Studio settings.
DEFAULTS: dict[str, str] = {
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    # Gemini 3.x models are served from the "global" Vertex location, not a region.
    "GOOGLE_CLOUD_LOCATION": "global",
    "GEMINI_MODEL_FAST": "gemini-3.1-flash-lite",
    "GEMINI_MODEL_STRONG": "gemini-3.1-pro-preview",
    "DETERMINISTIC_BASELINE": "true",
    "CACHED_REPLAY": "false",
    "EGRESS_SEED": "42",
}


def load_dotenv(path: Path | str | None = None, *, override: bool = False) -> dict[str, str]:
    """Load ``KEY=VALUE`` pairs from a `.env` file into ``os.environ``.

    Lines that are blank or start with ``#`` are ignored; surrounding quotes and
    an optional ``export`` prefix are stripped. Existing environment variables are
    preserved unless ``override`` is set. Returns the parsed pairs (for tests).
    """
    env_path = Path(path) if path is not None else _ENV_PATH
    parsed: dict[str, str] = {}
    if not env_path.exists():
        return parsed
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


def _env(key: str) -> str | None:
    val = os.environ.get(key)
    if val is None:
        val = DEFAULTS.get(key)
    return val


def _truthy(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def baseline_mode() -> bool:
    """True when the system should run with zero LLM calls (offline/test mode).

    The deterministic baseline is the *fallback*; the live Vertex/Gemini path is
    the product. This flag only chooses which one the orchestrator assembles.
    """
    return _truthy(_env("DETERMINISTIC_BASELINE"))


def fast_model() -> str:
    """Gemini model for the archetype mood-setters (they refresh every k ticks)."""
    return _env("GEMINI_MODEL_FAST") or "gemini-3.1-flash-lite"


def strong_model() -> str:
    """Stronger Gemini model for the analyst and the calibration critic."""
    return _env("GEMINI_MODEL_STRONG") or "gemini-3.1-pro-preview"


def seed() -> int:
    try:
        return int(_env("EGRESS_SEED") or "42")
    except ValueError:
        return 42


class VertexAuthError(RuntimeError):
    """Raised when the Vertex AI configuration is missing or forbidden."""


def assert_vertex_config() -> dict[str, str]:
    """Validate the Vertex AI configuration for a *live* run.

    Returns the resolved ``{project, location, use_vertexai}`` on success. Raises
    :class:`VertexAuthError` if a live Gemini call could not possibly succeed —
    or if an AI Studio ``GOOGLE_API_KEY`` is present, which is forbidden by the
    project's authentication rule. Never call this in baseline mode; it gates the
    live path only.
    """
    load_dotenv()

    if os.environ.get("GOOGLE_API_KEY"):
        raise VertexAuthError(
            "GOOGLE_API_KEY is set. Egress uses Vertex AI only, never AI Studio. "
            "Remove GOOGLE_API_KEY from your environment and .env."
        )

    if not _truthy(_env("GOOGLE_GENAI_USE_VERTEXAI")):
        raise VertexAuthError(
            "GOOGLE_GENAI_USE_VERTEXAI must be true so google-genai/ADK route to "
            "Vertex AI. Set it in .env (see .env.example)."
        )

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project or project == "your-gcp-project-id":
        raise VertexAuthError(
            "GOOGLE_CLOUD_PROJECT is not set. Authenticate with "
            "`gcloud auth application-default login` and set GOOGLE_CLOUD_PROJECT "
            "and GOOGLE_CLOUD_LOCATION in .env."
        )

    location = _env("GOOGLE_CLOUD_LOCATION") or "us-central1"
    return {
        "project": project,
        "location": location,
        "use_vertexai": "true",
    }
