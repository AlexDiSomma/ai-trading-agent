/**
 * Pagina System Health.
 *
 * È la prima pagina del progetto e non per caso. Risponde a una domanda sola:
 * **il sistema può operare, e se no perché.**
 *
 * Il progetto precedente dava l'impressione di essere rotto solo perché nessuno
 * diceva ad alta voce cosa lo bloccava. Qui i motivi del blocco stanno in cima,
 * scritti per esteso, prima di qualunque dato tecnico.
 */

import { SchedaComponente } from "@/components/SchedaComponente";
import { StatoBadge } from "@/components/StatoBadge";
import { API_URL, ApiError, getSystemHealth } from "@/lib/api";
import type { SystemHealth } from "@/types/health";

// Nessuna cache: uno stato vecchio è peggio di nessuno stato.
export const dynamic = "force-dynamic";

function durata(secondi: number): string {
  if (secondi < 60) return `${Math.round(secondi)} s`;
  if (secondi < 3600) return `${Math.floor(secondi / 60)} min`;
  const ore = Math.floor(secondi / 3600);
  const minuti = Math.floor((secondi % 3600) / 60);
  return `${ore} h ${minuti} min`;
}

export default async function PaginaSystemHealth() {
  let stato: SystemHealth | null = null;
  let erroreDiRete: string | null = null;

  try {
    stato = await getSystemHealth();
  } catch (errore) {
    erroreDiRete =
      errore instanceof ApiError
        ? errore.message
        : `Backend non raggiungibile su ${API_URL}. Avvialo con "uvicorn backend.main:app --reload".`;
  }

  if (!stato) {
    return (
      <main>
        <h1 className="text-2xl font-bold">Stato del sistema</h1>
        <div className="mt-6 rounded-lg border border-red-300 bg-red-50 p-4">
          <h2 className="font-semibold text-red-900">Backend non raggiungibile</h2>
          <p className="mt-1 text-sm text-red-800">{erroreDiRete}</p>
          <p className="mt-3 text-sm text-red-800">
            Finché il backend non risponde, nessuna operazione è possibile: non è
            un problema di questa pagina, è lo stato del sistema.
          </p>
        </div>
      </main>
    );
  }

  const modalitaDeclassata = stato.declared_mode !== stato.effective_mode;

  return (
    <main>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Stato del sistema</h1>
          <p className="text-sm text-zinc-600">
            Versione {stato.version} · ambiente {stato.environment} · attivo da{" "}
            {durata(stato.uptime_seconds)}
          </p>
        </div>
        <StatoBadge stato={stato.status} />
      </header>

      {/* La risposta alla domanda principale, prima di tutto il resto. */}
      <section
        className={`mt-6 rounded-lg border p-4 ${
          stato.can_trade
            ? "border-green-300 bg-green-50"
            : "border-amber-300 bg-amber-50"
        }`}
      >
        <h2 className="font-semibold">
          {stato.can_trade
            ? "Il sistema può operare"
            : "Il sistema NON può operare"}
        </h2>
        {!stato.can_trade && (
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900">
            {stato.blocking_reasons.map((motivo) => (
              <li key={motivo}>{motivo}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="mt-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-zinc-200 bg-white p-4">
          <h2 className="text-sm font-semibold text-zinc-500">Modalità operativa</h2>
          <p className="mt-1 text-xl font-bold">{stato.effective_mode}</p>
          {modalitaDeclassata && (
            <p className="mt-1 text-sm text-amber-800">
              Richiesta {stato.declared_mode}, declassata per sicurezza.
            </p>
          )}
          <p className="mt-2 text-xs text-zinc-500">
            {stato.effective_mode === "LIVE"
              ? "Gli ordini vengono inviati al broker con denaro reale."
              : "Nessun ordine reale viene inviato al broker."}
          </p>
        </div>

        <div className="rounded-lg border border-zinc-200 bg-white p-4">
          <h2 className="text-sm font-semibold text-zinc-500">Kill switch</h2>
          <p className="mt-1 text-xl font-bold">
            {stato.kill_switch_engaged ? "ATTIVO" : "Non attivo"}
          </p>
          {stato.kill_switch_reason && (
            <p className="mt-1 text-sm text-zinc-700">{stato.kill_switch_reason}</p>
          )}
          <p className="mt-2 text-xs text-zinc-500">
            Blocca l&apos;apertura di nuove posizioni. Non chiude quelle aperte.
          </p>
        </div>
      </section>

      {stato.warnings.length > 0 && (
        <section className="mt-6 rounded-lg border border-amber-300 bg-amber-50 p-4">
          <h2 className="font-semibold text-amber-900">Da sapere</h2>
          <p className="mt-1 text-xs text-amber-800">
            Condizioni in cui il sistema funziona ma non fa necessariamente ciò
            che si potrebbe credere.
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900">
            {stato.warnings.map((avviso) => (
              <li key={avviso}>{avviso}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-6">
        <h2 className="text-lg font-semibold">Componenti</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {stato.components.map((componente) => (
            <SchedaComponente key={componente.name} componente={componente} />
          ))}
        </div>
      </section>

      <footer className="mt-8 text-xs text-zinc-500">
        Ultimo controllo: {new Date(stato.checked_at).toLocaleString("it-IT")} ·
        backend: {API_URL}
      </footer>
    </main>
  );
}
