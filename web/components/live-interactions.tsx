import type { TickEvent } from "@/lib/types";
import { OrderBook } from "@/components/order-book";
import { CascadeFlow } from "@/components/cascade-flow";

/**
 * One bounded live panel that streams, as the run executes, the two things that
 * make the cascade legible: the order book draining (top) and the per-tick seller
 * surges by investor type (bottom). Both read the existing WebSocket tick stream.
 */
export function LiveInteractions({
  ticks,
  totalTicks,
}: {
  ticks: TickEvent[];
  totalTicks: number;
}) {
  return (
    <div>
      <div className="px-4 pt-1 pb-1 text-[11px] uppercase tracking-[0.12em] text-ink-faint">
        Order book — buy-side liquidity draining
      </div>
      <OrderBook ticks={ticks} />
      <div className="mx-4 h-px bg-line" />
      <div className="px-4 pt-3 pb-0.5 text-[11px] uppercase tracking-[0.12em] text-ink-faint">
        Seller surges — who is hitting the book each tick
      </div>
      <CascadeFlow ticks={ticks} totalTicks={totalTicks} />
    </div>
  );
}
