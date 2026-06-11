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
  caption,
  right,
  className,
}: {
  title: string;
  hint?: string;
  /** One plain sentence: what this panel shows and why it matters. Wraps. */
  caption?: string;
  right?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("px-4 pt-3.5 pb-2", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[12px] font-medium uppercase tracking-[0.13em] text-ink-muted">
            {title}
          </h2>
          {hint ? <p className="mt-0.5 truncate text-[11px] text-ink-faint">{hint}</p> : null}
        </div>
        {right ? <div className="shrink-0">{right}</div> : null}
      </div>
      {caption ? (
        <p className="mt-1.5 max-w-prose text-[11.5px] leading-relaxed text-ink-faint">{caption}</p>
      ) : null}
    </div>
  );
}
