import type { SourcedInput } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { fmtInt, fmtPct, fmtPrice } from "@/lib/utils";

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-wider text-ink-faint">{label}</div>
      <div className="tnum mt-0.5 text-[13.5px] text-ink">{value}</div>
    </div>
  );
}

export function SourcedInputs({
  data,
  loading,
}: {
  data: SourcedInput | null;
  loading: boolean;
}) {
  if (loading && !data) {
    return (
      <div className="grid grid-cols-2 gap-3 px-4 pb-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-9 rounded bg-surface-2" />
        ))}
      </div>
    );
  }
  if (!data) {
    return <p className="px-4 pb-4 text-[12px] text-ink-faint">No sourced data for this run.</p>;
  }

  const live = data.source === "alphavantage";
  const curated = data.source === "curated";
  const sourceLabel = live
    ? "Live data · Alpha Vantage"
    : curated
      ? "Saved reference"
      : "Stand-in data";
  return (
    <div className="space-y-3 px-4 pb-4">
      <div className="flex items-center justify-between gap-2">
        <span className="tnum text-[15px] text-ink">
          {data.symbol}
          {data.name ? <span className="ml-1.5 text-[12px] text-ink-faint">{data.name}</span> : null}
        </span>
        <Badge tone={live ? "buy" : curated ? "accent" : "neutral"}>{sourceLabel}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Field label="Price" value={fmtPrice(data.reference_price)} />
        <Field label="Avg daily volume" value={`${fmtInt(data.adv)} shares`} />
        <Field
          label="Daily volatility"
          value={data.realized_vol_daily != null ? fmtPct(data.realized_vol_daily, 1) : "n/a"}
        />
        <Field label="Shares tradable" value={`${fmtInt(data.free_float)} shares`} />
      </div>
      <p className="text-[10.5px] text-ink-faint">
        {data.window?.start && data.window?.end
          ? `Covers ${data.window.start} to ${data.window.end} (${data.bars} trading days)`
          : curated
            ? "A representative reference for this episode, not a live quote."
            : "Stand-in numbers. No live data feed is configured."}
      </p>
    </div>
  );
}
