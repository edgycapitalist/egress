import type { Metrics } from "@/lib/types";
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
    metrics.time_to_exit_ticks != null ? `${metrics.time_to_exit_ticks} ticks` : "never";

  return (
    <div className={cn("grid grid-cols-2 sm:grid-cols-3 divide-x divide-y divide-line")}>
      <Metric
        label="Fill rate"
        value={fmtPct(metrics.fill_rate, 0)}
        sub={`${fmtInt(metrics.filled_qty)} sold`}
        tone={metrics.fill_rate < 0.5 ? "sell" : "buy"}
      />
      <Metric
        label="Left stuck"
        value={fmtPct(metrics.pct_stuck, 0)}
        sub={`${fmtInt(metrics.stuck_qty)} shares`}
        tone={metrics.pct_stuck > 0.3 ? "sell" : "ink"}
      />
      <Metric
        label="Max drawdown"
        value={fmtPct(metrics.max_drawdown_pct, 0)}
        sub={`final ${metrics.final_price.toFixed(2)}`}
        tone="sell"
      />
      <Metric label="Slippage" value={fmtBps(metrics.slippage_bps)} sub="vs arrival" />
      <Metric
        label="Impl. shortfall"
        value={fmtBps(metrics.implementation_shortfall_bps)}
        sub={`VWAP ${metrics.vwap_sold != null ? metrics.vwap_sold.toFixed(2) : "—"}`}
      />
      <Metric
        label="Time to exit"
        value={exited}
        sub={`${metrics.halt_count} halt${metrics.halt_count === 1 ? "" : "s"} · ${metrics.ticks_run} ticks`}
        tone={metrics.halt_triggered ? "halt" : "ink"}
      />
    </div>
  );
}
