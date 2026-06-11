import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-[8px] text-[13px] font-medium transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:pointer-events-none disabled:opacity-45 select-none",
  {
    variants: {
      variant: {
        primary:
          "bg-ink text-bg hover:bg-white shadow-[0_1px_0_rgba(255,255,255,0.1)_inset]",
        accent: "bg-accent text-white hover:brightness-110",
        outline: "border border-line-strong bg-surface-2 text-ink hover:border-ink-faint hover:bg-surface",
        ghost: "text-ink-muted hover:text-ink hover:bg-surface-2",
      },
      size: {
        sm: "h-8 px-3",
        md: "h-9.5 px-4",
        lg: "h-11 px-5 text-[14px]",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(button({ variant, size }), className)} {...props} />;
}
