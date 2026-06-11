import { INVESTOR_TYPES, INVESTOR_SHORT, investorColor, type TickEvent } from "@/lib/types";

const W = 1000;
const H = 150;

export function CascadeFlow({ ticks, totalTicks }: { ticks: TickEvent[]; totalTicks: number }) {
  const maxX = Math.max(totalTicks - 1, ticks.length ? ticks[ticks.length - 1].tick : 0, 1);
  const maxTotal = Math.max(
    ...ticks.map((t) => INVESTOR_TYPES.reduce((s, k) => s + (t.actions_by_type[k] ?? 0), 0)),
    1,
  );

  const x = (tick: number) => (tick / maxX) * W;
  const y = (v: number) => H - (v / maxTotal) * H;

  // Build stacked cumulative bands, sellers first (bottom).
  const bands = INVESTOR_TYPES.map((type, idx) => {
    const below = INVESTOR_TYPES.slice(0, idx);
    const top: string[] = [];
    const bottom: string[] = [];
    for (const t of ticks) {
      const base = below.reduce((s, k) => s + (t.actions_by_type[k] ?? 0), 0);
      const val = base + (t.actions_by_type[type] ?? 0);
      top.push(`${x(t.tick).toFixed(1)},${y(val).toFixed(1)}`);
      bottom.push(`${x(t.tick).toFixed(1)},${y(base).toFixed(1)}`);
    }
    const d = `M${top.join(" L")} L${bottom.reverse().join(" L")} Z`;
    return { type, d };
  });

  return (
    <div className="px-4 pb-4">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-[150px] w-full">
        {ticks.length > 1
          ? bands.map((b) => (
              <path key={b.type} d={b.d} fill={investorColor(b.type)} fillOpacity={0.85} />
            ))
          : null}
      </svg>
      <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1.5">
        {INVESTOR_TYPES.map((t) => (
          <div key={t} className="flex items-center gap-1.5">
            <span
              className="h-2 w-2 rounded-[2px]"
              style={{ background: investorColor(t) }}
            />
            <span className="text-[11px] text-ink-muted">{INVESTOR_SHORT[t]}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
