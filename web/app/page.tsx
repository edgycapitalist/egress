"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Info, Pause } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScenarioBuilder, type BuilderState } from "@/components/scenario-builder";
import { PriceChart } from "@/components/price-chart";
import { LiveInteractions } from "@/components/live-interactions";
import { SourcedInputs } from "@/components/sourced-inputs";
import { FillProgress } from "@/components/fill-progress";
import { MetricsPanel } from "@/components/metrics-panel";
import { AnalystPanel } from "@/components/analyst-panel";
import { useRun } from "@/lib/useRun";
import type { Levers, Metrics, RunConfig, RunSource, SourcedInput } from "@/lib/types";
import { fmtPct } from "@/lib/utils";

const HTTP_BASE = process.env.NEXT_PUBLIC_GATEWAY_HTTP ?? "http://127.0.0.1:8000";

const DEFAULT_LEVERS: Levers = {
  scenario_text:
    "A heavily crowded name is hit by a surprise liquidity and bankruptcy scare. " +
    "Forced sellers hit margin calls, panic and trend sellers pile on, and " +
    "bargain-hunter and market-maker support is thin.",
  symbol: "CVNA",
  position_size: 250_000,
  population_size: 5_000,
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
  cached: "Saved example",
  "live-baseline": "Live (no AI)",
  "live-gemini": "Live (Gemini)",
};

