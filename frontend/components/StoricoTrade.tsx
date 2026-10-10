"use client";

/** Storico dei trade chiusi con filtri: conto, esito, strumento. */

import { useMemo, useState } from "react";

import { erre, nomeStrumento, prezzo, quando, soldi, tonoValore } from "@/lib/format";
import type { Trade } from "@/types/dashboard";

const MOTIVI: Record<string, string> = {
  TP: "Target", SL: "Stop", TRAIL: "Stop mobile", INVALIDATION: "Invalidata", TIME: "Scaduta", MANUAL: "Manuale",
};

export function StoricoTrade({ trades, valuta }: { trades: Trade[]; valuta: string }) {
  const [conto, setConto] = useState("tutti");
  const [esito, setEsito] = useState("tutti");
  const [testo, setTesto] = useState("");
  const filtrati = useMemo(
    () =>
      trades.filter(
        (t) =>
          (conto === "tutti" || t.plan.book === conto) &&
          (esito === "tutti" || (esito === "vinti" ? t.net_pnl > 0 : t.net_pnl <= 0)) &&
          (!testo || `${t.plan.canonical} ${nomeStrumento(t.plan.canonical)}`.toLowerCase().includes(testo.toLowerCase())),
      ),
    [trades, conto, esito, testo],
  );
  const totale = filtrati.reduce((s, t) => s + t.net_pnl, 0);
  const totaleR = filtrati.reduce((s, t) => s + t.r_multiple, 0);

  if (!trades.length) {
    return <p className="py-6 text-center text-sm text-zinc-500">Ancora nessun trade chiuso.</p>;
  }
  const scelta = "rounded-lg border border-zinc-300 bg-white px-2.5 py-1.5 text-sm";
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select value={conto} onChange={(e) => setConto(e.target.value)} className={scelta} aria-label="Conto">
          <option value="tutti">Tutti i conti</option>
          <option value="AI">AI</option>
          <option value="QUANT">QUANT</option>
        </select>
        <select value={esito} onChange={(e) => setEsito(e.target.value)} className={scelta} aria-label="Esito">
          <option value="tutti">Tutti gli esiti</option>
          <option value="vinti">In profitto</option>
          <option value="persi">In perdita</option>
        </select>
        <input value={testo} onChange={(e) => setTesto(e.target.value)} placeholder="Strumento…" className={`${scelta} w-32`} />
        <span className="ml-auto text-sm text-zinc-600">
          {filtrati.length} trade · <b className={tonoValore(totale)}>{soldi(totale, valuta, true)}</b> ·{" "}
          <b className={tonoValore(totaleR)}>{erre(totaleR)}</b>
        </span>
      </div>
      <div className="overflow-x-auto rounded-lg border border-zinc-200">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-zinc-50 text-left text-xs font-medium text-zinc-500">
            <tr>
              <th className="px-3 py-2">Chiuso</th><th className="px-3 py-2">Strumento</th><th className="px-3 py-2">Conto</th>
              <th className="px-3 py-2">Ingresso → uscita</th><th className="px-3 py-2">Motivo</th>
              <th className="px-3 py-2 text-right">Risultato</th><th className="px-3 py-2 text-right">R</th>
              <th className="px-3 py-2 text-right">Costi</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {filtrati.map((t) => (
              <tr key={`${t.plan.book}-${t.position_id}`} className="hover:bg-zinc-50">
                <td className="px-3 py-2 text-zinc-600">{quando(t.closed_at)}</td>
                <td className="px-3 py-2 font-medium">
                  {nomeStrumento(t.plan.canonical)} <span className="text-xs text-zinc-500">{t.plan.direction === "LONG" ? "acquisto" : "vendita"}</span>
                </td>
                <td className="px-3 py-2 text-xs">{t.plan.book}</td>
                <td className="px-3 py-2 text-zinc-600">{prezzo(t.entry_fill)} → {prezzo(t.exit_price)}</td>
                <td className="px-3 py-2">
                  {MOTIVI[t.exit_reason] ?? t.exit_reason}
                  {t.ambiguous_bar && <span title="Stop e target nella stessa candela: contato come stop (prudente)"> ⚠</span>}
                </td>
                <td className={`px-3 py-2 text-right font-medium ${tonoValore(t.net_pnl)}`}>{soldi(t.net_pnl, valuta, true)}</td>
                <td className={`px-3 py-2 text-right ${tonoValore(t.r_multiple)}`}>{erre(t.r_multiple)}</td>
                <td className="px-3 py-2 text-right text-xs text-zinc-500" title="spread + slittamento + commissioni + swap">
                  {soldi(t.spread_cost + t.slippage_cost + t.commission - t.swap, valuta)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
