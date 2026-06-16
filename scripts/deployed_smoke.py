"""Post-deploy smoke checks for the hosted Egress platform.

The checks are intentionally small: they prove the public frontend still loads,
the gateway can serve cached replay and WebSocket runs, the authenticated engine
responds, and deployed MCP endpoints are reachable. They do not replace the
offline eval suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _read_json(url: str, *, token: str | None = None, timeout: int = 30) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_status(url: str, *, token: str | None = None, timeout: int = 30) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def _identity_token(audience: str) -> str:
    completed = subprocess.run(
        ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


async def _ws_smoke(ws_url: str, payload: dict[str, Any], *, require_analysis: bool) -> None:
    import websockets

    seen: set[str] = set()
    async with websockets.connect(ws_url, open_timeout=30, close_timeout=10) as ws:
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=90)
            frame = json.loads(raw)
            frame_type = frame.get("type")
            seen.add(frame_type)
            if frame_type == "error":
                raise RuntimeError(frame.get("message", "websocket smoke returned error"))
            if frame_type == "done":
                await ws.close()
                break
    required = {"meta", "ticks", "metrics", "ensemble", "done"}
    if require_analysis:
        required.add("analysis")
    missing = required - seen
    if missing:
        raise RuntimeError(f"websocket smoke missing frames: {sorted(missing)}")


def _http_smokes(args: argparse.Namespace) -> None:
    frontend_status = _read_status(args.frontend_url)
    if frontend_status != 200:
        raise RuntimeError(f"frontend returned HTTP {frontend_status}")

    health = _read_json(f"{args.gateway_url.rstrip('/')}/api/health")
    if health.get("status") != "ok":
        raise RuntimeError(f"gateway unhealthy: {health}")

    replay = _read_json(
        f"{args.gateway_url.rstrip('/')}/api/replay?ref=docs/replays/flagship-42.ndjson"
    )
    if not replay.get("ticks") or not replay.get("metrics"):
        raise RuntimeError("cached replay did not include ticks and metrics")

    engine_token = _identity_token(args.engine_url.rstrip("/"))
    engine_health = _read_json(
        f"{args.engine_url.rstrip('/')}/health",
        token=engine_token,
    )
    if engine_health.get("status") not in {"ok", "degraded"}:
        raise RuntimeError(f"engine health failed: {engine_health}")

    for label, url in {
        "market_data_mcp": args.market_data_mcp_url,
        "news_mcp": args.news_mcp_url,
        "positioning_mcp": args.positioning_mcp_url,
    }.items():
        if not url:
            continue
        status = _read_status(url)
        if status >= 500:
            raise RuntimeError(f"{label} returned HTTP {status}")


async def _run(args: argparse.Namespace) -> None:
    _http_smokes(args)
    ws_url = args.gateway_url.rstrip("/").replace("https://", "wss://") + "/ws/run"
    await _ws_smoke(
        ws_url,
        {"mode": "cached", "pace_ms": 0, "batch_size": 100},
        require_analysis=False,
    )
    await _ws_smoke(
        ws_url,
        {
            "mode": "live",
            "gemini": False,
            "pace_ms": 0,
            "batch_size": 100,
            "scenario": {"symbol": "CVNA", "max_ticks": 20},
        },
        require_analysis=False,
    )
    await _ws_smoke(
        ws_url,
        {
            "mode": "live",
            "gemini": True,
            "gemini_mode": "fast",
            "pace_ms": 0,
            "batch_size": 100,
            "scenario": {"symbol": "CVNA", "max_ticks": 20},
        },
        require_analysis=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-url", required=True)
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--engine-url", required=True)
    parser.add_argument("--market-data-mcp-url")
    parser.add_argument("--news-mcp-url")
    parser.add_argument("--positioning-mcp-url")
    parser.add_argument("--agent-engine-id")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except (HTTPError, URLError, TimeoutError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("deployed smoke checks passed")


if __name__ == "__main__":
    main()
