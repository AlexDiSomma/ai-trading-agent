/**
 * Riquadro di un singolo componente di sistema.
 *
 * Mostra sempre il *motivo* accanto allo stato: uno stato senza spiegazione
 * obbliga ad aprire i log, e nel momento in cui serve nessuno apre i log.
 */

import { StatoBadge } from "@/components/StatoBadge";
import type { ComponentHealth } from "@/types/health";

export function SchedaComponente({ componente }: { componente: ComponentHealth }) {
  const voci = Object.entries(componente.metadata ?? {}).filter(
    ([, valore]) => valore !== null && valore !== "",
  );

  return (
    <article className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-zinc-900">{componente.name}</h3>
          {!componente.critical && (
            <p className="text-xs text-zinc-500">
              Non critico: un guasto qui non blocca l&apos;operativit&agrave;
            </p>
          )}
        </div>
        <StatoBadge stato={componente.status} />
      </header>

      <p className="mt-2 text-sm text-zinc-700">{componente.detail}</p>

      <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
        {componente.latency_ms !== null && (
          <div className="flex gap-1">
            <dt>Latenza:</dt>
            <dd className="font-mono">{componente.latency_ms} ms</dd>
          </div>
        )}
        {voci.map(([chiave, valore]) => (
          <div key={chiave} className="flex gap-1">
            <dt>{chiave.replaceAll("_", " ")}:</dt>
            <dd className="font-mono">{String(valore)}</dd>
          </div>
        ))}
      </dl>
    </article>
  );
}
