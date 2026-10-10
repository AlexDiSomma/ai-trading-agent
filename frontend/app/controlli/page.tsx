/** Rischio e sicurezza: fermare tutto, mettere in pausa, cambiare i limiti, gate LIVE. */

import { BackendGiu, BarraStato } from "@/components/BarraStato";
import { Azione } from "@/components/interattivi";
import { ModuloRischio } from "@/components/ModuloRischio";
import { Esci } from "@/components/Esci";
import { SceltaTema } from "@/components/Tema";
import { Badge, Card, TitoloPagina, tonoLibro } from "@/components/ui";
import { carica } from "@/lib/carica";
import { ETICHETTE_RISCHIO, fa, quando } from "@/lib/format";

export const dynamic = "force-dynamic";

const NOMI_GATE: Record<string, string> = {
  paper_trades: "Almeno 30 trade paper validati",
  paper_cycle: "Nessun errore critico nel ciclo paper",
  persistence: "Database PostgreSQL attivo",
  mt5: "MetaTrader 5 sano, trading consentito",
  risk_engine: "Controllo di rischio sul conto reale",
  kill_switch_tested: "Kill switch provato negli ultimi 7 giorni",
  circuit_breaker: "Circuit breaker chiuso",
  no_unknown_orders: "Nessun ordine con esito sconosciuto",
  configuration: "Modalità LIVE abilitata nel .env",
  user_confirmation: "Tua conferma scritta",
};

