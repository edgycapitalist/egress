import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Egress — crisis-exit simulator",
  description:
    "Simulate how an investment position behaves in a crisis before the money is committed. A market of thousands of agents, a draining order book, and a plain-language explanation of why the exit closed.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
