"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Pause } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScenarioBuilder, type BuilderState } from "@/components/scenario-builder";
import { PriceChart } from "@/components/price-chart";
import { CascadeFlow } from "@/components/cascade-flow";
import { OrderBook } from "@/components/order-book";
import { FillProgress } from "@/components/fill-progress";
import { MetricsPanel } from "@/components/metrics-panel";
import { AnalystPanel } from "@/components/analyst-panel";
import { useRun } from "@/lib/useRun";
import type { Levers, RunSource } from "@/lib/types";

const HTTP_BASE = process.env.NEXT_PUBLIC_GATEWAY_HTTP ?? "http://127.0.0.1:8000";

const DEFAULT_LEVERS: Levers = {
  scenario_text:
    "A crowded mid-cap industrial (ACME) is hit by a surprise rating downgrade to junk. " +
    "Forced sellers hit risk limits, panic and trend sellers pile on, and bargain-hunter " +
    "and market-maker support is thin.",
  position_size: 250_000,
  exit_speed: "measured",
  crowding_mix: {
    forced_seller: 18,
    panic_seller: 22,
    trend_follower: 20,
    bargain_hunter: 15,
    market_maker: 10,
    holder: 15,
  },
};

const SOURCE_LABEL: Record<RunSource, string> = {
  cached: "Cached replay",
  "live-baseline": "Live · deterministic",
  "live-gemini": "Live · Gemini",
};

export default function Page() {
  const { state, start, reset } = useRun();
  const [builder, setBuilder] = useState<BuilderState>({
    mode: "cached",
    gemini: false,
    levers: DEFAULT_LEVERS,
  });
  const [geminiEnabled, setGeminiEnabled] = useState(false);

  // Hydrate defaults + capability from the gateway, if reachable. Falls back silently.
  useEffect(() => {
    fetch(`${HTTP_BASE}/api/scenario/defaults`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setGeminiEnabled(Boolean(d.gemini_enabled));
        setBuilder((b) => ({
          ...b,
          levers: {
            ...b.levers,
            scenario_text: d.scenario_text ?? b.levers.scenario_text,
            position_size: d.position_size ?? b.levers.position_size,
            crowding_mix: scaleMix(d.crowding_mix) ?? b.levers.crowding_mix,
          },
        }));
      })
      .catch(() => {});
  }, []);

  const run = () =>
    start({
      mode: builder.mode,
      gemini: builder.gemini,
      levers: builder.levers,
    });

  const last = state.ticks[state.ticks.length - 1];
  const haltedNow = Boolean(last?.halted);
  const shockCount = useMemo(
    () => state.ticks.filter((t) => t.shock_applied).length,
    [state.ticks],
  );
  const hasRun = state.status !== "idle";
  const progress =
    state.totalTicks > 0 ? Math.min((state.ticks.length / state.totalTicks) * 100, 100) : 0;

  return (
    <div className="mx-auto flex min-h-screen max-w-[1500px] flex-col px-5 py-5 lg:px-7">
      {/* Header */}
      <header className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Logo />
          <div>
            <h1 className="text-[15px] font-semibold tracking-tight text-ink">Egress</h1>
            <p className="text-[11.5px] text-ink-faint">
              Can you actually get out? Simulate the crisis exit before you commit.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          {state.source ? (
            <Badge tone={state.source === "live-gemini" ? "accent" : "neutral"}>
              {SOURCE_LABEL[state.source]}
            </Badge>
          ) : null}
          <StatusPill status={state.status} halted={haltedNow} />
        </div>
      </header>

      {/* Body */}
      <div className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-[358px_1fr]">
        {/* Builder */}
        <Card className="flex flex-col overflow-hidden lg:sticky lg:top-5 lg:h-[calc(100vh-2.5rem)]">
          <CardHeader title="Scenario" hint="describe it, set the levers, run" />
          <ScenarioBuilder
            state={builder}
            setState={setBuilder}
            onRun={run}
            onReset={reset}
            status={state.status}
            geminiEnabled={geminiEnabled}
          />
        </Card>

        {/* Visualisation */}
        <div className="space-y-4">
          {state.error ? (
            <div className="flex items-center gap-2 rounded-[var(--radius)] border border-sell/30 bg-sell/10 px-4 py-3 text-[13px] text-sell">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {state.error}
            </div>
          ) : null}

          {!hasRun ? (
            <EmptyState />
          ) : (
            <>
              <Card className="fadeup overflow-hidden">
                <CardHeader
                  title="Price path"
                  hint="the cascade as the crowd sells into a draining book"
                  right={
                    <div className="flex items-center gap-2">
                      {shockCount > 0 ? (
                        <span className="tnum text-[11px] text-ink-faint">{shockCount} shocks</span>
                      ) : null}
                      {state.metrics?.halt_triggered || haltedNow ? (
                        <Badge tone="halt">
                          <Pause className="h-3 w-3" /> halt
                        </Badge>
                      ) : null}
                    </div>
                  }
                />
                <div className="px-3 pb-2">
                  <PriceChart ticks={state.ticks} config={state.config} totalTicks={state.totalTicks} />
                </div>
                {state.status === "running" ? (
                  <div className="h-0.5 w-full bg-surface-2">
                    <div
                      className="h-full bg-accent transition-all duration-200"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                ) : null}
              </Card>

              <Card className="fadeup overflow-hidden">
                <CardHeader
                  title="Who is selling"
                  hint="forced & panic sellers overwhelm thin support"
                />
                <CascadeFlow ticks={state.ticks} totalTicks={state.totalTicks} />
              </Card>

              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <Card className="fadeup overflow-hidden">
                  <CardHeader title="Order book" hint="liquidity draining" />
                  <OrderBook ticks={state.ticks} />
                </Card>
                <Card className="fadeup overflow-hidden">
                  <CardHeader title="Fill progress" hint="how much of the position got out" />
                  <FillProgress ticks={state.ticks} config={state.config} />
                </Card>
              </div>

              <Card className="fadeup overflow-hidden">
                <CardHeader title="Outcome" hint="the decision aid" />
                <MetricsPanel metrics={state.metrics} />
              </Card>

              <Card className="fadeup overflow-hidden">
                <AnalystPanel
                  analysis={state.analysis}
                  source={state.source}
                  loading={state.status === "running"}
                />
              </Card>
            </>
          )}
        </div>
      </div>

      <footer className="mt-6 flex items-center justify-between text-[11px] text-ink-faint">
        <span>
          Deterministic engine · ADK agents (Gemini via Vertex AI) · the model is one part of the
          system, not the engine.
        </span>
        <span className="tnum">Egress AI</span>
      </footer>
    </div>
  );
}

