"use client";

import { useCallback, useRef, useState } from "react";
import type {
  EnsembleResult,
  Frame,
  Levers,
  Metrics,
  PlatformMode,
  ReplayPayload,
  RunConfig,
  RunSource,
  TickEvent,
} from "./types";

const WS_URL =
  process.env.NEXT_PUBLIC_GATEWAY_WS ?? "ws://127.0.0.1:8000/ws/run";
const HTTP_BASE = process.env.NEXT_PUBLIC_GATEWAY_HTTP ?? "http://127.0.0.1:8000";

export type RunStatus = "idle" | "connecting" | "running" | "done" | "error";

export interface RunState {
  status: RunStatus;
  source: RunSource | null;
  platform: PlatformMode | null;
  config: RunConfig | null;
  ticks: TickEvent[];
  totalTicks: number;
  metrics: Metrics | null;
  ensemble: EnsembleResult | null;
  analysis: string | null;
  message: string | null;
  error: string | null;
}

const EMPTY: RunState = {
  status: "idle",
  source: null,
  platform: null,
  config: null,
  ticks: [],
  totalTicks: 0,
  metrics: null,
  ensemble: null,
  analysis: null,
  message: null,
  error: null,
};

export interface StartArgs {
  mode: "cached" | "live";
  gemini?: boolean;
  levers?: Levers;
  paceMs?: number;
}

/**
 * Opens a WebSocket to the gateway, drives one run, and accumulates the streamed
 * frames into render-ready state. Ticks arrive batched; we append them so the
 * price path and order book animate as the cascade unfolds.
 */
export function useRun() {
  const [state, setState] = useState<RunState>(EMPTY);
  const wsRef = useRef<WebSocket | null>(null);

  const stop = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  const start = useCallback((args: StartArgs) => {
    wsRef.current?.close();
    setState({ ...EMPTY, status: "connecting" });

    let ws: WebSocket;
    try {
      ws = new WebSocket(WS_URL);
    } catch {
      setState({ ...EMPTY, status: "error", error: "Could not open the gateway socket." });
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          mode: args.mode,
          gemini: args.gemini ?? false,
          scenario: args.levers ?? {},
          pace_ms: args.paceMs ?? undefined,
        }),
      );
      setState((s) => ({ ...s, status: "running" }));
    };

    ws.onmessage = (ev) => {
      let frame: Frame;
      try {
        frame = JSON.parse(ev.data as string) as Frame;
      } catch {
        return;
      }
      setState((s) => reduce(s, frame));
      // Close from our side once the run is done, so the gateway never tears the
      // connection down before the tail is delivered.
      if (frame.type === "done") ws.close();
    };

    ws.onerror = () => {
      // A drop after the outcome arrived is just an unclean close, not a failure -
      // the cascade and metrics are already in hand, so treat it as complete.
      setState((s) =>
        s.status === "done" || s.metrics
          ? { ...s, status: "done" }
          : { ...s, status: "error", error: "Lost the connection to the gateway." },
      );
    };

    ws.onclose = () => {
      setState((s) => (s.status === "running" || s.status === "connecting"
        ? { ...s, status: s.metrics ? "done" : "error", error: s.metrics ? s.error : (s.error ?? "The run ended early.") }
        : s));
    };
  }, []);

  const reset = useCallback(() => {
    stop();
    setState(EMPTY);
  }, [stop]);

  const loadReplay = useCallback(async (ref: string) => {
    if (!ref) return;
    setState((s) => ({ ...s, status: "running", message: "Loading selected case…" }));
    try {
      const res = await fetch(`${HTTP_BASE}/api/replay?ref=${encodeURIComponent(ref)}`);
      if (!res.ok) throw new Error("replay_load_failed");
      const payload = (await res.json()) as ReplayPayload;
      setState((s) => ({
        ...s,
        status: "done",
        config: payload.config,
        ticks: payload.ticks,
        totalTicks: payload.total_ticks,
        metrics: payload.metrics,
        message: null,
        error: null,
      }));
    } catch {
      setState((s) => ({
        ...s,
        status: "error",
        error: "Could not load the selected replay path.",
      }));
    }
  }, []);

  return { state, start, stop, reset, loadReplay };
}

function reduce(s: RunState, frame: Frame): RunState {
  switch (frame.type) {
    case "meta":
      return {
        ...s,
        status: "running",
        source: frame.source,
        platform: frame.platform ?? null,
        config: frame.config,
        totalTicks: frame.total_ticks,
        ticks: [],
        metrics: null,
        ensemble: null,
        analysis: null,
        error: null,
      };
    case "ticks":
      return { ...s, ticks: [...s.ticks, ...frame.ticks] };
    case "metrics":
      return { ...s, metrics: frame.metrics };
    case "ensemble":
      return { ...s, ensemble: frame.ensemble };
    case "analysis":
      return { ...s, analysis: frame.analysis };
    case "status":
      return { ...s, message: frame.message };
    case "error":
      return { ...s, status: "error", error: frame.message };
    case "done":
      return { ...s, status: "done", message: null };
    default:
      return s;
  }
}
