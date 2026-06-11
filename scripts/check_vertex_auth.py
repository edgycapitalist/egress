#!/usr/bin/env python3
"""Confirm Gemini works through Vertex AI before spending the build on it.

This is the one *real* Gemini call the Phase-2 plan calls for. It is intentionally
tiny — a single short generation — so it proves authentication and quota without
burning credit. Run it once you have Application Default Credentials and a project::

    gcloud auth application-default login
    cp .env.example .env        # then set GOOGLE_CLOUD_PROJECT (+ LOCATION)
    python scripts/check_vertex_auth.py

What it checks, in order:

1. The configuration is valid for Vertex (``GOOGLE_GENAI_USE_VERTEXAI=true``, a
   project and location are set) and there is **no** ``GOOGLE_API_KEY`` — Egress
   uses Vertex AI only, never AI Studio.
2. A direct ``google-genai`` call to the fast model returns text (auth + quota).
3. Optionally (``--agent``), the same call routed through an ADK ``LlmAgent`` +
   ``Runner``, proving the agent path the orchestrator uses needs no extra wiring.

Exit code is 0 on success, 1 on any failure, with a clear message — never a raw
SDK stack trace.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as `python scripts/check_vertex_auth.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.common.env import (  # noqa: E402
    VertexAuthError,
    assert_vertex_config,
    fast_model,
    load_dotenv,
)

PROMPT = "Reply with exactly: Egress Vertex auth OK"


def _direct_call(project: str, location: str, model: str) -> str:
    from google import genai

    client = genai.Client(vertexai=True, project=project, location=location)
    response = client.models.generate_content(model=model, contents=PROMPT)
    return (response.text or "").strip()


async def _agent_call(model: str) -> str:
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    agent = LlmAgent(name="AuthProbe", model=model, instruction=PROMPT)
    runner = InMemoryRunner(agent=agent, app_name="egress-auth")
    session = await runner.session_service.create_session(
        app_name="egress-auth", user_id="local"
    )
    text = ""
    async for event in runner.run_async(
        user_id="local",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=PROMPT)]),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None):
                    text += part.text
    return text.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        action="store_true",
        help="also route a call through an ADK LlmAgent + Runner",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        cfg = assert_vertex_config()
    except VertexAuthError as exc:
        print(f"✗ Vertex configuration invalid:\n  {exc}", file=sys.stderr)
        return 1

    model = fast_model()
    print("Vertex AI configuration:")
    print(f"  project   = {cfg['project']}")
    print(f"  location  = {cfg['location']}")
    print(f"  model     = {model}")
    print("  use_vertexai = true   (AI Studio API key absent, as required)")
    print()

    try:
        print("→ Direct google-genai call ...")
        reply = _direct_call(cfg["project"], cfg["location"], model)
        print(f"  ✓ Gemini replied: {reply!r}")

        if args.agent:
            print("→ ADK LlmAgent + Runner call ...")
            agent_reply = asyncio.run(_agent_call(model))
            print(f"  ✓ Agent replied: {agent_reply!r}")
    except Exception as exc:  # noqa: BLE001 — surface any auth/quota/SDK error clearly
        print(f"\n✗ Gemini call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "  Check: ADC is set (gcloud auth application-default login), the project "
            "has Vertex AI enabled, and the model is available in the location.",
            file=sys.stderr,
        )
        return 1

    print("\n✓ Auth and quota confirmed. The agents will make real Gemini calls "
          "with no further wiring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
