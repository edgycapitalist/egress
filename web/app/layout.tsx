import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Egress: crisis-exit simulator",
  description:
    "See whether you could sell a position in a market crash before the exit closes. Egress simulates a market of thousands of traders, shows the order book draining in real time, and explains in plain language why the exit held or closed.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
