"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Play,
  RotateCcw,
  Database,
  Radio,
  Check,
  Loader2,
  AlertCircle,
  Clock,
  FileSearch,
  Upload,
  Users,
} from "lucide-react";
import {
  INVESTOR_TYPES,
  INVESTOR_LABELS,
  investorColor,
  TICKER_PRESETS,
  type InvestorType,
  type Levers,
  type PeerCrowdingProfile,
  type PeerSourceMode,
} from "@/lib/types";
import type { RunStatus } from "@/lib/useRun";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn, fmtInt, fmtPct, fmtPrice } from "@/lib/utils";

const HTTP_BASE = process.env.NEXT_PUBLIC_GATEWAY_HTTP ?? "http://127.0.0.1:8000";

// Result of checking a typed ticker against the live data feed.
type TickerCheck =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "found"; name: string | null; price: number }
  | { status: "notfound" };

const EXIT_SPEEDS: { key: string; label: string }[] = [
  { key: "patient", label: "Patient" },
  { key: "measured", label: "Measured" },
  { key: "urgent", label: "Urgent" },
  { key: "fire_sale", label: "Fire sale" },
];

// Curated tickers that have a committed cached recording. Any other symbol is live-only.
const PRESET_SYMBOLS = new Set(TICKER_PRESETS.filter((p) => p.symbol).map((p) => p.symbol));
const QUICK_PICKS = TICKER_PRESETS.filter((p) => p.symbol);

const DEFAULT_PEER: Partial<PeerCrowdingProfile> = {
  case: "base",
  peer_fund_count: 10,
  overlap_pct: 0.45,
  avg_peer_position_pct_adv: 0.05,
  shared_trigger_drawdown_pct: 0.06,
  correlated_exit_probability: 0.65,
  leverage_sensitivity: 0.4,
  redemption_pressure: 0.35,
  etf_flow_pressure: 0.2,
  evidence_source: "synthetic_assumption",
  confidence: "low",
  notes: "User-edited assumption-led peer-crowding controls.",
};

const PEER_MODES: { key: PeerSourceMode; label: string; icon: React.ReactNode }[] = [
  { key: "assumption_led", label: "Assume", icon: <Users className="h-3.5 w-3.5" /> },
  { key: "sec_evidence", label: "SEC", icon: <FileSearch className="h-3.5 w-3.5" /> },
  { key: "user_upload", label: "Upload", icon: <Upload className="h-3.5 w-3.5" /> },
];

