import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ---- Formatters. A trading tool reads in tabular figures, not prose. ----

export const fmtInt = (n: number | null | undefined) =>
  n == null ? "—" : Math.round(n).toLocaleString("en-US");

export const fmtPrice = (n: number | null | undefined, dp = 2) =>
  n == null ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });

export const fmtPct = (frac: number | null | undefined, dp = 0) =>
  frac == null ? "—" : `${(frac * 100).toFixed(dp)}%`;

export const fmtBps = (n: number | null | undefined) =>
  n == null ? "—" : `${Math.round(n).toLocaleString("en-US")} bps`;

export const fmtCompact = (n: number | null | undefined) => {
  if (n == null) return "—";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return `${Math.round(n)}`;
};

export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