export default function Page() {
  const { state, start, reset } = useRun();
  const [builder, setBuilder] = useState<BuilderState>({
    mode: "cached",
    gemini: false,
    levers: DEFAULT_LEVERS,
  });
  const [geminiEnabled, setGeminiEnabled] = useState(false);
  const [avEnabled, setAvEnabled] = useState(false);
  const [sourced, setSourced] = useState<SourcedInput | null>(null);
  const [sourcedLoading, setSourcedLoading] = useState(false);

  // Hydrate defaults + capability from the gateway, if reachable. Falls back silently.
  useEffect(() => {
    fetch(`${HTTP_BASE}/api/scenario/defaults`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setGeminiEnabled(Boolean(d.gemini_enabled));
        setAvEnabled(Boolean(d.av_enabled));
        setBuilder((b) => ({
          ...b,
          levers: {
            ...b.levers,
            scenario_text: d.scenario_text ?? b.levers.scenario_text,
            position_size: d.position_size ?? b.levers.position_size,
            population_size: d.population_size ?? b.levers.population_size,
            crowding_mix: scaleMix(d.crowding_mix) ?? b.levers.crowding_mix,
          },
        }));
      })
      .catch(() => {});
  }, []);

  // Fetch the sourced inputs for the instrument the run resolved to, matching the
  // run's mode: a live run gets the real Alpha Vantage feed, cached gets the
  // recorded/curated reference - so the panel always agrees with the simulation.
  const symbol = state.config?.instrument.symbol;
  const live = state.source !== null && state.source !== "cached";
  useEffect(() => {
    if (!symbol) {
      setSourced(null);
      return;
    }
    setSourcedLoading(true);
    fetch(`${HTTP_BASE}/api/instrument?symbol=${encodeURIComponent(symbol)}&live=${live ? 1 : 0}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setSourced(d))
      .catch(() => setSourced(null))
      .finally(() => setSourcedLoading(false));
  }, [symbol, live]);

  const run = () =>
    start({ mode: builder.mode, gemini: builder.gemini, levers: builder.levers });

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
      <header className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Logo />
          <div>
            <h1 className="text-[15px] font-semibold tracking-tight text-ink">Egress</h1>
            <p className="text-[11.5px] text-ink-faint">Crisis-exit liquidity simulator</p>
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

      {/* What this is - for someone arriving cold. */}
      <div className="mb-4 rounded-[var(--radius)] border border-line bg-surface/60 px-4 py-3">
        <p className="max-w-3xl text-[13.5px] leading-relaxed text-ink">
          Say you hold a large position and a crisis hits. Could you actually
          <span className="text-ink"> sell it before the exit closes</span>? Egress answers that.
          It runs a simulated market of thousands of traders, then shows how far the price falls
          while you sell, how much of your position you get out, and how much stays stuck.
        </p>
        <p className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-ink-faint">
          <Info className="h-3 w-3 shrink-0" />
          The market itself runs on fixed, repeatable code. On a live Gemini run, a few AI agents
          (Google Vertex AI) set how each kind of investor behaves and write the explanation.
          Cached and baseline runs use simple stand-ins instead, with no AI.
        </p>
      </div>

      {/* Body */}
      <div className="grid flex-1 grid-cols-1 gap-3 lg:grid-cols-[358px_1fr]">
        {/* Builder */}
        <Card className="flex flex-col overflow-hidden lg:sticky lg:top-5 lg:max-h-[calc(100vh-2.5rem)]">
          <CardHeader title="Scenario" hint="describe it, set the levers, run" />
          <ScenarioBuilder
            state={builder}
            setState={setBuilder}
            onRun={run}
            onReset={reset}
            status={state.status}
            geminiEnabled={geminiEnabled}
            avEnabled={avEnabled}
          />
        </Card>

        {/* Visualisation */}
        <div className="space-y-3">
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
                  caption="The price as the crowd sells. A steep fall, or a halt marker, means the exit is closing while you are still trying to sell."
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
                  title="Inside the market"
                  caption="What is happening under the price. The top shows the buyers' orders (the support you sell into) drying up; the bottom shows which kinds of sellers are hitting the market each step. When sellers overwhelm the buyers, the book empties and trading stops."
                />
                <LiveInteractions ticks={state.ticks} totalTicks={state.totalTicks} />
              </Card>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Card className="fadeup overflow-hidden">
                  <CardHeader
                    title="Market data used"
                    caption={
                      live
                        ? "The real, current numbers this run used (from Alpha Vantage): recent price, average daily volume, and volatility. The free data feed covers about the last 100 trading days, so a live run reflects today's conditions, not the original crisis."
                        : "The recorded reference numbers for this saved example: a representative price, volume, and volatility for that episode. These are not a live quote."
                    }
                  />
                  <SourcedInputs data={sourced} loading={sourcedLoading} />
                </Card>
                <Card className="fadeup overflow-hidden">
                  <CardHeader
                    title="Fill progress"
                    caption="How much of your position actually sold versus how much is left stuck."
                  />
                  <FillProgress ticks={state.ticks} config={state.config} />
                </Card>
              </div>

              <Card className="fadeup overflow-hidden">
                <CardHeader
                  title="Outcome"
                  caption="The bottom line: could you get out, how far did the price move against you, and how much was left unsold."
                />
                <Verdict metrics={state.metrics} source={state.source} config={state.config} />
                <MetricsPanel metrics={state.metrics} />
              </Card>

              <Card className="fadeup overflow-hidden">
                <CardHeader
                  title="Explanation"
                  caption="A plain-language summary of why the exit held or closed, written only from this run's own numbers."
                />
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

      <footer className="mt-5 flex items-center justify-between text-[11px] text-ink-faint">
        <span>Market data via Alpha Vantage. A simulation, not investment advice.</span>
        <span className="tnum">Egress AI</span>
      </footer>
    </div>
  );
}

function Verdict({
  metrics,
  source,
  config,
}: {
  metrics: Metrics | null;
  source: RunSource | null;
  config: RunConfig | null;
}) {
  if (!metrics) return null;
  const closed = metrics.fill_rate < 0.999;
  const live = source !== null && source !== "cached";
  const ci = config?.crisis_intensity;
  return (
    <div className="border-b border-line px-4 pb-3.5 pt-1">
      <p className="text-[14.5px] leading-relaxed text-ink">
        {closed ? (
          <>
            You managed to sell only{" "}
            <span className="tnum font-semibold text-sell">{fmtPct(metrics.fill_rate, 0)}</span> of
            your position before the exit closed.{" "}
            <span className="tnum font-semibold text-sell">{fmtPct(metrics.pct_stuck, 0)}</span> was
            left stuck, unsellable.
          </>
        ) : (
          <>
            You sold your whole position (
            <span className="tnum font-semibold text-buy">{fmtPct(metrics.fill_rate, 0)}</span>).
            Nothing was left stuck. The exit stayed open.
          </>
        )}
      </p>
      {live && ci != null ? (
        <p className="mt-1.5 text-[12px] text-ink-faint">
          Simulated crisis severity:{" "}
          <span className="text-ink-muted">{crisisLabel(ci)}</span> (intensity {ci.toFixed(2)}).
          Set by your stress description and the ticker&apos;s latest news.
        </p>
      ) : null}
    </div>
  );
}

// Plain-language band for the engine's crisis intensity (0.3 mild to 1.6 extreme).
function crisisLabel(ci: number): string {
  if (ci < 0.5) return "mild";
  if (ci < 0.85) return "moderate";
  if (ci < 1.2) return "severe";
  return "extreme";
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

function StatusPill({ status, halted }: { status: string; halted: boolean }) {
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
        <p className="text-[14px] text-ink">Run your first simulation</p>
        <p className="text-[12.5px] leading-relaxed text-ink-faint">
          Press Run to replay the saved Carvana 2022 example, or switch to Live to test your own
          ticker, position size, and crisis. Then watch the price, the order book, and how much of
          the position you actually manage to sell.
        </p>
      </div>
    </Card>
  );
}

function Logo() {
  // An exit door with an arrow leaving through it - the position trying to get out.
  return (
    <div className="relative flex h-9 w-9 items-center justify-center rounded-[9px] border border-line-strong bg-surface-2">
      <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none">
        <path
          d="M13 4H6v16h7"
          stroke="var(--color-ink-faint)"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M11 12h9m-4-4 4 4-4 4"
          stroke="var(--color-sell)"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}
