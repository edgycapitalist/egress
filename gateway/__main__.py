"""Run the Egress gateway locally:  python -m gateway   (or:  make gateway).

Serves the FastAPI app on :8000 with the WebSocket hub at /ws/run. The cached
replay path needs no credentials and no cloud; live runs use the orchestrator.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    # Load .env so a live run can reach Alpha Vantage / Vertex when configured. Done
    # here (the server entry point), not at module import, so importing the app for
    # tests never picks up credentials or makes a live call.
    from agents.common.env import load_dotenv

    load_dotenv()
    uvicorn.run(
        "gateway.app:app",
        host=os.getenv("EGRESS_HOST", "127.0.0.1"),
        port=int(os.getenv("EGRESS_PORT", "8000")),
        reload=bool(os.getenv("EGRESS_RELOAD")),
    )


if __name__ == "__main__":
    main()
