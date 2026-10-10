/**
 * Barra di stato comune a tutte le pagine: il sistema sta lavorando? se no, perché?
 *
 * In cima, sempre visibili, le condizioni che fermano le aperture (kill switch,
 * pausa, errori): sono la prima cosa da sapere.
 */

import { AutoRefresh } from "@/components/AutoRefresh";
import { Azione } from "@/components/interattivi";
import { Badge } from "@/components/ui";
import { fa } from "@/lib/format";
import type { Dashboard } from "@/types/dashboard";

export function BarraStato({ d }: { d: Dashboard }) {
  const s = d.system;
  const ap = s.autopilot;
  const c = d.controls;
  const ks = c?.kill_switch.engaged ?? s.kill_switch.engaged;
  const errori = Object.entries(ap.last_error ?? {});
  const ai = s.ai_health;
  const uso = ai?.usage_today;
  const token = (uso?.input ?? 0) + (uso?.output ?? 0);
  const consumo = uso?.calls ? `oggi ${uso.calls} chiamate, ${token >= 1000 ? `${Math.round(token / 1000)} mila` : token} token` : "nessuna chiamata oggi";
  const stato = !ap.running
    ? { t: "Scheduler fermo", tono: "rosso" as const }
    : ks
      ? { t: "Kill switch attivo", tono: "rosso" as const }
      : c?.paused
        ? { t: "In pausa", tono: "giallo" as const }
        : { t: "Al lavoro", tono: "verde" as const };

  return (
    <div className="mb-5 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-200 bg-white px-4 py-3 shadow-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tono={stato.tono} punto>{stato.t}</Badge>
          <Badge tono={s.broker_connected ? "verde" : "rosso"} punto>XM {s.broker_connected ? "connesso" : "non connesso"}</Badge>
          <Badge tono="blu">PAPER</Badge>
          <Badge tono="grigio">LIVE spento</Badge>
          {ai && ai.ok !== null && (
            <span title={`AI ${consumo}`}>
              <Badge tono={ai.ok ? "verde" : "rosso"} punto>AI {ai.ok ? "attiva" : "ferma"}</Badge>
            </span>
          )}
          <span className="text-xs text-zinc-500">
            ultima scansione {fa(ap.last?.scan)}
            {ap.busy ? ` · in corso: ${ap.busy === "scan" ? "scansione" : ap.busy}` : ""}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <AutoRefresh secondi={15} />
          <Azione path="autopilot/scan" okMessaggio="Scansione avviata: i risultati arrivano in 1-2 minuti." piccolo>
            Scansiona ora
          </Azione>
        </div>
      </div>
      {ks && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <b>Kill switch attivo:</b> nessuna nuova apertura. Le posizioni aperte restano gestite. {c?.kill_switch.reason}
        </div>
      )}
      {c?.paused && !ks && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <b>In pausa:</b> il sistema analizza il mercato e gestisce le posizioni, ma non ne apre di nuove.
        </div>
      )}
      {ai?.ok === false && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <b>AI non disponibile {fa(ai.since)}:</b> {ai.message} Il conto AI non apre nuove posizioni e le notizie
          non vengono classificate; il conto QUANT continua e le posizioni aperte restano protette dai loro stop.
          {ai.kind === "CREDITO" && s.ai_provider === "openai" && (
            <>
              {" "}
              <a className="font-medium underline" href="https://platform.openai.com/settings/organization/billing/" target="_blank" rel="noreferrer">
                Apri la fatturazione OpenAI
              </a>
            </>
          )}
          <span className="block text-xs text-rose-700">Riprova da sola a ogni ciclo · {consumo}</span>
        </div>
      )}
      {errori.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {errori.map(([k, v]) => (
            <div key={k}><b>Problema nel ciclo {k}:</b> {v}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export function BackendGiu({ errore }: { errore: string }) {
  return (
    <div className="mx-auto mt-10 max-w-lg rounded-xl border border-rose-200 bg-white p-6 text-center shadow-sm">
      <h1 className="text-lg font-semibold">Il backend non risponde</h1>
      <p className="mt-2 text-sm text-zinc-600">{errore}</p>
      <p className="mt-3 text-sm text-zinc-600">
        Avvialo con <code className="rounded bg-zinc-100 px-1">avvia.bat</code> nella cartella del progetto. Questa pagina si
        ricarica da sola.
      </p>
      <div className="mt-4"><AutoRefresh secondi={10} /></div>
    </div>
  );
}
