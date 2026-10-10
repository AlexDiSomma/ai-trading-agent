/**
 * Indicatore di stato.
 *
 * Il colore non è l'unico segnale: c'è sempre anche la parola. Un pallino
 * rosso e uno verde sono indistinguibili per chi ha una discromatopsia, e
 * questa dashboard serve a decidere se mettere denaro sul mercato.
 */

import type { HealthStatus } from "@/types/health";

const ASPETTO: Record<HealthStatus, { etichetta: string; classi: string }> = {
  HEALTHY: {
    etichetta: "Sano",
    classi: "bg-green-100 text-green-900 border-green-300",
  },
  DEGRADED: {
    etichetta: "Degradato",
    classi: "bg-amber-100 text-amber-900 border-amber-300",
  },
  UNHEALTHY: {
    etichetta: "Fuori uso",
    classi: "bg-red-100 text-red-900 border-red-300",
  },
  UNKNOWN: {
    etichetta: "Sconosciuto",
    classi: "bg-zinc-100 text-zinc-800 border-zinc-300",
  },
};

export function StatoBadge({ stato }: { stato: HealthStatus }) {
  const aspetto = ASPETTO[stato] ?? ASPETTO.UNKNOWN;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold ${aspetto.classi}`}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      {aspetto.etichetta}
    </span>
  );
}
