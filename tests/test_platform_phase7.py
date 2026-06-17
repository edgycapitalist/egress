from __future__ import annotations

from pathlib import Path

import pytest
from agents.cards import discovery_payload
from engine.scenarios import flagship_scenario
from memory import (
    JsonlMemoryStore,
    VertexMemoryBankStore,
    build_memory_store,
    memory_context_for,
    write_calibration_adjustment,
    write_run_outcome,
)
from rag import LocalCorpusRetriever, format_snippets, retrieve_context


def test_agent_discovery_payload_lists_core_agents() -> None:
    payload = discovery_payload()
    names = {card["name"] for card in payload["cards"]}
    assert payload["protocol"] == "a2a-discovery-compatible"
    assert {"EgressRun", "ScenarioAuthor", "Analyst", "CalibrationCritic"} <= names
    assert "ForcedSellerMood" in names


def test_jsonl_memory_store_reads_recent_scenarios_and_calibration(tmp_path: Path) -> None:
    store = JsonlMemoryStore(tmp_path / "memory.jsonl")
    scenario = flagship_scenario().model_dump()
    metrics = {"run_id": scenario["run_id"], "fill_rate": 0.4}

    write_run_outcome(scenario, metrics, analysis="prior run", store=store)
    write_calibration_adjustment(
        scenario,
        {"episode_id": "cvna_2022", "verdict": "too_calm"},
        {"forced_seller": {"aggressiveness": 0.1}},
        store=store,
    )

    context = memory_context_for(scenario, store=store)
    assert context["backend"] == "jsonl"
    assert context["recent_scenarios"][0]["analysis"] == "prior run"
    assert context["calibration_adjustments"][0]["episode_id"] == "cvna_2022"


@pytest.mark.asyncio
async def test_vertex_memory_bank_store_uses_adk_memory_service(monkeypatch) -> None:
    from google.adk.memory import vertex_ai_memory_bank_service
    from google.adk.memory.base_memory_service import SearchMemoryResponse

    class FakeVertexMemoryService:
        memories = []
        init_args = {}

        def __init__(self, project=None, location=None, agent_engine_id=None):
            self.init_args.update(
                {
                    "project": project,
                    "location": location,
                    "agent_engine_id": agent_engine_id,
                }
            )

        async def add_memory(self, *, app_name, user_id, memories, custom_metadata=None):
            self.memories.extend(memories)

        async def search_memory(self, *, app_name, user_id, query):
            return SearchMemoryResponse(memories=list(self.memories))

    monkeypatch.setattr(
        vertex_ai_memory_bank_service,
        "VertexAiMemoryBankService",
        FakeVertexMemoryService,
    )

    store = VertexMemoryBankStore(
        agent_engine_id="projects/123/locations/us-central1/reasoningEngines/456",
        project="test-project",
        location="us-central1",
    )
    scenario = flagship_scenario().model_dump()
    metrics = {"run_id": scenario["run_id"], "fill_rate": 0.4}

    write_run_outcome(scenario, metrics, analysis="memory bank run", store=store)
    context = memory_context_for(scenario, store=store)

    assert store.health()["backend"] == "vertex_ai_memory_bank"
    assert FakeVertexMemoryService.init_args == {
        "project": "test-project",
        "location": "us-central1",
        "agent_engine_id": "456",
    }
    assert context["backend"] == "vertex_ai_memory_bank"
    assert context["recent_scenarios"][0]["analysis"] == "memory bank run"


def test_build_memory_store_prefers_agent_engine_memory(monkeypatch) -> None:
    from google.adk.memory import vertex_ai_memory_bank_service

    class FakeVertexMemoryService:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(
        vertex_ai_memory_bank_service,
        "VertexAiMemoryBankService",
        FakeVertexMemoryService,
    )
    monkeypatch.setenv("EGRESS_AGENT_ENGINE_ID", "reasoningEngines/789")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-x")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    store = build_memory_store()

    assert isinstance(store, VertexMemoryBankStore)
    assert store.health()["agent_engine_id"] == "789"


def test_local_rag_retrieves_source_labelled_snippets() -> None:
    retriever = LocalCorpusRetriever("docs/corpus")
    snippets = retriever.retrieve("CVNA crowded exit microstructure liquidity", limit=3)
    assert snippets
    rendered = format_snippets(snippets)
    assert "docs/corpus" in rendered
    assert "Retrieval backend" in rendered


def test_retrieve_context_falls_back_to_local_when_vertex_configured(monkeypatch) -> None:
    monkeypatch.setenv("VERTEX_SEARCH_DATASTORE_ID", "test-datastore")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    context = retrieve_context("peer crowding low base high ensemble")
    assert "fallback_local" in context["backend"]
    assert context["snippets"]


def test_mcp_tools_use_local_by_default_and_url_when_configured(monkeypatch) -> None:
    from mcp.market_data import tools as market_tools

    monkeypatch.delenv("MARKET_DATA_MCP_URL", raising=False)
    assert market_tools.market_data_tools() == market_tools.MARKET_DATA_TOOLS

    monkeypatch.setenv("MARKET_DATA_MCP_URL", "https://mcp.example")
    monkeypatch.setattr(
        "mcp.client.mcp_toolset_from_url",
        lambda url, *, name: [{"url": url, "name": name}],
    )
    assert market_tools.market_data_tools() == [
        {"url": "https://mcp.example", "name": "egress-market-data"}
    ]


@pytest.mark.asyncio
async def test_gateway_agent_engine_mode_falls_back(monkeypatch) -> None:
    import gateway.app as gateway_app

    async def failing_remote(*args, **kwargs):
        raise RuntimeError("remote down")

    async def fake_baseline(config):
        return {
            "error": None,
            "representative_replay_ref": "docs/replays/flagship-42.ndjson",
            "analysis": "fallback narrative",
            "ensemble_result": {"type": "ensemble", "run_id": "fallback", "cases": [], "bands": {}},
        }

    monkeypatch.setenv("EGRESS_ORCHESTRATOR_MODE", "agent_engine")
    monkeypatch.setattr("agents.orchestrator.remote.run_remote_orchestrator", failing_remote)
    monkeypatch.setattr("agents.orchestrator.driver.run_baseline_ensemble", fake_baseline)

    replay_ref, source, analysis, ensemble, platform = await gateway_app._run_live(
        {"symbol": "CVNA", "scenario_text": "severe crowded exit"},
        use_gemini=True,
    )
    assert replay_ref.endswith("flagship-42.ndjson")
    assert source == "live-baseline"
    assert analysis == "fallback narrative"
    assert ensemble is not None
    assert platform == "agent_engine_fallback"
