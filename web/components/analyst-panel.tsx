import { Sparkles } from "lucide-react";
import type { RunSource } from "@/lib/types";

export function AnalystPanel({
  analysis,
  source,
  loading,
}: {
  analysis: string | null;
  source: RunSource | null;
  loading: boolean;
}) {
  const label =
    source === "live-gemini"
      ? "Gemini analyst"
      : source === "cached"
        ? "Analyst (recorded)"
        : "Analyst (no AI)";

  // The analyst writes a few sentences joined by double-spaces; split for rhythm.
  const paras = analysis
    ? analysis.split(/\s{2,}/).filter(Boolean)
    : [];

  return (
    <div className="px-4 pb-4">
      <div className="mb-2.5 flex items-center gap-2">
        <Sparkles className="h-3.5 w-3.5 text-accent" strokeWidth={1.8} />
        <span className="text-[11px] uppercase tracking-[0.13em] text-ink-faint">{label}</span>
      </div>
      {loading && !analysis ? (
        <div className="space-y-2">
          {[90, 96, 70].map((w, i) => (
            <div key={i} className="h-3 rounded bg-surface-2" style={{ width: `${w}%` }} />
          ))}
        </div>
      ) : paras.length ? (
        <div className="space-y-2.5">
          {paras.map((p, i) => (
            <p key={i} className="text-[13.5px] leading-relaxed text-ink-muted">
              {p}
            </p>
          ))}
        </div>
      ) : (
        <p className="text-[13px] text-ink-faint">
          The explanation appears here once the run finishes.
        </p>
      )}
    </div>
  );
}
