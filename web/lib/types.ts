// Shapes mirrored from the engine ⇄ agents boundary contract (docs/contracts.md)
// and the gateway's WebSocket frame protocol (gateway/app.py). The frontend is a
// thin consumer of these - it never invents dynamics, it renders what the engine
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
export type Confidence = "low" | "medium" | "high";
export type EvidenceSource =
  | "alpha_vantage"
  | "sec_edgar"
  | "user_upload"
  | "curated_fixture"
  | "synthetic_assumption"
  | "gemini_inference"
  | "none";
export type ScenarioMode =
  | "historical_saved"
  | "live_current"
  | "assumption_led"
  | "sec_evidence"
  | "user_upload";
export type PeerCrowdingCase = "low" | "base" | "high" | "custom";
export type PeerSourceMode =
  | "auto"
  | "assumption_led"
  | "sec_evidence"
  | "user_upload"
  | "curated_fixture";

export interface EvidenceItem {
  field: string;
  source: EvidenceSource;
  confidence: Confidence;
  label: string;
  as_of: string | null;
  notes: string;
}

export interface EvidenceSummary {
  items: EvidenceItem[];
  summary: string;
}

export interface PeerCrowdingProfile {
  case: PeerCrowdingCase;
  peer_fund_count: number;
  overlap_pct: number;
  avg_peer_position_pct_adv: number;
  shared_trigger_drawdown_pct: number;
  correlated_exit_probability: number;
  leverage_sensitivity: number;
  redemption_pressure: number;
  etf_flow_pressure: number;
  evidence_source: EvidenceSource;
  confidence: Confidence;
  notes: string;
}

export interface TimeScale {
  tick_duration_seconds: number;
  session_ticks: number;
  exit_horizon_ticks: number | null;
  exit_horizon_hours: number | null;
  exit_horizon_days: number | null;
}

export interface PeerActionCounts {
  triggered_funds: number;
  liquidating_funds: number;
  shares_sold: number;
  shares_remaining: number;
}

export interface ImpactAttribution {
  exogenous_shock_bps: number;
  endogenous_trading_bps: number;
  liquidity_withdrawal_bps: number;
}

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
  peer_crowding?: PeerCrowdingProfile | null;
  time_scale?: TimeScale;
  scenario_mode?: ScenarioMode;
  evidence_summary?: EvidenceSummary | null;
  crisis_intensity?: number; // crisis magnitude; 1.0 = neutral (absent on older replays)
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
  peer_actions?: PeerActionCounts;
  impact_attribution?: ImpactAttribution;
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
  impact_attribution?: ImpactAttribution;
  ensemble_case?: PeerCrowdingCase | null;
  ensemble_seed?: number | null;
}

export interface MetricBand {
  low: number;
  median: number;
  high: number;
}

export interface EnsembleCaseSummary {
  case: PeerCrowdingCase;
  seeds: number[];
  peer_crowding: PeerCrowdingProfile | null;
  metrics: Metrics;
  representative_replay_ref: string | null;
}

export interface EnsembleResult {
  type: "ensemble";
  run_id: string;
  cases: EnsembleCaseSummary[];
  bands: Record<string, MetricBand>;
  representative_case: PeerCrowdingCase;
  representative_replay_ref: string | null;
  evidence_summary: EvidenceSummary | null;
}

export type RunSource = "cached" | "live-baseline" | "live-gemini";

export interface ReplayPayload {
  schema_version: string | null;
  config: RunConfig;
  total_ticks: number;
  ticks: TickEvent[];
  metrics: Metrics | null;
}

// Server → client frames.
export type Frame =
  | { type: "meta"; source: RunSource; schema_version: string; config: RunConfig; total_ticks: number }
  | { type: "ticks"; ticks: TickEvent[] }
  | { type: "metrics"; metrics: Metrics }
  | { type: "ensemble"; ensemble: EnsembleResult }
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
  peer_source_mode?: PeerSourceMode;
  peer_crowding?: Partial<PeerCrowdingProfile>;
  user_holdings_csv?: string;
  time_scale?: Partial<TimeScale>;
  exit_horizon_ticks?: number;
  exit_horizon_hours?: number;
  exit_horizon_days?: number;
  seed?: number;
}

// Tickers for the instrument picker. The same symbol means different things by mode:
// in a SAVED EXAMPLE (cached) it replays a recorded historical-reference episode; in
// LIVE mode the gateway fetches the name's CURRENT real data (Alpha Vantage) and runs
// the engine on it. "" replays the default CVNA recording. recordedShares is the
// position each saved recording actually sold (20% of that name's ADV), so the cached
// view can show the real number rather than the live input lever.
export interface TickerPreset {
  symbol: string; // "" = the default CVNA recording
  name: string; // short company name
  era: string; // the historical reference window the cached recording represents
  group: "liquid" | "illiquid" | "custom";
  recordedShares: number; // shares the saved recording sold (20% of ADV)
}

export const TICKER_PRESETS: TickerPreset[] = [
  { symbol: "CVNA", name: "Carvana", era: "late-2022", group: "illiquid", recordedShares: 2_400_000 },
  { symbol: "SIVB", name: "SVB Financial", era: "Mar-2023", group: "illiquid", recordedShares: 260_000 },
  { symbol: "AAPL", name: "Apple", era: "bad-earnings day", group: "liquid", recordedShares: 11_000_000 },
  { symbol: "SPY", name: "S&P 500 ETF", era: "drawdown", group: "liquid", recordedShares: 15_000_000 },
];

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
