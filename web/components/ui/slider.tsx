"use client";

import { cn } from "@/lib/utils";

export function Slider({
  label,
  value,
  display,
  min,
  max,
  step = 1,
  onChange,
  accent,
  className,
}: {
  label: string;
  value: number;
  display: string;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  accent?: string;
  className?: string;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex items-baseline justify-between">
        <label className="text-[12px] text-ink-muted">{label}</label>
        <span className="tnum text-[12px] text-ink">{display}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
        style={
          {
            background: `linear-gradient(to right, ${accent ?? "var(--color-accent)"} ${pct}%, var(--color-line-strong) ${pct}%)`,
          } as React.CSSProperties
        }
      />
    </div>
  );
}
