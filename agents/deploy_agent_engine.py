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
    "google-cloud-discoveryengine>=0.13",
    "google-genai>=0.3",
    "pydantic>=2.7",
    "numpy>=2.0",
    "httpx>=0.27",
    "cloudpickle>=3.0",
    "psycopg[binary]>=3.1",
    "pgvector>=0.2",
    "redis>=5.0",
]


def _env_vars() -> dict[str, Any]:
    from google.cloud.aiplatform_v1.types import env_var

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID") or ""
    location = (
        os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("GOOGLE_CLOUD_REGION")
        or "us-central1"
    )
    values: dict[str, Any] = {
        "PROJECT_ID": project,
        "GOOGLE_CLOUD_LOCATION": location,
        "GOOGLE_GENAI_USE_VERTEXAI": os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true"),
        "EGRESS_GEMINI_LIVE_MODE": os.getenv("EGRESS_GEMINI_LIVE_MODE", "fast"),
        "VERTEX_SEARCH_DATASTORE_ID": os.getenv(
            "VERTEX_SEARCH_DATASTORE_ID", "egress-crisis-corpus"
        ),
        "VERTEX_SEARCH_LOCATION": os.getenv("VERTEX_SEARCH_LOCATION", "global"),
        "VERTEX_SEARCH_COLLECTION": os.getenv(
            "VERTEX_SEARCH_COLLECTION", "default_collection"
        ),
    }
    for key in (
        "EGRESS_ENGINE_SERVICE_URL",
        "MARKET_DATA_MCP_URL",
        "NEWS_MCP_URL",
        "POSITIONING_MCP_URL",
        "VERTEX_MEMORY_BANK_ID",
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    secret_env = {
        "ALPHAVANTAGE_API_KEY": os.getenv(
            "EGRESS_ALPHAVANTAGE_SECRET", "egress-alphavantage-api-key"
        ),
        "DATABASE_URL": os.getenv("EGRESS_DATABASE_URL_SECRET", "egress-database-url"),
        "REDIS_URL": os.getenv("EGRESS_REDIS_URL_SECRET", "egress-redis-url"),
    }
    for key, secret in secret_env.items():
        if secret:
            values[key] = env_var.SecretRef(secret=secret, version="latest")
    return values


def _agent_engine_module() -> Any:
    errors: list[str] = []
    for module_name in ("vertexai.agent_engines", "vertexai.preview.reasoning_engines"):
        try:
            return __import__(module_name, fromlist=["create", "get"])
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
    raise RuntimeError("No Agent Engine SDK module available: " + "; ".join(errors))


def _resource_name(resource: Any) -> str:
    for attr in ("resource_name", "name"):
        value = getattr(resource, attr, None)
        if value:
            return str(value)
    raw = str(resource)
    marker = "projects/"
    if marker in raw:
        return raw[raw.index(marker) :].split()[0].strip("',)")
    return raw


def _package_kwargs(display_name: str, service_account: str | None) -> dict[str, Any]:
    return {
        "agent_engine": build_app(),
        "display_name": display_name,
        "requirements": DEFAULT_REQUIREMENTS,
        "extra_packages": ["agents", "engine", "mcp", "memory", "rag"],
        "env_vars": _env_vars(),
        "service_account": service_account or os.getenv("EGRESS_AGENT_SERVICE_ACCOUNT"),
        "min_instances": int(os.getenv("EGRESS_AGENT_MIN_INSTANCES", "0")),
        "max_instances": int(os.getenv("EGRESS_AGENT_MAX_INSTANCES", "1")),
    }


def deploy(
    display_name: str,
    *,
    staging_bucket: str | None = None,
    service_account: str | None = None,
    resource_name: str | None = None,
) -> Any:
    import vertexai

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    location = (
        os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("GOOGLE_CLOUD_REGION")
        or "us-central1"
    )
    vertexai.init(project=project, location=location, staging_bucket=staging_bucket)
    agent_engines = _agent_engine_module()
    package_kwargs = _package_kwargs(display_name, service_account)
    if resource_name:
        return agent_engines.update(resource_name, **package_kwargs)
    return agent_engines.create(**package_kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-name", default="egress-orchestrator")
    parser.add_argument("--staging-bucket", default=os.getenv("EGRESS_AGENT_STAGING_BUCKET"))
    parser.add_argument("--service-account", default=os.getenv("EGRESS_AGENT_SERVICE_ACCOUNT"))
    parser.add_argument(
        "--resource-name",
        default=os.getenv("EGRESS_AGENT_ENGINE_ID"),
        help="Existing Agent Engine resource to update; omitted creates a new resource.",
    )
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
                "env_vars": {
                    key: ("<secret-ref>" if value.__class__.__name__ == "SecretRef" else value)
                    for key, value in _env_vars().items()
                },
                "service_account": args.service_account,
                "resource_name": args.resource_name,
            }
        )
        return
    resource = deploy(
        args.display_name,
        staging_bucket=args.staging_bucket,
        service_account=args.service_account,
        resource_name=args.resource_name,
    )
    print(_resource_name(resource))


if __name__ == "__main__":
    main()