function scaleMix(mix: Record<string, number> | undefined): Levers["crowding_mix"] | null {
  if (!mix) return null;
  // The gateway returns fractions (sum 1); the sliders work in 0–40 weights.
  const out = {} as Levers["crowding_mix"];
  for (const [k, v] of Object.entries(mix)) {
    (out as Record<string, number>)[k] = Math.round(v * 100);
  }
  return out;
}

function StatusPill({
  status,
  halted,
}: {
  status: string;
  halted: boolean;
}) {
  const map: Record<string, { label: string; tone: string; dot: string }> = {
    idle: { label: "Ready", tone: "text-ink-faint", dot: "bg-ink-faint" },
    connecting: { label: "Connecting", tone: "text-ink-muted", dot: "bg-accent" },
    running: { label: halted ? "Halted" : "Running", tone: "text-ink", dot: halted ? "bg-halt" : "bg-buy" },
    done: { label: "Complete", tone: "text-ink-muted", dot: "bg-ink-muted" },
    error: { label: "Error", tone: "text-sell", dot: "bg-sell" },
  };
  const s = map[status] ?? map.idle;
  return (
    <div className="flex items-center gap-2 rounded-full border border-line bg-surface-2/60 px-3 py-1">
      <span
        className={`h-1.5 w-1.5 rounded-full ${s.dot} ${status === "running" ? "pulse-halt" : ""}`}
      />
      <span className={`text-[11.5px] ${s.tone}`}>{s.label}</span>
    </div>
  );
}

function EmptyState() {
  return (
    <Card className="flex h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <div className="flex h-11 w-11 items-center justify-center rounded-full border border-line bg-surface-2">
        <Activity className="h-5 w-5 text-ink-faint" strokeWidth={1.6} />
      </div>
      <div className="max-w-sm space-y-1.5">
        <p className="text-[14px] text-ink">Run the cascade</p>
        <p className="text-[12.5px] leading-relaxed text-ink-faint">
          Start with the recorded flagship replay, or switch to a live run and vary the position
          size, exit speed, and crowding mix to find the point where the exit closes.
        </p>
      </div>
    </Card>
  );
}

function Logo() {
  return (
    <div className="relative flex h-9 w-9 items-center justify-center rounded-[9px] border border-line-strong bg-surface-2">
      {/* A downward 'exit' glyph — the door the price falls through. */}
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none">
        <path d="M12 4v11" stroke="var(--color-sell)" strokeWidth="1.8" strokeLinecap="round" />
        <path
          d="M7 11l5 5 5-5"
          stroke="var(--color-sell)"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path d="M5 20h14" stroke="var(--color-ink-faint)" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    </div>
  );
}
