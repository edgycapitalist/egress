import type { CounterfactualAttribution, ImpactAttribution, Metrics } from "@/lib/types";
import { cn, fmtBps, fmtInt, fmtPct } from "@/lib/utils";

function Metric({
  label,
  value,
  sub,
  tone = "ink",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "ink" | "sell" | "buy" | "halt";
}) {
  const toneColor: Record<string, string> = {
    ink: "var(--color-ink)",
    sell: "var(--color-sell)",
    buy: "var(--color-buy)",
    halt: "var(--color-halt)",
  };
  return (
    <div className="px-4 py-3">
      <div className="text-[11px] uppercase tracking-[0.1em] text-ink-faint">{label}</div>
      <div className="tnum mt-1 text-[19px] leading-none" style={{ color: toneColor[tone] }}>
        {value}
      </div>
      {sub ? <div className="tnum mt-1 text-[11px] text-ink-faint">{sub}</div> : null}
    </div>
  );
}

export function MetricsPanel({ metrics }: { metrics: Metrics | null }) {
  if (!metrics) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 divide-x divide-y divide-line">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="px-4 py-3">
            <div className="h-2.5 w-16 rounded bg-surface-2" />
            <div className="mt-2 h-4 w-12 rounded bg-surface-2" />
          </div>
        ))}
      </div>
    );
  }

  const exited =
    metrics.time_to_exit_ticks != null ? `${metrics.time_to_exit_ticks} steps` : "never";
  const counterfactual = metrics.counterfactual_attribution ?? null;
  const impact = metrics.impact_attribution;

  return (
    <div>
      <div className={cn("grid grid-cols-2 sm:grid-cols-3 divide-x divide-y divide-line")}>
        <Metric
          label="Sold"
          value={fmtPct(metrics.fill_rate, 0)}
          sub={`${fmtInt(metrics.filled_qty)} shares`}
          tone={metrics.fill_rate < 0.5 ? "sell" : "buy"}
        />
        <Metric
          label="Left stuck"
          value={fmtPct(metrics.pct_stuck, 0)}
          sub={`${fmtInt(metrics.stuck_qty)} shares`}
          tone={metrics.pct_stuck > 0.3 ? "sell" : "ink"}
        />
        <Metric
          label="Worst price drop"
          value={fmtPct(metrics.max_drawdown_pct, 0)}
          sub={`ended at ${metrics.final_price.toFixed(2)}`}
          tone="sell"
        />
        <Metric label="Slippage" value={fmtBps(metrics.slippage_bps)} sub="vs start price" />
        <Metric
          label="Total cost"
          value={fmtBps(metrics.implementation_shortfall_bps)}
          sub={`avg sale ${metrics.vwap_sold != null ? metrics.vwap_sold.toFixed(2) : "n/a"}`}
        />
        <Metric
          label="Time to exit"
          value={exited}
          sub={`${metrics.halt_count} halt${metrics.halt_count === 1 ? "" : "s"} · ${metrics.ticks_run} steps`}
          tone={metrics.halt_triggered ? "halt" : "ink"}
        />
      </div>
      <ImpactEstimate counterfactual={counterfactual} impact={impact} />
    </div>
  );
}

function ImpactEstimate({
  counterfactual,
  impact,
}: {
  counterfactual: CounterfactualAttribution | null;
  impact: ImpactAttribution | undefined;
}) {
  if (counterfactual) {
    return (
      <div className="border-t border-line px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] uppercase tracking-[0.1em] text-ink-faint">
            Paired counterfactual impact estimates
          </span>
          <span className="text-[11px] text-ink-faint">approximate deltas, not causes</span>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
          <EstimateMini label="Shocks" value={counterfactual.exogenous_shock_bps} />
          <EstimateMini label="Peer cascade" value={counterfactual.peer_cascade_bps} />
          <EstimateMini label="Own exit" value={counterfactual.own_exit_bps} />
          <EstimateMini
            label="Residual"
            value={counterfactual.residual_market_behavior_bps}
          />
        </div>
      </div>
    );
  }
  if (!impact) return null;
  return (
    <div className="border-t border-line px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] uppercase tracking-[0.1em] text-ink-faint">
          Heuristic impact estimates
        </span>
        <span className="text-[11px] text-ink-faint">same-run estimate, not causal proof</span>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <EstimateMini label="Shocks" value={impact.exogenous_shock_bps} />
        <EstimateMini label="Trading" value={impact.endogenous_trading_bps} />
        <EstimateMini label="Liquidity" value={impact.liquidity_withdrawal_bps} />
      </div>
    </div>
  );
}

function EstimateMini({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0 rounded-[8px] border border-line bg-surface-2/35 px-2.5 py-2">
      <div className="truncate text-[10px] uppercase tracking-[0.08em] text-ink-faint">
        {label}
      </div>
      <div className="tnum mt-0.5 text-[13px] text-ink-muted">{fmtBps(value)}</div>
    </div>
  );
}
