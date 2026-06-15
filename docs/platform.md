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
python -m agents.deploy_agent_engine --display-name egress-orchestrator
```

The command prints the remote resource id. Put that id into
`EGRESS_AGENT_ENGINE_ID` for the gateway deploy. If an HTTP facade is deployed
instead, set `EGRESS_AGENT_ENGINE_URL` and the gateway will call `<url>/run`.

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
