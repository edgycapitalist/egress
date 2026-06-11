import type { RunConfig, TickEvent } from "@/lib/types";
import { fmtInt, fmtPct, fmtPrice } from "@/lib/utils";

export function FillProgress({
  ticks,
  config,
}: {
  ticks: TickEvent[];
  config: RunConfig | null;
}) {
  const target = config?.position.quantity ?? 0;
  const last = ticks[ticks.length - 1];
  const filled = last?.cumulative_filled ?? 0;
  const pct = target > 0 ? filled / target : 0;
  const vwap = last?.vwap_sold ?? null;
  const arrival = config?.position.arrival_price ?? null;

  return (
    <div className="space-y-2.5 px-4 pb-4">
      <div className="flex items-baseline justify-between">
        <span className="tnum text-[20px] text-ink">{fmtPct(pct, 0)}</span>
        <span className="tnum text-[12px] text-ink-faint">
          {fmtInt(filled)} / {fmtInt(target)} shares
        </span>
      </div>
      <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-surface-2">
        <div
          className="h-full rounded-full bg-buy transition-all duration-300"
          style={{ width: `${Math.min(pct * 100, 100)}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-[12px]">
        <span className="text-ink-faint">
          VWAP sold <span className="tnum text-ink-muted">{vwap != null ? fmtPrice(vwap) : "—"}</span>
        </span>
        <span className="text-ink-faint">
          vs arrival <span className="tnum text-ink-muted">{arrival != null ? fmtPrice(arrival) : "—"}</span>
        </span>
      </div>
    </div>
  );
}
