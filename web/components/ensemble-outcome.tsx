import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type {
  EnsembleCaseSummary,
  EnsembleResult,
  MetricBand,
  PeerCrowdingCase,
} from "@/lib/types";
import { cn, fmtBps, fmtInt, fmtPct } from "@/lib/utils";

const CASE_COPY: Record<PeerCrowdingCase, { label: string; caption: string }> = {
  low: { label: "Low crowding", caption: "fewer shared holders" },
  base: { label: "Base crowding", caption: "central assumption" },
  high: { label: "High crowding", caption: "crowded exit stress" },
  custom: { label: "Custom", caption: "selected profile" },
};

export function EnsembleOutcome({
  ensemble,
  selectedCase,
  loadingCase,
  onSelectCase,
}: {
  ensemble: EnsembleResult;
  selectedCase: PeerCrowdingCase;
  loadingCase: PeerCrowdingCase | null;
  onSelectCase: (summary: EnsembleCaseSummary) => void;
}) {
  const bands = ensemble.bands;
  return (
    <div className="space-y-4 border-b border-line px-4 pb-4 pt-1">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Badge tone="neutral">Scenario range, not forecast</Badge>
        <span className="text-[11px] leading-relaxed text-ink-faint">
          Low/base/high cases show explicit assumptions, not a sellability prediction.
        </span>
      </div>

      <div className="grid gap-2 md:grid-cols-3">
        {ensemble.cases.map((summary) => (
          <CaseButton
            key={summary.case}
            summary={summary}
            selected={summary.case === selectedCase}
            loading={summary.case === loadingCase}
            onClick={() => onSelectCase(summary)}
          />
        ))}
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
        <Band label="Fill range" band={bands.fill_rate} format={(v) => fmtPct(v, 0)} />
        <Band label="Stuck range" band={bands.pct_stuck} format={(v) => fmtPct(v, 0)} tone="sell" />
        <Band label="Slippage" band={bands.slippage_bps} format={fmtBps} />
        <Band
          label="Drawdown"
          band={bands.max_drawdown_pct}
          format={(v) => fmtPct(v, 0)}
          tone="sell"
        />
        <Band
          label="Halt prob."
          band={bands.halt_probability}
          format={(v) => fmtPct(v, 0)}
          tone="halt"
        />
        <WorstBand bands={bands} />
      </div>

      <p className="text-[11.5px] leading-relaxed text-ink-faint">
        The cards are deterministic low/base/high peer-crowding cases across fixed seeds. The
        animation and detailed metrics below show the selected representative path, not the whole
        range or a forecast.
      </p>
    </div>
  );
}

function CaseButton({
  summary,
  selected,
  loading,
  onClick,
}: {
  summary: EnsembleCaseSummary;
  selected: boolean;
  loading: boolean;
  onClick: () => void;
}) {
  const metrics = summary.metrics;
  const copy = CASE_COPY[summary.case] ?? CASE_COPY.custom;
  const peer = summary.peer_crowding;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "min-h-[138px] rounded-[8px] border bg-surface-2/40 px-3 py-3 text-left transition-colors",
        selected ? "border-accent/60 bg-accent/10" : "border-line hover:border-line-strong",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-[13px] font-medium text-ink">{copy.label}</p>
          <p className="mt-0.5 text-[11px] text-ink-faint">{copy.caption}</p>
        </div>
        <ChevronRight
          className={cn("h-4 w-4 shrink-0", selected ? "text-accent" : "text-ink-faint")}
        />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2">
        <Mini label="Sold" value={fmtPct(metrics.fill_rate, 0)} tone="buy" />
        <Mini label="Stuck" value={fmtPct(metrics.pct_stuck, 0)} tone="sell" />
        <Mini label="Slippage" value={fmtBps(metrics.slippage_bps)} />
        <Mini label="Seeds" value={summary.seeds.length ? `${summary.seeds.length}` : "-"} />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {peer ? (
          <>
            <Badge tone={peer.evidence_source === "synthetic_assumption" ? "halt" : "accent"}>
              {peer.confidence} confidence
            </Badge>
            <Badge tone="neutral">{fmtInt(peer.peer_fund_count)} peers</Badge>
          </>
        ) : null}
        {loading ? <Badge tone="accent">loading path</Badge> : null}
      </div>
    </button>
  );
}

function Mini({
  label,
  value,
  tone = "ink",
}: {
  label: string;
  value: string;
  tone?: "ink" | "sell" | "buy";
}) {
  const color =
    tone === "sell" ? "text-sell" : tone === "buy" ? "text-buy" : "text-ink-muted";
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.08em] text-ink-faint">{label}</div>
      <div className={cn("tnum mt-0.5 text-[13px]", color)}>{value}</div>
    </div>
  );
}

function Band({
  label,
  band,
  format,
  tone = "ink",
}: {
  label: string;
  band: MetricBand | undefined;
  format: (v: number | null | undefined) => string;
  tone?: "ink" | "sell" | "halt";
}) {
  const color =
    tone === "sell" ? "text-sell" : tone === "halt" ? "text-halt" : "text-ink";
  return (
    <div className="rounded-[8px] border border-line bg-surface-2/35 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.08em] text-ink-faint">{label}</div>
      <div className={cn("tnum mt-1 text-[14px]", color)}>
        {format(band?.low)}-{format(band?.high)}
      </div>
      <div className="tnum mt-0.5 text-[10.5px] text-ink-faint">
        median {format(band?.median)}
      </div>
    </div>
  );
}

function WorstBand({ bands }: { bands: EnsembleResult["bands"] }) {
  const stuck = bands.pct_stuck?.high;
  return (
    <div className="rounded-[8px] border border-sell/25 bg-sell/10 px-3 py-2">
      <div className="text-[10px] uppercase tracking-[0.08em] text-sell">Worst seed</div>
      <div className="tnum mt-1 text-[14px] text-sell">{fmtPct(stuck, 0)} stuck</div>
      <div className="mt-0.5 text-[10.5px] text-ink-faint">highest band edge</div>
    </div>
  );
}