const HORIZON_HOURS = [3, 6.5, 12, 24];
const HORIZON_DAYS = [1, 2, 3, 5];

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
  // In live mode the user types a ticker and sets their own position. In a saved
  // example the position is fixed by the recording, so the live input does not apply.
  const symbol = (levers.symbol ?? "").trim().toUpperCase();
  const tickerActive = Boolean(symbol);
  // Cached mode only has recordings for the curated presets; any other ticker has no
  // recording and only runs on the live path.
  const isPreset = PRESET_SYMBOLS.has(symbol);
  const cachedNoRecording = cached && tickerActive && !isPreset;
  // The position the selected saved recording actually sold (20% of that name's ADV);
  // a blank/unknown ticker in cached falls back to the default CVNA recording.
  const cachedShares =
    TICKER_PRESETS.find((p) => p.symbol === symbol)?.recordedShares ??
    TICKER_PRESETS.find((p) => p.symbol === "CVNA")!.recordedShares;

  // Dynamically check whether a typed ticker resolves to real live data. Only meaningful
  // in live mode with a data key configured; debounced and cached per symbol to spare the
  // feed's small daily budget. (Cached mode validates via the saved-recording check.)
  const [tickerCheck, setTickerCheck] = useState<TickerCheck>({ status: "idle" });
  const checkCache = useRef<Map<string, TickerCheck>>(new Map());
  const validatable = !cached && avEnabled && /^[A-Z.]{1,6}$/.test(symbol);

  useEffect(() => {
    if (!validatable) {
      setTickerCheck({ status: "idle" });
      return;
    }
    const hit = checkCache.current.get(symbol);
    if (hit) {
      setTickerCheck(hit);
      return;
    }
    setTickerCheck({ status: "checking" });
    const id = setTimeout(() => {
      fetch(`${HTTP_BASE}/api/instrument?symbol=${encodeURIComponent(symbol)}&live=1`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          const res: TickerCheck =
            d && (d.source === "alphavantage" || d.source === "curated")
              ? { status: "found", name: d.name ?? null, price: d.reference_price }
              : { status: "notfound" };
          checkCache.current.set(symbol, res);
          setTickerCheck(res);
        })
        .catch(() => setTickerCheck({ status: "idle" }));
    }, 600);
    return () => clearTimeout(id);
  }, [symbol, validatable]);

  const mixTotal = useMemo(
    () => INVESTOR_TYPES.reduce((s, t) => s + (levers.crowding_mix[t] ?? 0), 0) || 1,
    [levers.crowding_mix],
  );
  const peerMode = (levers.peer_source_mode ?? "assumption_led") as PeerSourceMode;
  const peer = { ...DEFAULT_PEER, ...(levers.peer_crowding ?? {}) };
  const horizonMode = levers.exit_horizon_hours != null ? "hours" : "days";
  const horizonValue =
    horizonMode === "hours"
      ? (levers.exit_horizon_hours ?? 6.5)
      : (levers.exit_horizon_days ?? 3);

  const set = (patch: Partial<Levers>) => setState({ ...state, levers: { ...levers, ...patch } });
  const setMix = (t: InvestorType, v: number) =>
    set({ crowding_mix: { ...levers.crowding_mix, [t]: v } });
  const setPeerMode = (mode: PeerSourceMode) => {
    const patch: Partial<Levers> = { peer_source_mode: mode };
    if (mode === "assumption_led") {
      patch.peer_crowding = { ...DEFAULT_PEER, ...(levers.peer_crowding ?? {}) };
      patch.user_holdings_csv = "";
    }
    set(patch);
  };
  const setPeer = (patch: Partial<PeerCrowdingProfile>) =>
    set({
      peer_crowding: {
        ...DEFAULT_PEER,
        ...(levers.peer_crowding ?? {}),
        ...patch,
        case: "base",
        evidence_source: "synthetic_assumption",
        confidence: "low",
      },
    });
  const setHorizonMode = (mode: "hours" | "days") => {
    const value = mode === "hours" ? 6.5 : 3;
    set(
      mode === "hours"
        ? {
            exit_horizon_hours: value,
            exit_horizon_days: undefined,
            time_scale: {
              ...(levers.time_scale ?? {}),
              session_ticks: levers.time_scale?.session_ticks ?? 100,
              exit_horizon_hours: value,
              exit_horizon_days: null,
            },
          }
        : {
            exit_horizon_days: value,
            exit_horizon_hours: undefined,
            time_scale: {
              ...(levers.time_scale ?? {}),
              session_ticks: levers.time_scale?.session_ticks ?? 100,
              exit_horizon_days: value,
              exit_horizon_hours: null,
            },
          },
    );
  };
  const setHorizonValue = (value: number) => {
    const next = Math.max(0.25, value || 0.25);
    set(
      horizonMode === "hours"
        ? {
            exit_horizon_hours: next,
            exit_horizon_days: undefined,
            time_scale: {
              ...(levers.time_scale ?? {}),
              session_ticks: levers.time_scale?.session_ticks ?? 100,
              exit_horizon_hours: next,
              exit_horizon_days: null,
            },
          }
        : {
            exit_horizon_days: next,
            exit_horizon_hours: undefined,
            time_scale: {
              ...(levers.time_scale ?? {}),
              session_ticks: levers.time_scale?.session_ticks ?? 100,
              exit_horizon_days: next,
              exit_horizon_hours: null,
            },
          },
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 pb-4 pt-1">
        {/* Mode */}
        <div className="space-y-2">
          <Label>Run source</Label>
          <Segmented
            options={[
              { key: "cached", label: "Saved example", icon: <Database className="h-3.5 w-3.5" /> },
              { key: "live", label: "Live", icon: <Radio className="h-3.5 w-3.5" /> },
            ]}
            value={mode}
            onChange={(m) => {
              // Switching mode invalidates the displayed run (its source no longer
              // matches), so clear it: the stale "Saved example / Complete" badge and
              // results go away and the status returns to Ready until the next run.
              if (m !== mode) onReset();
              setState({ ...state, mode: m });
            }}
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
              Use real Gemini (Vertex AI). This spends credits.
            </label>
          ) : null}
          <p className="text-[11px] leading-relaxed text-ink-faint">
            {cached
              ? "Replays a saved example crash. Fully offline and identical every time, so it is the easiest way to start."
              : state.gemini
                ? "Runs the simulation for real, with Gemini deciding how each kind of investor reacts to your scenario and the latest news."
                : "Runs the simulation on the ticker's current real market data. This reflects today's conditions, not the original crisis."}
          </p>
        </div>

        <Divider />

        {/* Plain-language scenario */}
        <div className="space-y-2">
          <Label>Stress event</Label>
          <textarea
            value={levers.scenario_text}
            onChange={(e) => set({ scenario_text: e.target.value })}
            disabled={cached || busy}
            rows={4}
            className={cn(
              "w-full resize-none rounded-[8px] border border-line bg-surface-2/60 px-3 py-2.5 text-[12.5px] leading-relaxed text-ink placeholder:text-ink-faint focus:border-line-strong focus:outline-none",
              (cached || busy) && "opacity-55",
            )}
            placeholder="Describe the crisis you want to test. The more severe your wording, the harder the simulated shock. For example: a sudden bankruptcy scare with panic selling and no buyers."
          />
          {!cached ? (
            <Caption>
              Your wording drives the run. Together with the ticker&apos;s latest news, it sets how
              severe the crisis is. A mild description may leave the exit open; a severe one can
              close it.
            </Caption>
          ) : null}
        </div>

        {/* Instrument - live: type any ticker. cached: pick a curated recording. */}
        <div className="space-y-2">
          <Label>Stock (ticker)</Label>
          <input
            type="text"
            value={symbol}
            onChange={(e) => set({ symbol: e.target.value.toUpperCase().replace(/[^A-Z.]/g, "") })}
            placeholder={cached ? "Leave blank for CVNA" : "Type a ticker, e.g. AAPL"}
            disabled={busy}
            spellCheck={false}
            className={cn(
              "w-full rounded-[8px] border border-line bg-surface-2/60 px-3 py-2 text-[12.5px] uppercase tracking-wide text-ink placeholder:normal-case placeholder:tracking-normal placeholder:text-ink-faint focus:border-line-strong focus:outline-none",
              busy && "opacity-55",
            )}
          />
          {/* Curated quick-picks (saved recordings) belong to cached mode. */}
          {cached ? (
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
            </div>
          ) : null}
          <Caption>{instrumentCaption({ cached, symbol, isPreset, avEnabled })}</Caption>
          {cachedNoRecording ? (
            <p className="text-[11px] leading-relaxed text-[var(--color-halt)]">
              No saved recording for {symbol}. Saved examples only cover{" "}
              {QUICK_PICKS.map((p) => p.symbol).join(", ")}. Switch to Live to run {symbol} on
              current data.
            </p>
          ) : null}
          {validatable ? (
            <div className="flex items-start gap-1.5 text-[11px] leading-relaxed">
              {tickerCheck.status === "checking" ? (
                <>
                  <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin text-ink-faint" />
                  <span className="text-ink-faint">Checking {symbol}…</span>
                </>
              ) : tickerCheck.status === "found" ? (
                <>
                  <Check className="mt-0.5 h-3 w-3 shrink-0 text-buy" />
                  <span className="text-buy">
                    {tickerCheck.name ?? symbol} found at {fmtPrice(tickerCheck.price)}.
                  </span>
                </>
              ) : tickerCheck.status === "notfound" ? (
                <>
                  <AlertCircle className="mt-0.5 h-3 w-3 shrink-0 text-[var(--color-halt)]" />
                  <span className="text-[var(--color-halt)]">
                    No live data for {symbol}. It may be an unrecognised ticker, or the daily data
                    limit was reached. It will run on stand-in numbers.
                  </span>
                </>
              ) : null}
            </div>
          ) : null}
        </div>

        {/* Position size - a free, editable share count on the live path (your real
            position); cached replays a fixed recording, so it doesn't apply there. */}
        {cached ? (
          <div className="space-y-1.5">
            <Label>Position size</Label>
            <div className="flex items-baseline justify-between rounded-[8px] border border-line bg-surface-2/40 px-3 py-2">
              <span className="text-[12.5px] text-ink">
                <span className="tnum">{fmtInt(cachedShares)}</span> shares
              </span>
              <span className="text-[11px] text-ink-faint">fixed by recording</span>
            </div>
            <Caption>
              This saved example sold a fixed block (about 20% of the stock&apos;s average daily
              volume), so it changes with the chosen stock, not by hand. Switch to Live to set
              your own position.
            </Caption>
          </div>
        ) : (
          <div className="space-y-1.5">
            <Label>Position size</Label>
            <div className="relative">
              <input
                type="number"
                min={1}
                step={10_000}
                value={levers.position_size}
                onChange={(e) =>
                  set({ position_size: Math.max(1, Math.round(Number(e.target.value) || 0)) })
                }
                disabled={busy}
                className={cn(
                  "tnum w-full rounded-[8px] border border-line bg-surface-2/60 px-3 py-2 pr-14 text-[12.5px] text-ink focus:border-line-strong focus:outline-none",
                  busy && "opacity-55",
                )}
              />
              <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-ink-faint">
                shares
              </span>
            </div>
            <Caption>
              How many shares you are trying to sell. This is your real position, and it runs
              exactly as entered. A bigger position is harder to get out.
            </Caption>
          </div>
        )}

        {/* Market participants (population_size) */}
        <div className={cn("space-y-1", cached && "pointer-events-none opacity-55")}>
          <Slider
            label="Number of traders"
            display={`${fmtInt(levers.population_size)} traders`}
            min={1_000}
            max={20_000}
            step={500}
            value={levers.population_size}
            onChange={(v) => set({ population_size: v })}
          />
          <Caption>
            How many traders are in the simulated market. More traders means a deeper, more liquid
            market that can absorb your selling more easily.
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
            How hard you push to sell at each step. Faster selling gets out sooner but pushes the
            price down more. Applies to live runs only; a saved example just replays a recording.
          </Caption>
        </div>

        {/* Exit horizon */}
        <div className={cn("space-y-2", cached && "pointer-events-none opacity-55")}>
          <Label>Exit horizon</Label>
          <Segmented
            options={[
              { key: "days", label: "Days", icon: <Clock className="h-3.5 w-3.5" /> },
              { key: "hours", label: "Hours", icon: <Clock className="h-3.5 w-3.5" /> },
            ]}
            value={horizonMode}
            onChange={setHorizonMode}
            disabled={busy}
          />
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <input
              type="number"
              min={0.25}
              step={horizonMode === "hours" ? 0.5 : 0.25}
              value={horizonValue}
              onChange={(e) => setHorizonValue(Number(e.target.value))}
              disabled={busy}
              className={cn(
                "tnum w-full rounded-[8px] border border-line bg-surface-2/60 px-3 py-2 text-[12.5px] text-ink focus:border-line-strong focus:outline-none",
                busy && "opacity-55",
              )}
            />
            <span className="flex min-w-16 items-center justify-center rounded-[8px] border border-line bg-surface-2/40 px-3 text-[11px] text-ink-faint">
              {horizonMode}
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            {(horizonMode === "hours" ? HORIZON_HOURS : HORIZON_DAYS).map((v) => (
              <button
                key={v}
                disabled={busy}
                onClick={() => setHorizonValue(v)}
                className={cn(
                  "rounded-[6px] border px-2 py-1 text-[11px] transition-colors",
                  Math.abs(horizonValue - v) < 0.01
                    ? "border-line-strong bg-ink text-bg"
                    : "border-line bg-surface-2/60 text-ink-muted hover:text-ink",
                )}
              >
                <span className="tnum">{v}</span>
              </button>
            ))}
          </div>
          <Caption>
            The engine uses 100 ticks per average-volume session. This converts the clock horizon
            into ticks, so a three-day exit has three sessions to complete.
          </Caption>
        </div>

        <Divider />

        {/* Peer crowding */}
        <div className={cn("space-y-3", cached && "pointer-events-none opacity-55")}>
          <div className="space-y-2">
            <Label>Peer crowding</Label>
            <Segmented
              options={PEER_MODES}
              value={peerMode}
              onChange={setPeerMode}
              disabled={busy}
            />
            <Caption>{peerModeCaption(peerMode)}</Caption>
          </div>

          {peerMode === "user_upload" ? (
            <div className="space-y-1.5">
              <textarea
                value={levers.user_holdings_csv ?? ""}
                onChange={(e) => set({ user_holdings_csv: e.target.value })}
                disabled={busy}
                rows={4}
                className={cn(
                  "w-full resize-none rounded-[8px] border border-line bg-surface-2/60 px-3 py-2.5 font-mono text-[11.5px] leading-relaxed text-ink placeholder:text-ink-faint focus:border-line-strong focus:outline-none",
                  busy && "opacity-55",
                )}
                placeholder={"symbol,manager,shares\nCVNA,Alpha Fund,1000000"}
              />
              <Caption>
                Rows can include symbol, manager, shares, pct_adv, leverage_sensitivity, and
                redemption_pressure. Uploaded evidence overrides SEC or synthetic assumptions.
              </Caption>
            </div>
          ) : null}

          {peerMode === "assumption_led" ? (
            <div className="space-y-2.5">
              <Slider
                label="Peer funds"
                display={`${Math.round(Number(peer.peer_fund_count ?? 0))} funds`}
                min={0}
                max={40}
                step={1}
                value={Number(peer.peer_fund_count ?? 0)}
                onChange={(v) => setPeer({ peer_fund_count: v })}
              />
              <Slider
                label="Overlap"
                display={fmtPct(Number(peer.overlap_pct ?? 0), 0)}
                min={0}
                max={1}
                step={0.01}
                value={Number(peer.overlap_pct ?? 0)}
                onChange={(v) => setPeer({ overlap_pct: v })}
              />
              <Slider
                label="Avg peer size"
                display={`${fmtPct(Number(peer.avg_peer_position_pct_adv ?? 0), 1)} ADV`}
                min={0}
                max={0.2}
                step={0.005}
                value={Number(peer.avg_peer_position_pct_adv ?? 0)}
                onChange={(v) => setPeer({ avg_peer_position_pct_adv: v })}
              />
              <Slider
                label="Shared trigger"
                display={`${fmtPct(Number(peer.shared_trigger_drawdown_pct ?? 0), 1)} drawdown`}
                min={0.01}
                max={0.15}
                step={0.005}
                value={Number(peer.shared_trigger_drawdown_pct ?? 0)}
                onChange={(v) => setPeer({ shared_trigger_drawdown_pct: v })}
              />
              <Slider
                label="Correlated exit"
                display={fmtPct(Number(peer.correlated_exit_probability ?? 0), 0)}
                min={0}
                max={1}
                step={0.01}
                value={Number(peer.correlated_exit_probability ?? 0)}
                onChange={(v) => setPeer({ correlated_exit_probability: v })}
              />
              <div className="grid grid-cols-1 gap-2">
                <MiniSlider
                  label="Leverage"
                  value={Number(peer.leverage_sensitivity ?? 0)}
                  onChange={(v) => setPeer({ leverage_sensitivity: v })}
                />
                <MiniSlider
                  label="Redemptions"
                  value={Number(peer.redemption_pressure ?? 0)}
                  onChange={(v) => setPeer({ redemption_pressure: v })}
                />
                <MiniSlider
                  label="ETF flows"
                  value={Number(peer.etf_flow_pressure ?? 0)}
                  onChange={(v) => setPeer({ etf_flow_pressure: v })}
                />
              </div>
            </div>
          ) : null}
        </div>

        <Divider />

        {/* Crowding mix */}
        <div className={cn("space-y-3", cached && "pointer-events-none opacity-55")}>
          <Label>Who is in the market</Label>
          <Caption>
            What share of the crowd each kind of investor makes up. A market packed with forced and
            panic sellers and few buyers is what makes an exit close. The shares are scaled to total
            100%.
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

      {/* Actions - pinned, always visible */}
      <div className="shrink-0 border-t border-line bg-surface/95 p-4">
        <div className="flex gap-2">
          <Button onClick={onRun} disabled={busy} className="flex-1" size="lg">
            <Play className="h-4 w-4" strokeWidth={2.2} />
            {busy ? "Running…" : cached ? "Replay example" : "Run simulation"}
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

function MiniSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="grid grid-cols-[74px_1fr_38px] items-center gap-2">
      <span className="text-[11px] text-ink-faint">{label}</span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="tnum text-right text-[11px] text-ink-muted">{fmtPct(value, 0)}</span>
    </div>
  );
}

function peerModeCaption(mode: PeerSourceMode): string {
  if (mode === "sec_evidence") {
    return "Uses free public SEC EDGAR evidence when enabled, then falls back to curated or synthetic assumptions with labels.";
  }
  if (mode === "user_upload") {
    return "Uses your uploaded holder rows first, so private or desk-curated evidence can drive the peer profile.";
  }
  return "Uses explicit low-confidence assumptions you can edit. Good for exploring where the exit breaks without paid data.";
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
      return "Replays the saved Carvana (CVNA) 2022 crash. Fixed, offline, and identical every time.";
    if (isPreset)
      return `Replays the saved ${symbol} example with fixed historical prices. Identical every time.`;
    return `No saved example for ${symbol}. Switch to Live to run it on current data.`;
  }
  if (!symbol)
    return "Type a ticker to pull its current data. Left blank, it uses CVNA. Your position size and stress description below drive the run.";
  return avEnabled
    ? `Pulls ${symbol}'s current real data (price, volume, volatility) from Alpha Vantage and runs the simulation on it. This is today's market, not the original crisis. Your stress description and the latest news set how severe it gets.`
    : `Runs ${symbol} on stand-in data (no Alpha Vantage key is set), so these are not real current numbers. Your stress description sets how severe the crisis gets.`;
}

function Divider() {
  return <div className="h-px w-full bg-line" />;
}