export default async function Controlli() {
  const { d, errore } = await carica();
  if (!d) return <BackendGiu errore={errore} />;
  const c = d.controls;
  const ks = c?.kill_switch;
  const live = d.live;
  return (
    <main>
      <BarraStato d={d} />
      <TitoloPagina titolo="Rischio e sicurezza" descrizione="Qui fermi il sistema e decidi quanto può rischiare. Ogni azione è registrata." />

      <div className="grid gap-4 lg:grid-cols-2">
        <section className={`rounded-xl border p-5 shadow-sm ${ks?.engaged ? "border-rose-300 bg-rose-50" : "border-zinc-200 bg-white"}`}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold">Kill switch globale</h2>
              <p className="mt-1 text-sm text-zinc-600">
                {ks?.engaged
                  ? `Attivo ${fa(ks.since)}: nessuna nuova apertura, su nessun conto. ${ks.reason ?? ""}`
                  : "Blocca subito ogni nuova apertura, paper e reale. Le posizioni aperte restano gestite con i loro stop."}
              </p>
            </div>
            <Badge tono={ks?.engaged ? "rosso" : "verde"} punto>{ks?.engaged ? "ATTIVO" : "spento"}</Badge>
          </div>
          <div className="mt-4">
            {ks?.engaged ? (
              <Azione path="controls/kill-switch" body={{ engage: false, reason: "Rilasciato dalla dashboard" }} variante="secondario"
                conferma={{ titolo: "Rilasciare il kill switch?", testo: "Il sistema potrà di nuovo aprire posizioni.", bottone: "Rilascia" }}
                okMessaggio="Kill switch rilasciato.">
                Rilascia
              </Azione>
            ) : (
              <Azione path="controls/kill-switch" body={{ engage: true, reason: "Attivato dalla dashboard" }} variante="pericolo"
                conferma={{ titolo: "Attivare il kill switch?", testo: "Nessuna nuova posizione verrà aperta finché non lo rilasci. Per chiudere anche le posizioni aperte usa «Chiudi tutto» nella pagina Posizioni.", bottone: "Attiva kill switch", pericolosa: true }}
                okMessaggio="Kill switch attivo: nessuna nuova apertura.">
                Ferma tutto
              </Azione>
            )}
          </div>
        </section>

        <section className="rounded-xl border border-zinc-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold">Pausa</h2>
              <p className="mt-1 text-sm text-zinc-600">
                {c?.paused
                  ? "Il sistema continua ad analizzare e a gestire le posizioni, ma non ne apre di nuove."
                  : "Sospende le nuove aperture senza fermare analisi e gestione. Utile quando vuoi solo osservare."}
              </p>
            </div>
            <Badge tono={c?.paused ? "giallo" : "verde"} punto>{c?.paused ? "in pausa" : "attivo"}</Badge>
          </div>
          <div className="mt-4">
            <Azione path="controls/pause" body={{ paused: !c?.paused }} variante={c?.paused ? "primario" : "secondario"}
              okMessaggio={c?.paused ? "Ripreso: il sistema può aprire posizioni." : "In pausa: nessuna nuova apertura."}>
              {c?.paused ? "Riprendi" : "Metti in pausa"}
            </Azione>
          </div>
        </section>
      </div>

      {c && "quant_target_r" in c && (
        <div className="mt-4">
          <Card
            titolo="Target di QUANT"
            sottotitolo="Dove QUANT prende il profitto, in multipli del rischio. Vale per le nuove aperture; le statistiche restano separate per target."
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-sm font-medium">
                Attuale <Badge tono={c.quant_target_r ? "giallo" : "grigio"} punto>{c.quant_target_r ? `${String(c.quant_target_r).replace(".", ",")}R` : "2R (standard)"}</Badge>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {[null, 1.5, 1.3].filter((v) => v !== (c.quant_target_r ?? null)).map((v) => (
                  <Azione
                    key={String(v)}
                    path="controls/quant-target"
                    body={{ target_r: v }}
                    piccolo
                    okMessaggio={`Target QUANT: ${v ? String(v).replace(".", ",") + "R" : "2R standard"} per le nuove aperture.`}
                  >
                    {v ? `${String(v).replace(".", ",")}R` : "2R standard"}
                  </Azione>
                ))}
              </div>
            </div>
            <p className="mt-3 text-xs text-zinc-500">
              Backtest 16 mesi: 2R −0,049R per trade, 1,3R −0,028R (vince il 44% invece del 36%, ma incassa meno). Nessuno dei due è positivo.
            </p>
          </Card>
        </div>
      )}

      {c?.inverted && (
        <div className="mt-4">
          <Card
            titolo="Segnali invertiti (esperimento)"
            sottotitolo="Quando il conto vorrebbe comprare vende, e viceversa; stop e target specchiati. Vale per le nuove aperture."
          >
            <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
              Nel backtest di 16 mesi QUANT invertito ha fatto peggio dell&apos;originale (−0,086R per trade contro −0,049R):
              lo spread si paga in entrambi i versi. I trade invertiti sono contati a parte nelle statistiche.
            </p>
            <div className="space-y-3">
              {(["QUANT", "AI"] as const).map((libro) => {
                const attivo = c.inverted?.[libro] ?? false;
                return (
                  <div key={libro} className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      Conto <Badge tono={tonoLibro(libro)}>{libro}</Badge>
                      <Badge tono={attivo ? "giallo" : "grigio"} punto>{attivo ? "invertito" : "normale"}</Badge>
                    </div>
                    {attivo ? (
                      <Azione path="controls/invert" body={{ book: libro, enabled: false }} piccolo okMessaggio={`Segnali ${libro} di nuovo normali.`}>
                        Torna normale
                      </Azione>
                    ) : (
                      <Azione
                        path="controls/invert"
                        body={{ book: libro, enabled: true }}
                        piccolo
                        conferma={{
                          titolo: `Invertire i segnali del conto ${libro}?`,
                          testo: "Ogni nuova apertura andrà nel verso opposto a quello scelto dalla strategia. Le posizioni già aperte non cambiano.",
                          bottone: "Inverti",
                        }}
                        okMessaggio={`Segnali ${libro} invertiti per le nuove aperture.`}
                      >
                        Inverti
                      </Azione>
                    )}
                  </div>
                );
              })}
            </div>
          </Card>
        </div>
      )}

      {c?.trailing && (
        <div className="mt-4">
          <Card
            titolo="Stop mobile"
            sottotitolo="Sposta lo stop quando il trade va in guadagno. Spento di default: la misura in ombra, nella pagina Performance, dice se conviene."
          >
            <div className="space-y-4">
              {(["QUANT", "AI"] as const).map((libro) => {
                const attuale = c.trailing?.books[libro] ?? null;
                const regola = c.trailing?.rules.find((r) => r.id === attuale);
                return (
                  <div key={libro} className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        Conto <Badge tono={tonoLibro(libro)}>{libro}</Badge>
                        <Badge tono={regola ? "verde" : "grigio"} punto>{regola ? regola.name : "spento"}</Badge>
                      </div>
                      <p className="mt-0.5 text-xs text-zinc-500">{regola ? regola.description : "Stop fisso: si esce solo a stop, target, invalidazione o scadenza."}</p>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {attuale !== null && (
                        <Azione path="controls/trailing" body={{ book: libro, rule: null }} piccolo okMessaggio={`Stop mobile ${libro} spento per le nuove posizioni.`}>
                          Spegni
                        </Azione>
                      )}
                      {c.trailing?.rules
                        .filter((r) => r.id !== attuale)
                        .map((r) => (
                          <Azione
                            key={r.id}
                            path="controls/trailing"
                            body={{ book: libro, rule: r.id }}
                            piccolo
                            conferma={{
                              titolo: `Usare «${r.name}» sul conto ${libro}?`,
                              testo: `${r.description} Vale per le posizioni aperte da ora in poi; quelle già aperte restano come sono. I trade con lo stop mobile sono contati a parte nelle statistiche.`,
                              bottone: "Attiva",
                            }}
                            okMessaggio={`${r.name} attivo sul conto ${libro} per le nuove posizioni.`}
                          >
                            {r.name}
                          </Azione>
                        ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>
      )}

      <div className="mt-4">
        <Card titolo="Preferenze" sottotitolo="Valgono solo per questo dispositivo">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-sm font-medium">Tema</div>
              <p className="text-xs text-zinc-500">Chiaro, scuro o automatico come il sistema operativo.</p>
            </div>
            <SceltaTema />
          </div>
          <div className="mt-4 flex items-center justify-between border-t border-zinc-100 pt-4">
            <div>
              <div className="text-sm font-medium">Sessione</div>
              <p className="text-xs text-zinc-500">Esci da questo dispositivo. Per cambiare password: npm run imposta-password.</p>
            </div>
            <Esci className="rounded-lg px-3 py-2 ring-1 ring-inset ring-zinc-300" />
          </div>
        </Card>
      </div>

      <div className="mt-4">
        <Card titolo="Limiti di rischio" sottotitolo="Valgono per entrambi i conti. Hanno tetti di sicurezza che da qui non si possono superare.">
          {c ? <ModuloRischio campi={c.risk.editable} bloccato={c.risk.live_locked} /> : <p className="text-sm text-zinc-500">Scheduler non attivo.</p>}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card titolo="Registro delle azioni" sottotitolo="Comandi e modifiche ai limiti">
          <ul className="divide-y divide-zinc-100 text-sm">
            {[
              ...(c?.risk.audit ?? []).map((a) => ({ at: a.at, testo: `${ETICHETTE_RISCHIO[a.field]?.label ?? a.field}: ${String(a.old ?? "nessuno")} → ${String(a.new ?? "nessuno")} · ${a.reason}` })),
              ...(c?.log ?? []).map((l) => ({ at: l.at, testo: l.action })),
            ]
              .sort((x, y) => y.at.localeCompare(x.at))
              .slice(0, 15)
              .map((r, i) => (
                <li key={i} className="flex justify-between gap-3 py-2">
                  <span>{r.testo}</span>
                  <span className="shrink-0 text-xs text-zinc-400">{quando(r.at)}</span>
                </li>
              ))}
            {!c?.risk.audit.length && !c?.log.length && <li className="py-2 text-zinc-500">Nessuna azione registrata.</li>}
          </ul>
        </Card>
        <Card
          titolo="Passaggio al conto reale"
          sottotitolo="Il LIVE si attiva solo con tutte le condizioni vere insieme"
          azioni={<Azione path="live/drills/kill-switch" piccolo okMessaggio="Prova del kill switch superata." campoRisultato="passed">Prova kill switch</Azione>}
        >
          {!live?.readiness ? (
            <p className="text-sm text-zinc-500">Valutazione in corso…</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {live.readiness.gates.map((g) => (
                <li key={g.name} className="flex gap-2">
                  <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] text-white ${g.ok ? "bg-emerald-500" : "bg-zinc-300"}`}>{g.ok ? "✓" : ""}</span>
                  <div>
                    <div className={g.ok ? "text-zinc-800" : "text-zinc-600"}>{NOMI_GATE[g.name] ?? g.name}</div>
                    <div className="text-xs text-zinc-500">{g.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </main>
  );
}
