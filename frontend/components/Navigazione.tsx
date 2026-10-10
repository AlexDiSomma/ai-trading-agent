"use client";

/** Navigazione: barra laterale su desktop, barra in basso su telefono. */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Esci } from "@/components/Esci";
import { SceltaTema } from "@/components/Tema";

const VOCI = [
  { href: "/", label: "Panoramica", icona: "◉" },
  { href: "/posizioni", label: "Posizioni", icona: "⇅" },
  { href: "/grafici", label: "Grafici", icona: "▥" },
  { href: "/mercati", label: "Mercati", icona: "◫" },
  { href: "/analisi", label: "Analisi", icona: "◎" },
  { href: "/notizie", label: "Notizie", icona: "≡" },
  { href: "/performance", label: "Performance", icona: "↗" },
  { href: "/controlli", label: "Rischio", icona: "⛨" },
];

export function Navigazione() {
  const percorso = usePathname();
  if (percorso === "/login") return null;
  const attiva = (href: string) => (href === "/" ? percorso === "/" : percorso.startsWith(href));
  return (
    <>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-56 flex-col border-r border-zinc-200 bg-white md:flex">
        <div className="px-5 py-5">
          <div className="text-sm font-semibold tracking-tight text-zinc-900">AI Trading Agent</div>
          <div className="text-xs text-zinc-500">XM · conto demo</div>
        </div>
        <nav className="flex-1 space-y-0.5 px-3">
          {VOCI.map((v) => (
            <Link
              key={v.href}
              href={v.href}
              className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
                attiva(v.href) ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900"
              }`}
            >
              <span aria-hidden className="w-4 text-center">{v.icona}</span>
              {v.label}
            </Link>
          ))}
        </nav>
        <div className="space-y-3 px-5 py-4 text-xs text-zinc-400">
          <SceltaTema compatta />
          <div className="flex items-center justify-between">
            <Link href="/system-health" className="hover:text-zinc-700">Stato tecnico</Link>
            <Esci />
          </div>
        </div>
      </aside>
      <nav className="fixed inset-x-0 bottom-0 z-30 flex overflow-x-auto border-t border-zinc-200 bg-white/95 backdrop-blur md:hidden">
        {VOCI.map((v) => (
          <Link
            key={v.href}
            href={v.href}
            className={`flex min-w-[64px] flex-1 flex-col items-center gap-0.5 py-2 text-[10px] font-medium ${attiva(v.href) ? "text-zinc-900" : "text-zinc-400"}`}
          >
            <span aria-hidden className="text-base leading-none">{v.icona}</span>
            {v.label}
          </Link>
        ))}
      </nav>
    </>
  );
}
