"use client";

/**
 * Modifica dei limiti di rischio. Le modifiche si vedono come differenze prima della
 * conferma, servono un motivo e una conferma esplicita; i valori fuori dai tetti di
 * sicurezza vengono segnalati subito e rifiutati dal backend.
 */

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";

import { Conferma, eseguiAzione, useNotifica } from "@/components/interattivi";
import { ETICHETTE_RISCHIO } from "@/lib/format";
import type { Comandi } from "@/types/dashboard";

type Campi = Comandi["risk"]["editable"];

export function ModuloRischio({ campi, bloccato }: { campi: Campi; bloccato: boolean }) {
  const router = useRouter();
  const notifica = useNotifica();
  const iniziali = useMemo(
    () => Object.fromEntries(Object.entries(campi).map(([k, v]) => [k, v.value === null ? "" : String(v.value)])),
    [campi],
  );
  const [valori, setValori] = useState<Record<string, string>>(iniziali);
  const [motivo, setMotivo] = useState("");
  const [aperta, setAperta] = useState(false);
  const [inCorso, avvia] = useTransition();

  const modifiche = Object.entries(valori).filter(([k, v]) => v !== iniziali[k]);
  const errori: Record<string, string> = {};
  for (const [k, v] of Object.entries(valori)) {
    const c = campi[k];
    if (c.min === null || v === "") continue;
    const n = Number(v.replace(",", "."));
    if (Number.isNaN(n)) errori[k] = "Numero non valido";
    else if (n < c.min || n > (c.max ?? Infinity)) errori[k] = `Ammesso fra ${c.min} e ${c.max}`;
  }
  const valido = modifiche.length > 0 && Object.keys(errori).length === 0 && motivo.trim().length >= 3;

  const salva = () => {
    setAperta(false);
    const changes = Object.fromEntries(
      modifiche.map(([k, v]) => [k, campi[k].min === null ? v : v === "" ? null : Number(v.replace(",", "."))]),
    );
    avvia(async () => {
      const r = await eseguiAzione("controls/risk", { changes, reason: motivo, confirm: true });
      if (r.ok) {
        notifica("Limiti aggiornati: valgono dalla prossima decisione.");
        setMotivo("");
        router.refresh();
      } else {
        notifica(r.errore ?? "Modifica rifiutata.", "errore");
      }
    });
  };

  if (bloccato) {
    return <p className="text-sm text-zinc-600">In modalità LIVE i limiti si cambiano solo nel file <code>.env</code>, con riavvio.</p>;
  }
  return (
    <div>
      <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
        {Object.entries(campi).map(([k, c]) => {
          const meta = ETICHETTE_RISCHIO[k] ?? { label: k, hint: "" };
          const cambiato = valori[k] !== iniziali[k];
          return (
            <label key={k} className="block">
              <span className="flex items-center justify-between text-sm font-medium text-zinc-800">
                {meta.label}
                {cambiato && <span className="text-xs font-normal text-amber-700">modificato</span>}
              </span>
              <div className="mt-1 flex items-center gap-2">
                <input
                  value={valori[k]}
                  onChange={(e) => setValori((s) => ({ ...s, [k]: e.target.value }))}
                  inputMode={c.min === null ? "text" : "decimal"}
                  placeholder={k === "daily_profit_target_pct" ? "nessuno" : undefined}
                  className={`w-full rounded-lg border px-3 py-2 text-sm outline-none transition focus:ring-2 ${
                    errori[k] ? "border-rose-300 focus:ring-rose-200" : cambiato ? "border-amber-300 focus:ring-amber-200" : "border-zinc-300 focus:ring-zinc-200"
                  }`}
                />
                {meta.unit && <span className="text-sm text-zinc-500">{meta.unit}</span>}
              </div>
              <span className={`mt-1 block text-xs ${errori[k] ? "text-rose-600" : "text-zinc-500"}`}>
                {errori[k] ?? meta.hint}
                {!errori[k] && c.min !== null ? ` (da ${c.min} a ${c.max})` : ""}
              </span>
            </label>
          );
        })}
      </div>
      <div className="mt-5 flex flex-col gap-3 border-t border-zinc-100 pt-4 sm:flex-row sm:items-end">
        <label className="flex-1">
          <span className="text-sm font-medium text-zinc-800">Motivo della modifica</span>
          <input
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
            placeholder="Es. più posizioni per raccogliere trade in demo"
            className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-zinc-200"
          />
        </label>
        <div className="flex gap-2">
          <button
            onClick={() => { setValori(iniziali); setMotivo(""); }}
            disabled={modifiche.length === 0 || inCorso}
            className="rounded-lg px-3.5 py-2 text-sm font-medium text-zinc-600 hover:bg-zinc-100 disabled:opacity-40"
          >
            Annulla modifiche
          </button>
          <button
            onClick={() => setAperta(true)}
            disabled={!valido || inCorso}
            className="rounded-lg bg-zinc-900 px-3.5 py-2 text-sm font-semibold text-white hover:bg-zinc-800 disabled:opacity-40"
          >
            Salva {modifiche.length > 0 ? `(${modifiche.length})` : ""}
          </button>
        </div>
      </div>
      <Conferma
        aperta={aperta}
        titolo="Confermi i nuovi limiti?"
        testo="Valgono da subito per le nuove decisioni; le posizioni già aperte mantengono stop e target."
        conferma="Applica"
        onConferma={salva}
        onAnnulla={() => setAperta(false)}
      >
        <ul className="mt-3 space-y-1 rounded-lg bg-zinc-50 p-3 text-sm">
          {modifiche.map(([k, v]) => (
            <li key={k} className="flex justify-between gap-3">
              <span>{ETICHETTE_RISCHIO[k]?.label ?? k}</span>
              <span><span className="text-zinc-400 line-through">{iniziali[k] || "nessuno"}</span> → <b>{v || "nessuno"}</b></span>
            </li>
          ))}
        </ul>
      </Conferma>
    </div>
  );
}
