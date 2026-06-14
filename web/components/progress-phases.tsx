import { Check, Loader2 } from "lucide-react";
import type { RunState } from "@/lib/useRun";
import { cn } from "@/lib/utils";

const PHASES = [
  "Evidence",
  "Assumptions",
  "Simulation",
  "Analysis",
] as const;

type Phase = (typeof PHASES)[number];

export function ProgressPhases({ state }: { state: RunState }) {
  if (state.status === "idle") return null;

  const active = activePhase(state.message, state);
  return (
    <div className="fadeup rounded-[var(--radius)] border border-line bg-surface/70 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[12px] font-medium uppercase tracking-[0.12em] text-ink-muted">
            Run progress
          </p>
          <p className="mt-1 text-[11.5px] text-ink-faint">
            {state.message ?? progressCopy(state)}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {PHASES.map((phase) => {
            const done = phaseDone(phase, state);
            const current = active === phase && !done && state.status === "running";
            return (
              <span
                key={phase}
                className={cn(
                  "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11px]",
                  done
                    ? "border-buy/30 bg-buy/10 text-buy"
                    : current
                      ? "border-accent/30 bg-accent/10 text-accent"
                      : "border-line bg-surface-2 text-ink-faint",
                )}
              >
                {done ? (
                  <Check className="h-3 w-3" />
                ) : current ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : null}
                {phase}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function activePhase(message: string | null, state: RunState): Phase {
  const text = (message ?? "").toLowerCase();
  if (text.includes("evidence") || text.includes("market data")) return "Evidence";
  if (text.includes("assumption") || text.includes("gemini")) return "Assumptions";
  if (text.includes("ensemble") || text.includes("simulation")) return "Simulation";
  if (state.metrics && !state.analysis) return "Analysis";
  return state.ticks.length > 0 ? "Simulation" : "Evidence";
}

function phaseDone(phase: Phase, state: RunState): boolean {
  if (state.status === "done") return true;
  if (phase === "Evidence") return Boolean(state.config);
  if (phase === "Assumptions") return state.ticks.length > 0 || Boolean(state.ensemble);
  if (phase === "Simulation") return Boolean(state.metrics);
  return Boolean(state.analysis);
}

function progressCopy(state: RunState): string {
  if (state.status === "connecting") return "Opening the gateway socket.";
  if (state.status === "error") return "The run stopped before a complete result arrived.";
  if (state.status === "done") return "Complete. The selected path and result bands are ready.";
  return "Working through evidence, assumptions, simulation, and analysis.";
}
