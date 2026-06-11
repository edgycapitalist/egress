import { cn } from "@/lib/utils";

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: React.ReactNode;
  tone?: "neutral" | "sell" | "buy" | "halt" | "accent";
  className?: string;
}) {
  const tones: Record<string, string> = {
    neutral: "border-line-strong bg-surface-2 text-ink-muted",
    sell: "border-sell/30 bg-sell/10 text-sell",
    buy: "border-buy/30 bg-buy/10 text-buy",
    halt: "border-halt/30 bg-halt/10 text-halt",
    accent: "border-accent/30 bg-accent/10 text-accent",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
