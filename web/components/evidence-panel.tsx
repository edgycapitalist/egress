import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type {
  Confidence,
  EvidenceItem,
  EvidenceSource,
  EnsembleResult,
  PeerCrowdingProfile,
  RunConfig,
} from "@/lib/types";
import { fmtPct } from "@/lib/utils";

const SOURCE_LABELS: Record<EvidenceSource, string> = {
  alpha_vantage: "Alpha Vantage",
  sec_edgar: "SEC 13F / EDGAR",
  user_upload: "User upload",
  curated_fixture: "Curated fixture",
  synthetic_assumption: "Synthetic assumption",
  gemini_inference: "Gemini inference",
  none: "No source",
};

export function EvidencePanel({
  config,
  ensemble,
}: {
  config: RunConfig | null;
  ensemble: EnsembleResult | null;
}) {
  const summary = ensemble?.evidence_summary ?? config?.evidence_summary ?? null;
  const peer = config?.peer_crowding ?? firstPeer(ensemble);
  const items = summary?.items ?? [];
  const assumptionLed =
    peer?.evidence_source === "synthetic_assumption" || config?.scenario_mode === "assumption_led";
  const secLookupOnly =
    Boolean(peer) &&
    peer?.evidence_source !== "sec_edgar" &&
    items.some((item) => item.field === "sec_lookup" && item.source === "sec_edgar");

  if (!config && !ensemble) {
    return <p className="px-4 pb-4 text-[12px] text-ink-faint">Evidence labels appear after a run.</p>;
  }

  return (
    <div className="space-y-3 px-4 pb-4">
      {assumptionLed ? (
        <div className="flex items-start gap-2 rounded-[8px] border border-halt/30 bg-halt/10 px-3 py-2 text-[11.5px] leading-relaxed text-halt">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Peer crowding is assumption-led. Treat the result as a transparent stress range, not an
          evidence-backed forecast.
        </div>
      ) : null}

      {secLookupOnly ? (
        <div className="flex items-start gap-2 rounded-[8px] border border-halt/30 bg-halt/10 px-3 py-2 text-[11.5px] leading-relaxed text-halt">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          SEC lookup only; peer assumptions came from curated or synthetic fallback evidence.
        </div>
      ) : null}

      {summary?.summary ? (
        <p className="text-[12px] leading-relaxed text-ink-muted">{summary.summary}</p>
      ) : null}

      {peer ? <PeerBlock peer={peer} /> : null}

      <div className="divide-y divide-line rounded-[8px] border border-line bg-surface-2/35">
        {items.length ? (
          items.map((item, i) => <EvidenceRow key={`${item.field}-${i}`} item={item} />)
        ) : (
          <p className="px-3 py-3 text-[12px] text-ink-faint">
            No explicit evidence ledger was attached to this replay.
          </p>
        )}
      </div>
    </div>
  );
}

function PeerBlock({ peer }: { peer: PeerCrowdingProfile }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <PeerField label="Peer funds" value={`${peer.peer_fund_count}`} />
      <PeerField label="Overlap" value={fmtPct(peer.overlap_pct, 0)} />
      <PeerField label="Avg peer size" value={`${fmtPct(peer.avg_peer_position_pct_adv, 1)} ADV`} />
      <PeerField label="Shared trigger" value={`${fmtPct(peer.shared_trigger_drawdown_pct, 1)} drop`} />
    </div>
  );
}

function PeerField({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[8px] border border-line bg-surface-2/45 px-3 py-2">
      <div className="text-[10.5px] uppercase tracking-[0.1em] text-ink-faint">{label}</div>
      <div className="tnum mt-1 text-[13px] text-ink">{value}</div>
    </div>
  );
}

function EvidenceRow({ item }: { item: EvidenceItem }) {
  const source = SOURCE_LABELS[item.source] ?? item.source;
  return (
    <div className="grid gap-2 px-3 py-2.5 sm:grid-cols-[1fr_auto] sm:items-start">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[12.5px] text-ink">{item.label || item.field}</span>
          <Badge tone={sourceTone(item.source)}>{source}</Badge>
          <Badge tone={confidenceTone(item.confidence)}>{item.confidence} confidence</Badge>
        </div>
        {item.notes ? (
          <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">{item.notes}</p>
        ) : null}
      </div>
      {item.as_of ? (
        <span className="tnum text-[11px] text-ink-faint sm:text-right">{item.as_of}</span>
      ) : null}
    </div>
  );
}

function firstPeer(ensemble: EnsembleResult | null): PeerCrowdingProfile | null {
  return ensemble?.cases.find((c) => c.case === ensemble.representative_case)?.peer_crowding ?? null;
}

function sourceTone(source: EvidenceSource): "neutral" | "sell" | "buy" | "halt" | "accent" {
  if (source === "user_upload" || source === "sec_edgar" || source === "alpha_vantage") return "buy";
  if (source === "curated_fixture" || source === "gemini_inference") return "accent";
  if (source === "synthetic_assumption") return "halt";
  return "neutral";
}

function confidenceTone(confidence: Confidence): "neutral" | "sell" | "buy" | "halt" | "accent" {
  if (confidence === "high") return "buy";
  if (confidence === "medium") return "accent";
  return "halt";
}
