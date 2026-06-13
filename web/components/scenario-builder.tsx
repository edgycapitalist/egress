"use client";

import { useMemo } from "react";
import { Play, RotateCcw, Database, Radio } from "lucide-react";
import {
  INVESTOR_TYPES,
  INVESTOR_LABELS,
  investorColor,
  TICKER_PRESETS,
  tickerLabel,
  type InvestorType,
  type Levers,
} from "@/lib/types";
import type { RunStatus } from "@/lib/useRun";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn, fmtInt, fmtPct } from "@/lib/utils";

const EXIT_SPEEDS: { key: string; label: string }[] = [
  { key: "patient", label: "Patient" },
  { key: "measured", label: "Measured" },
  { key: "urgent", label: "Urgent" },
  { key: "fire_sale", label: "Fire sale" },
];

function Segmented<T extends string>({
  options,
  value,
  onChange,
  disabled,
}: {
  options: { key: T; label: string; icon?: React.ReactNode }[];
  value: T;
  onChange: (v: T) => void;
  disabled?: boolean;
}) {
  return (
    <div
      className={cn(
        "grid gap-1 rounded-[9px] border border-line bg-surface-2/60 p-1",
        disabled && "opacity-50",
      )}
      style={{ gridTemplateColumns: `repeat(${options.length}, 1fr)` }}
    >
      {options.map((o) => (
        <button
          key={o.key}
          disabled={disabled}
          onClick={() => onChange(o.key)}
          className={cn(
            "flex items-center justify-center gap-1.5 rounded-[6px] px-2 py-1.5 text-[12px] font-medium transition-colors",
            value === o.key ? "bg-ink text-bg" : "text-ink-muted hover:text-ink",
          )}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export interface BuilderState {
  mode: "cached" | "live";
  gemini: boolean;
  levers: Levers;
}

export function ScenarioBuilder({
  state,
  setState,
  onRun,
  onReset,
  status,
  geminiEnabled,
}: {
  state: BuilderState;
  setState: (s: BuilderState) => void;
  onRun: () => void;
  onReset: () => void;
  status: RunStatus;
  geminiEnabled: boolean;
}) {
  const { mode, levers } = state;
  const busy = status === "running" || status === "connecting";
  const cached = mode === "cached";
  // A curated ticker drives the instrument and sizes the position at a fixed %ADV on
  // the gateway, so the manual position slider doesn't apply while one is selected.
  const tickerActive = Boolean(levers.symbol);

  const mixTotal = useMemo(
    () => INVESTOR_TYPES.reduce((s, t) => s + (levers.crowding_mix[t] ?? 0), 0) || 1,
    [levers.crowding_mix],
  );

  const set = (patch: Partial<Levers>) => setState({ ...state, levers: { ...levers, ...patch } });
  const setMix = (t: InvestorType, v: number) =>
    set({ crowding_mix: { ...levers.crowding_mix, [t]: v } });

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 pb-4 pt-1">
        {/* Mode */}
        <div className="space-y-2">
          <Label>Run source</Label>
          <Segmented
            options={[
              { key: "cached", label: "Cached", icon: <Database className="h-3.5 w-3.5" /> },
              { key: "live", label: "Live", icon: <Radio className="h-3.5 w-3.5" /> },
            ]}
            value={mode}
            onChange={(m) => setState({ ...state, mode: m })}
            disabled={busy}
          />
          {mode === "live" && geminiEnabled ? (
            <label className="flex cursor-pointer items-center gap-2 pt-0.5 text-[12px] text-ink-muted">
              <input
                type="checkbox"
                checked={state.gemini}
                onChange={(e) => setState({ ...state, gemini: e.target.checked })}
                className="accent-[var(--color-accent)]"
              />
              Use real Gemini (Vertex AI) — spends credits
            </label>
          ) : null}
          <p className="text-[11px] leading-relaxed text-ink-faint">
            {cached
              ? "Replays a recorded historical-reference episode — fully offline, identical every time."
              : state.gemini
                ? "Runs the agents for real: Gemini sets each archetype's stance, the engine runs the market on the instrument's current real data."
                : "Runs the engine now on the instrument's current real data (Alpha Vantage). This is today's conditions — it does not reproduce the historical crisis."}
          </p>
        </div>

        <Divider />

        {/* Plain-language scenario */}
        <div className="space-y-2">
          <Label>Position & stress event</Label>
          <textarea
            value={levers.scenario_text}
            onChange={(e) => set({ scenario_text: e.target.value })}
            disabled={cached || busy}
            rows={4}
            className={cn(
              "w-full resize-none rounded-[8px] border border-line bg-surface-2/60 px-3 py-2.5 text-[12.5px] leading-relaxed text-ink placeholder:text-ink-faint focus:border-line-strong focus:outline-none",
              (cached || busy) && "opacity-55",
            )}
            placeholder="Describe the position you hold and the crisis you want to stress-test…"
          />
        </div>

        {/* Instrument (curated ticker presets; works in cached and live) */}
        <div className="space-y-2">
          <Label>Instrument</Label>
          <select
            value={levers.symbol ?? ""}
            onChange={(e) => set({ symbol: e.target.value })}
            disabled={busy}
            className={cn(
              "w-full rounded-[8px] border border-line bg-surface-2/60 px-3 py-2 text-[12.5px] text-ink focus:border-line-strong focus:outline-none",
              busy && "opacity-55",
            )}
          >
            {TICKER_PRESETS.map((p) => (
              <option key={p.symbol || "custom"} value={p.symbol}>
                {tickerLabel(p, cached)}
              </option>
            ))}
          </select>
          <Caption>
            {cached
              ? "Cached replays this name's recorded historical-reference episode (CVNA late-2022, SVB Mar-2023) — fixed prices, identical every time. The position is sized at 20% of ADV so deep and thin names compare fairly."
              : "Live fetches this name's current real data (price, ADV, free float, volatility) and runs the engine on it, position sized at 20% of ADV. This is today's conditions, not the historical crisis — a name that has since recovered will behave differently."}
          </Caption>
        </div>

        {/* Position size */}
        <div
          className={cn(
            "space-y-1",
            (cached || tickerActive) && "pointer-events-none opacity-55",
          )}
        >
          <Slider
            label="Position size"
            display={`${fmtInt(levers.position_size)} shares`}
            min={50_000}
            max={1_000_000}
            step={10_000}
            value={levers.position_size}
            onChange={(v) => set({ position_size: v })}
          />
          <Caption>
            {tickerActive
              ? "Set automatically to 20% of the selected ticker's ADV. Switch to “Flagship” above to set it by hand."
              : "The number of shares you are trying to sell."}
          </Caption>
        </div>

        {/* Market participants (population_size) */}
        <div className={cn("space-y-1", cached && "pointer-events-none opacity-55")}>
          <Slider
            label="Market participants"
            display={`${fmtInt(levers.population_size)} agents`}
            min={1_000}
            max={20_000}
            step={500}
            value={levers.population_size}
            onChange={(v) => set({ population_size: v })}
          />
          <Caption>
            How many traders make up the market. More participants = a deeper, more
            liquid market that absorbs the same order more easily.
          </Caption>
        </div>

        {/* Exit speed */}
        <div className={cn("space-y-2", cached && "pointer-events-none opacity-55")}>
          <Label>Exit speed</Label>
          <Segmented
            options={EXIT_SPEEDS}
            value={levers.exit_speed}
            onChange={(v) => set({ exit_speed: v })}
            disabled={busy}
          />
          <Caption>
            How aggressively you sell into each tick&apos;s volume. Applies to Live runs
            only — cached mode replays a fixed recording.
          </Caption>
        </div>

        <Divider />

        {/* Crowding mix */}
        <div className={cn("space-y-3", cached && "pointer-events-none opacity-55")}>
          <Label>Crowding mix</Label>
          <Caption>
            Each investor type&apos;s share of the trading crowd — i.e. of the market.
            The shares are normalised to total 100%.
          </Caption>
          {INVESTOR_TYPES.map((t) => (
            <Slider
              key={t}
              label={INVESTOR_LABELS[t]}
              display={`${fmtPct((levers.crowding_mix[t] ?? 0) / mixTotal, 0)} of market`}
              min={0}
              max={40}
              step={1}
              value={levers.crowding_mix[t] ?? 0}
              onChange={(v) => setMix(t, v)}
              accent={investorColor(t)}
            />
          ))}
        </div>
      </div>

      {/* Actions — pinned, always visible */}
      <div className="shrink-0 border-t border-line bg-surface/95 p-4">
        <div className="flex gap-2">
          <Button onClick={onRun} disabled={busy} className="flex-1" size="lg">
            <Play className="h-4 w-4" strokeWidth={2.2} />
            {busy ? "Running…" : cached ? "Replay cascade" : "Run simulation"}
          </Button>
          <Button onClick={onReset} variant="outline" size="lg" disabled={busy} aria-label="Reset">
            <RotateCcw className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function Label({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[12px] font-medium uppercase tracking-[0.1em] text-ink-muted">
        {children}
      </span>
      {hint ? <span className="text-[11px] text-ink-faint">· {hint}</span> : null}
    </div>
  );
}

function Caption({ children }: { children: React.ReactNode }) {
  return <p className="text-[11px] leading-relaxed text-ink-faint">{children}</p>;
}

function Divider() {
  return <div className="h-px w-full bg-line" />;
}
