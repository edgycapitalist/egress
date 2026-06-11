import { cn } from "@/lib/utils";

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-[var(--radius)] border border-line bg-surface/80 backdrop-blur-sm",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({
  title,
  hint,
  right,
  className,
}: {
  title: string;
  hint?: string;
  right?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3 px-4 pt-3.5 pb-2", className)}>
      <div className="min-w-0">
        <h2 className="text-[12px] font-medium uppercase tracking-[0.13em] text-ink-muted">
          {title}
        </h2>
        {hint ? <p className="mt-0.5 truncate text-[11px] text-ink-faint">{hint}</p> : null}
      </div>
      {right ? <div className="shrink-0">{right}</div> : null}
    </div>
  );
}
