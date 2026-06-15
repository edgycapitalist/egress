"""Deploy or update the Egress orchestrator on Vertex AI Agent Engine.

Usage after GCP resources exist:

    python -m agents.deploy_agent_engine --display-name egress-orchestrator

The command prints the remote resource id. Configure the gateway with:

    EGRESS_ORCHESTRATOR_MODE=agent_engine
    EGRESS_AGENT_ENGINE_ID=<printed resource id>
    EGRESS_ENGINE_SERVICE_URL=<private Cloud Run engine URL>
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from agents.agent_engine_app import build_app

DEFAULT_REQUIREMENTS = [
    "google-adk>=0.2",
    "google-cloud-aiplatform[agent-engines]>=1.60",
    "google-genai>=0.3",
    "pydantic>=2.7",
    "numpy>=2.0",
    "httpx>=0.27",
]


def _agent_engine_module() -> Any:
    errors: list[str] = []
    for module_name in ("vertexai.agent_engines", "vertexai.preview.reasoning_engines"):
        try:
            return __import__(module_name, fromlist=["create", "get"])
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    raise RuntimeError("No Agent Engine SDK module available: " + "; ".join(errors))


def deploy(display_name: str, *, staging_bucket: str | None = None) -> Any:
    import vertexai

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    location = (
        os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("GOOGLE_CLOUD_REGION")
        or "us-central1"
    )
    vertexai.init(project=project, location=location, staging_bucket=staging_bucket)
    agent_engines = _agent_engine_module()
    return agent_engines.create(
        build_app(),
        display_name=display_name,
        requirements=DEFAULT_REQUIREMENTS,
        extra_packages=["agents", "engine", "mcp", "memory", "rag"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-name", default="egress-orchestrator")
    parser.add_argument("--staging-bucket", default=os.getenv("EGRESS_AGENT_STAGING_BUCKET"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deployed without calling Vertex AI.",
    )
    args = parser.parse_args()
    if args.dry_run:
        print(
            {
                "display_name": args.display_name,
                "requirements": DEFAULT_REQUIREMENTS,
                "extra_packages": ["agents", "engine", "mcp", "memory", "rag"],
            }
        )
        return
    resource = deploy(args.display_name, staging_bucket=args.staging_bucket)
    print(resource)


if __name__ == "__main__":
    main()
