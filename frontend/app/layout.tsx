import type { Metadata, Viewport } from "next";

import { Notifiche } from "@/components/interattivi";
import { Navigazione } from "@/components/Navigazione";
import { SCRIPT_TEMA } from "@/components/Tema";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI Trading Agent",
  description: "Il sistema che lavora: mercato, decisioni, posizioni, rischio.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="it" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: SCRIPT_TEMA }} />
      </head>
      <body className="min-h-screen bg-zinc-50 text-zinc-900 antialiased">
        <Notifiche>
          <Navigazione />
          <div className="pb-20 md:pb-0 md:pl-56">
            <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6">{children}</div>
          </div>
        </Notifiche>
      </body>
    </html>
  );
}
