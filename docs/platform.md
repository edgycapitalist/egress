# Phase 7 Platform Wiring

This repo has two execution paths:

- Local/dev: gateway calls the in-process ADK driver; the driver uses the
  in-process deterministic engine, local FunctionTool MCP wrappers, JSONL memory,
  and local corpus retrieval.
- Deployed/GCP: gateway sets `EGRESS_ORCHESTRATOR_MODE=agent_engine` and calls the
  remote Agent Engine facade. The ADK engine bridge sets `EGRESS_ENGINE_SERVICE_URL`
  so deterministic market mechanics run through the engine Cloud Run service.

Key env vars:

- `EGRESS_ORCHESTRATOR_MODE=in_process|agent_engine`
- `EGRESS_AGENT_ENGINE_URL` or `EGRESS_AGENT_ENGINE_ID`
- `EGRESS_ENGINE_SERVICE_URL`
- `EGRESS_DEPLOYED_MODE=true`
- `REDIS_URL`
- `MARKET_DATA_MCP_URL`
- `NEWS_MCP_URL`
- `POSITIONING_MCP_URL`
- `VERTEX_SEARCH_DATASTORE_ID`
- `VERTEX_SEARCH_LOCATION`
- `VERTEX_MEMORY_BANK_ID`
- `DATABASE_URL`

Local smoke commands:

```bash
python -m engine.service
python -m agents.deploy_agent_engine --dry-run
```

Agent Engine deployment, after GCP resources exist:

```bash
python -m agents.deploy_agent_engine \
  --display-name egress-orchestrator \
  --resource-name projects/978090004115/locations/us-central1/reasoningEngines/3984250257792827392
```

The command updates the existing resource when `--resource-name` is provided; if
omitted, it creates a new Agent Engine resource. It prints the remote resource id.
Put that id into `EGRESS_AGENT_ENGINE_ID` for the gateway deploy. If an HTTP
facade is deployed instead, set `EGRESS_AGENT_ENGINE_URL` and the gateway will
call `<url>/run`.

The remote orchestrator payload is:

```json
{
  "scenario": {},
  "scenario_prompt": "plain language prompt",
  "use_gemini": true,
  "gemini_mode": "fast",
  "fallback_config": {}
}
```

The response should match the local driver result:

```json
{
  "source": "live-gemini",
  "ensemble_result": {},
  "representative_replay_ref": "runs/example.ndjson",
  "analysis": "..."
}
```

If the remote result includes `replay_ndjson` or `replay_records`, the gateway
materializes it into `runs/*-remote.ndjson` before streaming it to the frontend.

## CI/CD shape

Pushes to `main` are owned by `.github/workflows/deploy.yml`. The workflow uses
Workload Identity Federation as `github-deployer` and updates the existing Cloud
Run services:

- `egress-engine`
- `egress-market-data-mcp`
- `egress-news-mcp`
- `egress-positioning-mcp`
- `egress-gateway`
- `egress-frontend`

The frontend service name is never changed, so the public judging URL remains:

```text
https://egress-frontend-978090004115.us-central1.run.app
```

Service images are built by separate Cloud Build configs:

- `cloudbuild.gateway.yaml`
- `cloudbuild.engine.yaml`
- `cloudbuild.market-data-mcp.yaml`
- `cloudbuild.news-mcp.yaml`
- `cloudbuild.positioning-mcp.yaml`
- `web/cloudbuild.yaml`

The deploy order is backend services first, then Agent Engine, then gateway, then
frontend. This keeps the gateway's environment pointing at already-created
engine/MCP URLs and the Agent Engine resource id. The workflow runs
`scripts/deployed_smoke.py` at the end to check the public frontend, gateway
health, cached replay, WebSocket cached/live runs, authenticated engine health,
and MCP endpoint reachability.

`.github/workflows/ci.yml` runs the offline Python suite, ruff, the
discrimination/holdout/latency eval targets, the Next.js build, and every Docker
target. A PR that breaks one service image should fail before it reaches deploy.

## Platform bootstrap boundary

Normal app deploys should not create long-lived or costly resources. Bootstrap is
a separate manual/platform operation and owns:

- required APIs
- service accounts and IAM roles
- Artifact Registry
- Secret Manager secret metadata
- Cloud SQL Postgres and `scripts/db/init.sql`
- Memorystore Redis and Serverless VPC connector
- Vertex AI Search datastore and corpus import
- Agent Engine staging bucket
- optional Memory Bank resource when its API surface is verified

The current deployed project uses Cloud SQL/pgvector as the deployed memory
fallback. Vertex AI Memory Bank remains a platform follow-up until its creation
API is verified.

## Rollback

If a main deployment breaks the demo path:

1. Keep cached replay working first. It is the fallback judges can use even if
   Gemini or Agent Engine is unhealthy.
2. Shift Cloud Run traffic for `egress-gateway` and/or `egress-frontend` back to
   the previous known-good revision:

   ```bash
   gcloud run services update-traffic egress-gateway \
     --region us-central1 \
     --to-revisions PREVIOUS_REVISION=100
   ```

3. If remote orchestration is the problem, put the gateway into safe mode:

   ```bash
   gcloud run services update egress-gateway \
     --region us-central1 \
     --set-env-vars EGRESS_ORCHESTRATOR_MODE=in_process,EGRESS_LIVE_GEMINI=false
   ```

4. If the Agent Engine update is the problem but gateway fallback is healthy,
   keep the new gateway and restore `EGRESS_AGENT_ENGINE_ID` to the previous
   resource id.

Rollback criteria: frontend load failure, cached replay failure, gateway
WebSocket failure, live baseline failure, Gemini/Agent Engine failures that do
not fall back cleanly, unacceptable live-run latency, or service-to-service IAM
blocking engine/MCP access.
