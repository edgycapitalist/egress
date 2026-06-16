"""Agent Engine application facade for Egress.

The deployed Agent Engine resource should expose a small JSON boundary that the
gateway can call. This class keeps that boundary stable: it accepts the same
payload the gateway sends in remote mode and returns the same result shape as the
local orchestrator driver.
"""

from __future__ import annotations

import asyncio
from typing import Any

from engine.schema import RunConfig


class EgressAgentEngineApp:
    """Serializable facade for Vertex AI Agent Engine / Reasoning Engine."""

    def query(self, **kwargs: Any) -> dict[str, Any]:
        payload = kwargs.get("input") if isinstance(kwargs.get("input"), dict) else kwargs
        return asyncio.run(self.run(payload))

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        from agents.orchestrator.driver import (
            run_baseline_ensemble,
            run_detailed_live_ensemble,
            run_fast_live_ensemble,
        )

        fallback_raw = payload.get("fallback_config") or {}
        fallback_config = RunConfig.model_validate(fallback_raw)
        use_gemini = bool(payload.get("use_gemini"))
        scenario_prompt = str(payload.get("scenario_prompt") or "")
        gemini_mode = str(payload.get("gemini_mode") or "fast").lower()

        if not use_gemini:
            result = await run_baseline_ensemble(fallback_config)
            result["source"] = "live-baseline"
            return result

        if gemini_mode in {"detailed", "ai_detailed", "full"}:
            result = await run_detailed_live_ensemble(
                scenario_prompt,
                fallback_config=fallback_config,
            )
        else:
            result = await run_fast_live_ensemble(
                scenario_prompt,
                fallback_config=fallback_config,
            )
        result["source"] = (
            "live-baseline" if result.get("fallback_reason") else "live-gemini"
        )
        return result


def build_app() -> EgressAgentEngineApp:
    return EgressAgentEngineApp()
