/** Mattoni visivi condivisi (componenti server, senza stato). */

import type { ReactNode } from "react";

export type Tono = "verde" | "giallo" | "rosso" | "grigio" | "blu" | "viola";

const TONI: Record<Tono, string> = {
  verde: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  giallo: "bg-amber-50 text-amber-800 ring-amber-600/20",
  rosso: "bg-rose-50 text-rose-800 ring-rose-600/20",
  grigio: "bg-zinc-100 text-zinc-700 ring-zinc-500/20",
  blu: "bg-sky-50 text-sky-800 ring-sky-600/20",
  viola: "bg-violet-50 text-violet-800 ring-violet-600/20",
};

export function Badge({ children, tono = "grigio", punto = false }: { children: ReactNode; tono?: Tono; punto?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONI[tono]}`}>
      {punto && <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export const tonoLibro = (b: string): Tono => (b === "AI" ? "viola" : "blu");
export const tonoQualita = (q: string): Tono =>
  q === "GOOD" ? "verde" : q === "DEGRADED" ? "giallo" : q === "INSUFFICIENT" ? "rosso" : "grigio";
export const tonoRegime = (r: string): Tono =>
  r === "RISK_ON" ? "verde" : r === "RISK_OFF" ? "rosso" : r === "MIXED" || r === "TRANSITION" ? "giallo" : "grigio";

export const NOMI_REGIME: Record<string, string> = {
  RISK_ON: "Propensione al rischio",
  RISK_OFF: "Avversione al rischio",
  MIXED: "Segnali misti",
  TRANSITION: "In transizione",
  EVENT_DRIVEN: "Guidato dagli eventi",
  UNCERTAIN: "Incerto",
};

export function Card({ titolo, sottotitolo, azioni, children, className = "" }: {
  titolo?: ReactNode; sottotitolo?: ReactNode; azioni?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-xl border border-zinc-200 bg-white shadow-sm ${className}`}>
      {(titolo || azioni) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-zinc-100 px-4 py-3">
          <div>
            {titolo && <h2 className="text-sm font-semibold text-zinc-900">{titolo}</h2>}
            {sottotitolo && <p className="mt-0.5 text-xs text-zinc-500">{sottotitolo}</p>}
          </div>
          {azioni && <div className="flex items-center gap-2">{azioni}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ etichetta, valore, nota, tono = "" }: { etichetta: string; valore: ReactNode; nota?: ReactNode; tono?: string }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <div className="text-xs font-medium text-zinc-500">{etichetta}</div>
      <div className={`mt-1 text-2xl font-semibold tracking-tight ${tono}`}>{valore}</div>
      {nota && <div className="mt-1 text-xs text-zinc-500">{nota}</div>}
    </div>
  );
}

export function Vuoto({ titolo, children }: { titolo: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-zinc-200 px-4 py-6 text-center">
      <p className="text-sm font-medium text-zinc-700">{titolo}</p>
      {children && <p className="mx-auto mt-1 max-w-md text-xs text-zinc-500">{children}</p>}
    </div>
  );
}

export function TitoloPagina({ titolo, descrizione, destra }: { titolo: string; descrizione?: string; destra?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-900">{titolo}</h1>
        {descrizione && <p className="mt-1 max-w-2xl text-sm text-zinc-500">{descrizione}</p>}
      </div>
      {destra}
    </div>
  );
}
