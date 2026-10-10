/** Mercati e strategie: cosa può operare, cosa ne pensa il sistema, interruttori. */

import Link from "next/link";

import { BackendGiu, BarraStato } from "@/components/BarraStato";
import { Interruttore } from "@/components/interattivi";
import { Badge, Card, TitoloPagina, Vuoto } from "@/components/ui";
import { carica } from "@/lib/carica";
import { fa, nomeStrumento, num } from "@/lib/format";
import type { Opportunita, Tesi } from "@/types/dashboard";

export const dynamic = "force-dynamic";

const STRATEGIE = [
  { id: "AI", nome: "AI", descrizione: "Il modello studia contesto globale e grafici e scrive una tesi: compra, vendi o aspetta. Il codice traduce la tesi in ingresso, stop e target." },
  { id: "QUANT", nome: "QUANT", descrizione: "Regola fissa di confronto: segue la tendenza quando i timeframe sono allineati. Stop a 1,5 volte la volatilità, target a 2 volte il rischio." },
];

function TesiBreve({ t }: { t?: Tesi }) {
  if (!t) return <p className="text-xs text-zinc-400">Nessuna tesi AI ancora (al massimo una all&apos;ora per strumento).</p>;
  const tono = t.decision === "LONG" ? "verde" : t.decision === "SHORT" ? "rosso" : "grigio";
  const etichetta = t.status !== "VALID" ? "Tesi scartata" : t.decision === "LONG" ? "AI: acquisto" : t.decision === "SHORT" ? "AI: vendita" : "AI: aspetta";
  return (
    <details className="group text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-2">
        <Badge tono={t.status !== "VALID" ? "giallo" : tono}>{etichetta}</Badge>
        <span className="text-zinc-400">{fa(t.at)}</span>
        <span className="text-zinc-400 group-open:hidden">· mostra il ragionamento</span>
      </summary>
      <div className="mt-2 space-y-1.5 rounded-lg bg-zinc-50 p-3 text-zinc-700">
        {(t.wait_reason || t.scenario) && <p>{t.wait_reason ?? t.scenario}</p>}
        {t.failure_reasons.length > 0 && <p className="text-amber-700">Scartata perché: {t.failure_reasons.join(" · ")}</p>}
        {t.supporting.map((x, i) => <p key={`s${i}`} className="text-emerald-800">+ {x}</p>)}
        {t.contradictory.map((x, i) => <p key={`c${i}`} className="text-rose-800">− {x}</p>)}
        {t.invalidations.length > 0 && <p className="text-zinc-500">Si annulla se: {t.invalidations.join(" · ")}</p>}
      </div>
    </details>
  );
}

function Riga({ simbolo, attivo, o, tesi }: { simbolo: string; attivo: boolean; o?: Opportunita; tesi?: Tesi }) {
  const stato = !attivo
    ? <Badge>Disattivato da te</Badge>
    : !o
      ? <Badge>In attesa di scansione</Badge>
      : o.tradeable
        ? <Badge tono="verde">Operabile</Badge>
        : <span className="text-xs text-amber-700" title={o.blocking_reasons.join("\n")}>{o.blocking_reasons[0]}</span>;
  return (
    <li className={`flex flex-col gap-3 py-4 sm:flex-row sm:items-start ${attivo ? "" : "opacity-60"}`}>
      <div className="flex items-center gap-3 sm:w-56">
        <Interruttore
          attivo={attivo}
          path="controls/symbols"
          chiave="symbol"
          id={simbolo}
          etichetta={`Opera su ${simbolo}`}
          nome={simbolo}
        />
        <div>
          <div className="font-semibold">{nomeStrumento(o?.canonical ?? simbolo)}</div>
          <div className="text-xs text-zinc-500">
            {o?.canonical ?? simbolo} · {simbolo} ·{" "}
            <Link href={`/grafici?s=${encodeURIComponent(simbolo)}&tf=M15`} className="font-medium text-zinc-600 hover:underline">grafico</Link>
          </div>
        </div>
      </div>
      <div className="grid flex-1 gap-2 sm:grid-cols-[180px_1fr]">
        <div className="space-y-1 text-xs">
          {o ? (
            <>
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-20 rounded-full bg-zinc-100">
                  <div className="h-1.5 rounded-full bg-zinc-800" style={{ width: `${Math.round(o.score * 100)}%` }} />
                </div>
                <span className="text-zinc-600">punteggio {num(o.score * 100, 0)}</span>
              </div>
              <div className="text-zinc-500">
                tendenza {o.direction_hint === "LONG" ? "rialzista" : o.direction_hint === "SHORT" ? "ribassista" : "assente"} · allineamento {num(o.alignment)}
              </div>
            </>
          ) : null}
          <div>{stato}</div>
        </div>
        <TesiBreve t={tesi} />
      </div>
    </li>
  );
}

export default async function Mercati() {
  const { d, errore } = await carica();
  if (!d) return <BackendGiu errore={errore} />;
  const c = d.controls;
  const perSimbolo = Object.fromEntries(d.opportunities.map((o) => [o.symbol, o]));
  const simboli = Object.keys(c?.symbols ?? perSimbolo);
  const ordinati = [...simboli].sort((a, b) => (perSimbolo[b]?.score ?? -1) - (perSimbolo[a]?.score ?? -1));

  return (
    <main>
      <BarraStato d={d} />
      <TitoloPagina
        titolo="Mercati e strategie"
        descrizione="Decidi su quali strumenti e con quali strategie il sistema può aprire posizioni. Spegnere un mercato non chiude le posizioni già aperte."
      />
      <div className="grid gap-3 md:grid-cols-2">
        {STRATEGIE.map((s) => (
          <div key={s.id} className="flex items-start gap-4 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
            <Interruttore
              attivo={c?.strategies[s.id] ?? true}
              path="controls/strategies"
              chiave="book"
              id={s.id}
              etichetta={`Strategia ${s.nome}`}
              nome={`Strategia ${s.nome}`}
            />
            <div>
              <div className="font-semibold">Strategia {s.nome}</div>
              <p className="mt-0.5 text-sm text-zinc-500">{s.descrizione}</p>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4">
        <Card titolo="Strumenti" sottotitolo="Ordinati per punteggio dello scanner: tendenza, allineamento dei timeframe, costo dello spread.">
          {simboli.length === 0 ? (
            <Vuoto titolo="In attesa della prima scansione" />
          ) : (
            <ul className="divide-y divide-zinc-100">
              {ordinati.map((s) => (
                <Riga key={s} simbolo={s} attivo={c?.symbols[s] ?? true} o={perSimbolo[s]} tesi={d.ai_theses[perSimbolo[s]?.canonical ?? s]} />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </main>
  );
}
