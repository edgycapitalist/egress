// Shapes mirrored from the engine ⇄ agents boundary contract (docs/contracts.md)
// and the gateway's WebSocket frame protocol (gateway/app.py). The frontend is a
// thin consumer of these — it never invents dynamics, it renders what the engine
// produced.

export const INVESTOR_TYPES = [
  "forced_seller",
  "panic_seller",
  "trend_follower",
  "bargain_hunter",
  "market_maker",
  "holder",
] as const;

export type InvestorType = (typeof INVESTOR_TYPES)[number];

export interface Shock {
  tick: number;
  kind: "news" | "price";
  severity: number;
  note: string;
}

export interface RunConfig {
  run_id: string;
  seed: number;
  instrument: {
    symbol: string;
    reference_price: number;
    tick_size: number;
    adv: number;
    free_float: number;
    halt_tier: number;
  };
  position: { side: "sell"; quantity: number; arrival_price: number };
  exit_speed: { mode: string; participation_rate: number | null; horizon_ticks: number | null };
  crowding_mix: Record<InvestorType, number>;
  population_size: number;
  shock_schedule: Shock[];
  halt_rule: { band_pct: number; window_ticks: number; pause_ticks: number };
  max_ticks: number;
  ticks_per_window: number;
  baseline_mode: boolean;
}

export interface Fill {
  price: number;
  size: number;
  aggressor: "buy" | "sell";
}

export interface TickEvent {
  type: "tick";
  tick: number;
  last_price: number;
  best_bid: number | null;
  best_ask: number | null;
  depth_bid: number;
  depth_ask: number;
  fills: Fill[];
  filled_this_tick: number;
  cumulative_filled: number;
  vwap_sold: number | null;
  actions_by_type: Record<string, number>;
  halted: boolean;
  halt_started: boolean;
  shock_applied: Shock | null;
}

export interface Metrics {
  type: "metrics";
  run_id: string;
  fill_rate: number;
  filled_qty: number;
  stuck_qty: number;
  pct_stuck: number;
  implementation_shortfall_bps: number;
  slippage_bps: number;
  vwap_sold: number | null;
  arrival_price: number;
  final_price: number;
  max_drawdown_pct: number;
  time_to_exit_ticks: number | null;
  halt_triggered: boolean;
  halt_count: number;
  ticks_run: number;
}

export type RunSource = "cached" | "live-baseline" | "live-gemini";

// Server → client frames.
export type Frame =
  | { type: "meta"; source: RunSource; schema_version: string; config: RunConfig; total_ticks: number }
  | { type: "ticks"; ticks: TickEvent[] }
  | { type: "metrics"; metrics: Metrics }
  | { type: "analysis"; analysis: string }
  | { type: "status"; message: string }
  | { type: "error"; message: string }
  | { type: "done" };

// Scenario-builder levers (client → gateway).
export interface Levers {
  scenario_text: string;
  symbol?: string; // curated ticker preset; "" = flagship CVNA with the manual size
  position_size: number;
  population_size: number;
  exit_speed: string;
  crowding_mix: Record<InvestorType, number>;
  seed?: number;
}

// Tickers for the instrument picker. The same symbol means different things by mode:
// in CACHED mode it replays a recorded historical-reference episode; in LIVE mode the
// gateway fetches the name's CURRENT real data (Alpha Vantage) and runs the engine on
// it. "" keeps the flagship + manual size. Labels are mode-aware via tickerLabel().
export interface TickerPreset {
  symbol: string; // "" = flagship / manual size
  name: string; // short company name
  era: string; // the historical reference window the cached recording represents
  group: "liquid" | "illiquid" | "custom";
}

export const TICKER_PRESETS: TickerPreset[] = [
  { symbol: "", name: "Flagship (CVNA)", era: "", group: "custom" },
  { symbol: "CVNA", name: "Carvana", era: "late-2022", group: "illiquid" },
  { symbol: "SIVB", name: "SVB Financial", era: "Mar-2023", group: "illiquid" },
  { symbol: "AAPL", name: "Apple", era: "bad-earnings day", group: "liquid" },
  { symbol: "SPY", name: "S&P 500 ETF", era: "drawdown", group: "liquid" },
];

// Honest, mode-aware label: cached = recorded historical reference, live = current data.
export function tickerLabel(p: TickerPreset, cached: boolean): string {
  if (!p.symbol) return cached ? "Flagship (CVNA) — recorded demo" : "Flagship (CVNA) — manual size";
  return cached ? `${p.symbol} — ${p.era} reference` : `${p.symbol} — live (current data)`;
}

// Real sourced inputs for an instrument (from the gateway's /api/instrument).
export interface SourcedInput {
  symbol: string;
  name: string | null;
  reference_price: number;
  adv: number;
  free_float: number;
  realized_vol_daily: number | null;
  window: { start: string | null; end: string | null } | null;
  bars: number;
  source: "alphavantage" | "synthetic" | "curated";
}

export const INVESTOR_LABELS: Record<InvestorType, string> = {
  forced_seller: "Forced sellers",
  panic_seller: "Panic sellers",
  trend_follower: "Trend followers",
  bargain_hunter: "Bargain hunters",
  market_maker: "Market makers",
  holder: "Long-term holders",
};

export const INVESTOR_SHORT: Record<InvestorType, string> = {
  forced_seller: "Forced",
  panic_seller: "Panic",
  trend_follower: "Trend",
  bargain_hunter: "Bargain",
  market_maker: "Makers",
  holder: "Holders",
};

// CSS custom-property colour per type (defined in globals.css @theme).
export const investorColor = (t: InvestorType) => `var(--color-${t})`;
