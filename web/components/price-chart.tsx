import type { RunConfig, TickEvent } from "@/lib/types";
import { fmtPrice, fmtPct } from "@/lib/utils";

const W = 1000;
const H = 340;
const PAD = { top: 18, right: 16, bottom: 22, left: 46 };

interface Band {
  start: number;
  end: number;
}

function haltBands(ticks: TickEvent[]): Band[] {
  const bands: Band[] = [];
  let open: number | null = null;
  for (const t of ticks) {
    if (t.halted && open === null) open = t.tick;
    if (!t.halted && open !== null) {
      bands.push({ start: open, end: t.tick });
      open = null;
    }
  }
  if (open !== null) bands.push({ start: open, end: ticks[ticks.length - 1].tick });
  return bands;
}

export function PriceChart({
  ticks,
  config,
  totalTicks,
}: {
  ticks: TickEvent[];
  config: RunConfig | null;
  totalTicks: number;
}) {
  const arrival = config?.position.arrival_price ?? config?.instrument.reference_price ?? 100;
  const symbol = config?.instrument.symbol ?? "—";

  const prices = ticks.map((t) => t.last_price);
  const lo = Math.min(arrival, ...(prices.length ? prices : [arrival])) * 0.985;
  const hi = Math.max(arrival, ...(prices.length ? prices : [arrival])) * 1.01;
  const span = Math.max(hi - lo, 1e-6);
  const lastTick = ticks.length ? ticks[ticks.length - 1].tick : 0;
  const maxX = Math.max(totalTicks - 1, lastTick, 1);

  const x = (tick: number) => PAD.left + (tick / maxX) * (W - PAD.left - PAD.right);
  const y = (price: number) => PAD.top + (1 - (price - lo) / span) * (H - PAD.top - PAD.bottom);

  const path = ticks.map((t, i) => `${i === 0 ? "M" : "L"}${x(t.tick).toFixed(1)},${y(t.last_price).toFixed(1)}`).join(" ");
  const area =
    ticks.length > 1
      ? `${path} L${x(lastTick).toFixed(1)},${(H - PAD.bottom).toFixed(1)} L${x(0).toFixed(1)},${(H - PAD.bottom).toFixed(1)} Z`
      : "";

  const last = ticks[ticks.length - 1];
  const drop = last ? (arrival - last.last_price) / arrival : 0;
  const bands = haltBands(ticks);
  const shocks = ticks.filter((t) => t.shock_applied);

  const gridPrices = [hi, arrival, lo + span * 0.0].filter((v, i, a) => a.indexOf(v) === i);

  return (
    <div className="relative">
      <div className="flex items-end justify-between px-1 pb-2">
        <div className="flex items-baseline gap-2.5">
          <span className="tnum text-[26px] leading-none text-ink">
            {last ? fmtPrice(last.last_price) : fmtPrice(arrival)}
          </span>
          <span
            className="tnum text-[13px]"
            style={{ color: drop > 0 ? "var(--color-sell)" : "var(--color-ink-muted)" }}
          >
            {drop > 0 ? "▼" : ""} {fmtPct(Math.abs(drop), 1)}
          </span>
        </div>
        <div className="tnum text-[11px] text-ink-faint">
          {symbol} · tick {last?.tick ?? 0}/{Math.max(totalTicks - 1, 0)}
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="h-[340px] w-full" preserveAspectRatio="none">
        <defs>
          <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-sell)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--color-sell)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* gridlines */}
        {gridPrices.map((p, i) => (
          <g key={i}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(p)}
              y2={y(p)}
              stroke="var(--color-line)"
              strokeWidth={1}
              strokeDasharray={p === arrival ? "4 4" : undefined}
              opacity={p === arrival ? 0.9 : 0.5}
            />
            <text x={6} y={y(p) + 3.5} fill="var(--color-ink-faint)" fontSize={11} className="tnum">
              {fmtPrice(p, 0)}
            </text>
          </g>
        ))}
        <text x={PAD.left + 4} y={y(arrival) - 5} fill="var(--color-ink-faint)" fontSize={10}>
          arrival
        </text>

        {/* halt bands */}
        {bands.map((b, i) => (
          <g key={`h${i}`}>
            <rect
              x={x(b.start)}
              y={PAD.top}
              width={Math.max(x(b.end) - x(b.start), 3)}
              height={H - PAD.top - PAD.bottom}
              fill="var(--color-halt)"
              opacity={0.1}
            />
            <line
              x1={x(b.start)}
              x2={x(b.start)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              stroke="var(--color-halt)"
              strokeWidth={1}
              opacity={0.5}
            />
          </g>
        ))}

        {/* shock markers */}
        {shocks.map((t, i) => (
          <g key={`s${i}`}>
            <line
              x1={x(t.tick)}
              x2={x(t.tick)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              stroke="var(--color-ink-faint)"
              strokeWidth={1}
              strokeDasharray="2 3"
              opacity={0.45}
            />
            <circle cx={x(t.tick)} cy={PAD.top + 2} r={2.5} fill="var(--color-ink-muted)" />
          </g>
        ))}

        {area ? <path d={area} fill="url(#priceFill)" /> : null}
        {path ? (
          <path d={path} fill="none" stroke="var(--color-sell)" strokeWidth={1.8} strokeLinejoin="round" />
        ) : null}

        {last ? (
          <g>
            <circle cx={x(last.tick)} cy={y(last.last_price)} r={3.5} fill="var(--color-sell)" />
            <circle
              cx={x(last.tick)}
              cy={y(last.last_price)}
              r={6.5}
              fill="none"
              stroke="var(--color-sell)"
              strokeWidth={1}
              opacity={0.4}
            />
          </g>
        ) : null}
      </svg>
    </div>
  );
}
