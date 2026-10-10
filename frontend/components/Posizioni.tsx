/** Posizioni paper aperte: una card per posizione, leggibile anche da telefono. */

import Link from "next/link";

import { Azione } from "@/components/interattivi";
import { Badge, tonoLibro, Vuoto } from "@/components/ui";
import { erre, fa, nomeStrumento, prezzo, quando, soldi, tonoValore } from "@/lib/format";
import type { Posizione } from "@/types/dashboard";

function Barra({ p }: { p: Posizione }) {
  // Dove si trova il prezzo fra stop e target: 0% = stop, 100% = target.
  const e = p.entry_fill ?? p.plan.entry_price;
  const ultimo = p.last_price ?? e;
  const tot = Math.abs(p.plan.target - p.plan.stop) || 1;
  const pos = Math.min(1, Math.max(0, Math.abs(ultimo - p.plan.stop) / tot));
  const ingresso = Math.min(1, Math.max(0, Math.abs(e - p.plan.stop) / tot));
  return (
    <div className="mt-3">
      <div className="relative h-2 rounded-full bg-gradient-to-r from-rose-200 via-zinc-100 to-emerald-200">
        <span className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-zinc-400" style={{ left: `${ingresso * 100}%` }} title="Ingresso" />
        <span className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-zinc-900 shadow" style={{ left: `${pos * 100}%` }} title="Prezzo attuale" />
      </div>
      {p.stop_r != null && (
        <p className="mt-1 text-[11px] font-medium text-emerald-700">
          Stop mobile: spostato a {prezzo(e + (p.plan.direction === "LONG" ? 1 : -1) * p.stop_r * Math.abs(e - p.plan.stop))} ({p.stop_r >= 0 ? "+" : ""}{erre(p.stop_r)})
        </p>
      )}
      <div className="mt-1 flex justify-between text-[11px] text-zinc-500">
        <span>SL {prezzo(p.plan.stop)}</span>
        <span>ingresso {prezzo(e)}</span>
        <span>TP {prezzo(p.plan.target)}</span>
      </div>
    </div>
  );
}

export function ElencoPosizioni({ posizioni, valuta }: { posizioni: Posizione[]; valuta: string }) {
  if (!posizioni.length) {
    return (
      <Vuoto titolo="Nessuna posizione aperta">
        Il sistema apre una posizione solo quando un piano supera tutti i controlli di rischio. Attendere è un esito normale.
      </Vuoto>
    );
  }
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {posizioni.map((p) => {
        const inAttesa = p.status === "PENDING";
        const rCorrente =
          p.unrealized_pnl !== null && p.risk_amount ? p.unrealized_pnl / p.risk_amount : null;
        return (
          <article key={p.position_id} className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold">{nomeStrumento(p.plan.canonical)}</span>
                  <span className="text-xs text-zinc-400">{p.plan.canonical}</span>
                  <Badge tono={p.plan.direction === "LONG" ? "verde" : "rosso"}>{p.plan.direction === "LONG" ? "Acquisto" : "Vendita"}</Badge>
                  <Badge tono={tonoLibro(p.plan.book)}>{p.plan.book}</Badge>
                  {p.trailing_rule && <Badge tono="verde">stop mobile</Badge>}
                </div>
                <p className="mt-1 text-xs text-zinc-500">
                  {inAttesa
                    ? `Ordine ${p.plan.entry_type} a ${prezzo(p.plan.entry_price)} · scade ${quando(p.plan.valid_until)}`
                    : `Aperta ${fa(p.opened_at)} · ${p.volume} lotti · rischio ${soldi(p.risk_amount, valuta)} · RR ${p.plan.rr.toFixed(2)}`}
                </p>
              </div>
              <div className="text-right">
                <div className={`text-lg font-semibold ${tonoValore(p.unrealized_pnl)}`}>
                  {inAttesa ? "in attesa" : soldi(p.unrealized_pnl, valuta, true)}
                </div>
                {!inAttesa && <div className={`text-xs ${tonoValore(rCorrente)}`}>{erre(rCorrente)}</div>}
              </div>
            </div>
            {!inAttesa && <Barra p={p} />}
            {p.plan.reasons[0] && <p className="mt-3 line-clamp-2 text-xs text-zinc-500">{p.plan.reasons[0]}</p>}
            <div className="mt-3 flex items-center justify-between">
              <span className="text-[11px] text-zinc-400">
                chiusura automatica entro {quando(p.plan.expires_at)} ·{" "}
                <Link href={`/grafici?s=${encodeURIComponent(p.plan.broker_symbol)}&tf=M5`} className="font-medium text-zinc-600 underline-offset-2 hover:underline">
                  grafico
                </Link>
              </span>
              <Azione
                path="paper/close"
                body={{ book: p.plan.book, position_id: p.position_id }}
                piccolo
                variante="secondario"
                conferma={{
                  titolo: inAttesa ? `Annullare l'ordine su ${p.plan.canonical}?` : `Chiudere ${p.plan.canonical} adesso?`,
                  testo: inAttesa
                    ? "L'ordine in attesa viene cancellato dal conto paper."
                    : `La posizione si chiude al prezzo di mercato corrente. Risultato attuale: ${soldi(p.unrealized_pnl, valuta, true)}.`,
                  bottone: inAttesa ? "Annulla ordine" : "Chiudi posizione",
                  pericolosa: true,
                }}
                okMessaggio={p.plan.canonical}
                campoRisultato="result"
              >
                {inAttesa ? "Annulla" : "Chiudi"}
              </Azione>
            </div>
          </article>
        );
      })}
    </div>
  );
}
