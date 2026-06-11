import type { TickEvent } from "@/lib/types";
import { cn, fmtCompact, fmtPrice } from "@/lib/utils";

function Sparkline({ values, color }: { values: number[]; color: string }) {
  if (values.length < 2) return <div className="h-8" />;
  const max = Math.max(...values, 1);
  const w = 100;
  const h = 32;
  const pts = values
    .map((v, i) => `${(i / (values.length - 1)) * w},${h - (v / max) * h}`)
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-8 w-full">
      <polyline points={`0,${h} ${pts} ${w},${h}`} fill={color} fillOpacity={0.12} stroke="none" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function DepthBar({
  label,
  value,
  max,
  color,
  align,
}: {
  label: string;
  value: number;
  max: number;
  color: string;
  align: "left" | "right";
}) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className={cn("space-y-1", align === "right" && "text-right")}>
      <div
        className={cn(
          "flex items-baseline gap-2",
          align === "right" ? "justify-end" : "justify-start",
        )}
      >
        <span className="text-[11px] uppercase tracking-wider text-ink-faint">{label}</span>
        <span className="tnum text-[13px] text-ink">{fmtCompact(value)}</span>
      </div>
      <div className={cn("h-2 w-full overflow-hidden rounded-full bg-surface-2")}>
        <div
          className={cn("h-full rounded-full transition-all duration-300", align === "right" && "ml-auto")}
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
    </div>
  );
}

export function OrderBook({ ticks }: { ticks: TickEvent[] }) {
  const last = ticks[ticks.length - 1];
  const bidHist = ticks.map((t) => t.depth_bid);
  const maxDepth = Math.max(...ticks.map((t) => Math.max(t.depth_bid, t.depth_ask)), 1);

  const bid = last?.best_bid;
  const ask = last?.best_ask;
  const spread = bid != null && ask != null ? ask - bid : null;

  return (
    <div className="space-y-4 px-4 pb-4">
      <div className="flex items-stretch justify-between gap-3 rounded-[8px] border border-line bg-surface-2/60 p-3">
        <div className="space-y-0.5">
          <div className="text-[11px] uppercase tracking-wider text-ink-faint">Best bid</div>
          <div className="tnum text-[16px] text-buy">{bid != null ? fmtPrice(bid) : "—"}</div>
        </div>
        <div className="flex flex-col items-center justify-center">
          <div className="text-[11px] uppercase tracking-wider text-ink-faint">Spread</div>
          <div className="tnum text-[13px] text-ink-muted">
            {spread != null ? fmtPrice(spread) : "no book"}
          </div>
        </div>
        <div className="space-y-0.5 text-right">
          <div className="text-[11px] uppercase tracking-wider text-ink-faint">Best ask</div>
          <div className="tnum text-[16px] text-sell">{ask != null ? fmtPrice(ask) : "—"}</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <DepthBar label="Bid depth" value={last?.depth_bid ?? 0} max={maxDepth} color="var(--color-buy)" align="left" />
        <DepthBar label="Ask depth" value={last?.depth_ask ?? 0} max={maxDepth} color="var(--color-sell)" align="right" />
      </div>

      <div>
        <div className="mb-1 text-[11px] text-ink-faint">Buy-side support draining</div>
        <Sparkline values={bidHist} color="var(--color-buy)" />
      </div>
    </div>
  );
}
