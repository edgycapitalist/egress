"use client";

import { useMemo } from "react";
import { Play, RotateCcw, Database, Radio } from "lucide-react";
import {
  INVESTOR_TYPES,
  INVESTOR_LABELS,
  investorColor,
  TICKER_PRESETS,
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

// Curated tickers that have a committed cached recording. Any other symbol is live-only.
const PRESET_SYMBOLS = new Set(TICKER_PRESETS.filter((p) => p.symbol).map((p) => p.symbol));
const QUICK_PICKS = TICKER_PRESETS.filter((p) => p.symbol);

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
  avEnabled,
}: {
  state: BuilderState;
  setState: (s: BuilderState) => void;
  onRun: () => void;
  onReset: () => void;
  status: RunStatus;
  geminiEnabled: boolean;
  avEnabled: boolean;
}) {
  const { mode, levers } = state;
  const busy = status === "running" || status === "connecting";
  const cached = mode === "cached";
  // A chosen ticker drives the instrument and sizes the position at a fixed %ADV on
  // the gateway, so the manual position slider doesn't apply while one is set.
  const symbol = (levers.symbol ?? "").trim().toUpperCase();
  const tickerActive = Boolean(symbol);
  // Cached mode only has recordings for the curated presets; any other ticker has no
  // recording and only runs on the live path.
  const isPreset = PRESET_SYMBOLS.has(symbol);
  const cachedNoRecording = cached && tickerActive && !isPreset;

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

        {/* Instrument — type any ticker, or quick-pick a curated name. */}
        <div className="space-y-2">
          <Label>Instrument</Label>
          <input
            type="text"
            value={symbol}
            onChange={(e) => set({ symbol: e.target.value.toUpperCase().replace(/[^A-Z.]/g, "") })}
            placeholder="Ticker, e.g. AAPL — blank = flagship (CVNA)"
            disabled={busy}
            spellCheck={false}
            className={cn(
              "w-full rounded-[8px] border border-line bg-surface-2/60 px-3 py-2 text-[12.5px] uppercase tracking-wide text-ink placeholder:normal-case placeholder:tracking-normal placeholder:text-ink-faint focus:border-line-strong focus:outline-none",
              busy && "opacity-55",
            )}
          />
          <div className="flex flex-wrap gap-1">
            {QUICK_PICKS.map((p) => (
              <button
                key={p.symbol}
                disabled={busy}
                onClick={() => set({ symbol: p.symbol })}
                className={cn(
                  "rounded-[6px] border px-2 py-1 text-[11px] transition-colors",
                  symbol === p.symbol
                    ? "border-line-strong bg-ink text-bg"
                    : "border-line bg-surface-2/60 text-ink-muted hover:text-ink",
                )}
              >
                {p.symbol}
              </button>
            ))}
            <button
              disabled={busy || !tickerActive}
              onClick={() => set({ symbol: "" })}
              className={cn(
                "rounded-[6px] border border-line px-2 py-1 text-[11px] text-ink-muted transition-colors hover:text-ink",
                !tickerActive && "opacity-40",
              )}
            >
              Flagship
            </button>
          </div>
          <Caption>{instrumentCaption({ cached, symbol, isPreset, avEnabled })}</Caption>
          {cachedNoRecording ? (
            <p className="text-[11px] leading-relaxed text-[var(--color-halt)]">
              No cached recording for {symbol}. Cached only has the curated names
              ({QUICK_PICKS.map((p) => p.symbol).join(", ")}); switch to Live to run {symbol}.
            </p>
          ) : null}
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

// Honest, mode/symbol/AV-aware description of what running this instrument will do.
function instrumentCaption({
  cached,
  symbol,
  isPreset,
  avEnabled,
}: {
  cached: boolean;
  symbol: string;
  isPreset: boolean;
  avEnabled: boolean;
}): string {
  if (cached) {
    if (!symbol)
      return "Cached replays the recorded CVNA flagship cascade — fixed, offline, identical every time.";
    if (isPreset)
      return `Cached replays the recorded historical-reference episode for ${symbol} (fixed prices). Position sized at 20% of ADV so deep and thin names compare fairly.`;
    return `Cached has no recording for ${symbol} — switch to Live to run it on current data.`;
  }
  if (!symbol)
    return "Live runs the engine on the CVNA flagship (curated reference) with your manual position size below.";
  return avEnabled
    ? `Live fetches ${symbol}'s current real data (price, ADV, volatility) from Alpha Vantage and runs the engine on it — today's conditions, not the historical crisis. Position sized at 20% of ADV.`
    : `Live runs ${symbol} on a curated/synthetic reference — no Alpha Vantage key is configured, so this is not real current data. Position sized at 20% of ADV.`;
}

function Divider() {
  return <div className="h-px w-full bg-line" />;
}
