/** Performance: i due conti paper a confronto, con i numeri che contano. */

import { BackendGiu, BarraStato } from "@/components/BarraStato";
import { EquityChart } from "@/components/EquityChart";
import { Badge, Card, TitoloPagina, tonoLibro } from "@/components/ui";
import { carica } from "@/lib/carica";
import { erre, num, pct, soldi, tonoValore } from "@/lib/format";
import type { Libro } from "@/types/dashboard";

export const dynamic = "force-dynamic";

const VERDETTI: Record<string, string> = {
  INSUFFICIENT_SAMPLE: "Troppo presto per dirlo: servono almeno 30 trade chiusi per conto.",
  AI_BETTER: "L'AI sta rendendo più della regola fissa, per trade.",
  QUANT_BETTER_OR_EQUAL: "La regola fissa rende quanto o più dell'AI, per trade.",
};

function Riga({ e, v, tono = "", aiuto }: { e: string; v: string; tono?: string; aiuto?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5" title={aiuto}>
      <dt className="text-sm text-zinc-500">{e}</dt>
      <dd className={`text-sm font-medium ${tono}`}>{v}</dd>
    </div>
  );
}

function Conto({ nome, b, valuta }: { nome: string; b: Libro; valuta: string }) {
  const p = b.performance;
  return (
    <Card titolo={<span className="flex items-center gap-2">Conto <Badge tono={tonoLibro(nome)}>{nome}</Badge></span>} sottotitolo={`partenza ${soldi(p.initial_balance, valuta)}`}>
      <div className="mb-3 flex items-baseline gap-3">
        <span className="text-2xl font-semibold">{soldi(b.equity_now, valuta)}</span>
        <span className={`text-sm font-medium ${tonoValore(p.return_pct)}`}>{p.return_pct > 0 ? "+" : ""}{pct(p.return_pct, 2)}</span>
      </div>
      <dl className="divide-y divide-zinc-100">
        <Riga e="Trade chiusi" v={`${p.trades} (${p.wins} vinti, ${p.losses} persi)`} />
        <Riga e="Win rate" v={p.win_rate === null ? "—" : pct(p.win_rate * 100)} aiuto="Quota di trade chiusi in profitto" />
        <Riga e="Profit factor" v={num(p.profit_factor)} aiuto="Guadagni lordi divisi per perdite lorde: sopra 1 il conto guadagna" />
        <Riga e="Expectancy" v={`${erre(p.expectancy_r)} · ${soldi(p.expectancy_ccy, valuta, true)}`} tono={tonoValore(p.expectancy_r)} aiuto="Risultato medio per trade: la misura principale" />
        <Riga e="Totale" v={`${erre(p.total_r)} · ${soldi(p.net_pnl, valuta, true)}`} tono={tonoValore(p.net_pnl)} />
        <Riga e="Drawdown massimo" v={`${soldi(p.max_drawdown_ccy, valuta)} (${pct(p.max_drawdown_pct, 2)})`} aiuto="Il calo peggiore dal picco" />
        <Riga e="Durata media" v={p.mean_hours === null ? "—" : `${num(p.mean_hours, 1)} h`} />
        <Riga e="Escursione media contro / a favore" v={`${erre(p.mean_mae_r)} / ${erre(p.mean_mfe_r)}`} aiuto="Quanto il prezzo è andato contro e a favore prima dell'uscita" />
        <Riga e="Uscite" v={Object.entries(p.exit_reasons).map(([k, n]) => `${k} ${n}`).join(" · ") || "—"} />
        <Riga e="Costi pagati" v={soldi(p.costs.spread + p.costs.slippage + p.costs.commission - p.costs.swap, valuta)} aiuto={`spread ${p.costs.spread} · slittamento ${p.costs.slippage} · commissioni ${p.costs.commission} · swap ${p.costs.swap}`} />
      </dl>
    </Card>
  );
}

function Misure({ nome, b }: { nome: string; b: Libro }) {
  const o = b.performance.trailing_shadow;
  const c = b.performance.correlation;
  return (
    <Card titolo={<span className="flex items-center gap-2">Misure in ombra <Badge tono={tonoLibro(nome)}>{nome}</Badge></span>} sottotitolo="Cosa sarebbe successo con regole diverse, sugli stessi trade. Non cambiano nulla.">
      <h3 className="text-sm font-semibold">Stop mobile</h3>
      {!o || o.trades === 0 ? (
        <p className="mt-1 text-sm text-zinc-500">Nessun trade misurato ancora: la misura parte dai trade aperti dopo il 7 ottobre.</p>
      ) : (
        <>
          <p className="mt-1 text-xs text-zinc-500">
            Su {o.trades} trade, uscita reale {erre(o.actual_r)} in totale (R di prezzo, senza costi in valuta).
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-zinc-500">
                  <th className="py-1 pr-3 font-medium">Regola</th>
                  <th className="py-1 pr-3 text-right font-medium">Totale</th>
                  <th className="py-1 pr-3 text-right font-medium">Rispetto al reale</th>
                  <th className="py-1 text-right font-medium">Meglio / peggio</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100">
                {Object.entries(o.rules).map(([id, r]) => (
                  <tr key={id} title={r.description}>
                    <td className="py-1.5 pr-3">{r.name}</td>
                    <td className="py-1.5 pr-3 text-right">{erre(r.total_r)}</td>
                    <td className={`py-1.5 pr-3 text-right font-medium ${tonoValore(r.difference_r)}`}>{r.difference_r > 0 ? "+" : ""}{erre(r.difference_r)}</td>
                    <td className="py-1.5 text-right text-zinc-600">{r.better} / {r.worse}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <h3 className="mt-4 text-sm font-semibold">Posizioni correlate</h3>
      {!c || b.performance.trades === 0 ? (
        <p className="mt-1 text-sm text-zinc-500">Nessun trade chiuso.</p>
      ) : (
        <dl className="mt-1 divide-y divide-zinc-100">
          <Riga
            e="Aperti con la stessa scommessa già in corso"
            v={`${c.correlated.trades} trade · ${erre(c.correlated.total_r)}`}
            tono={tonoValore(c.correlated.total_r)}
            aiuto={`Stessa scommessa = stesso gruppo (${c.groups.join(", ")}) e stesso verso di rischio`}
          />
          <Riga e="Indipendenti" v={`${c.independent.trades} trade · ${erre(c.independent.total_r)}`} tono={tonoValore(c.independent.total_r)} />
          <Riga
            e="Con «una posizione per scommessa»"
            v={erre(c.without_correlated_total_r)}
            aiuto="Stima: toglie i trade correlati, non sa cosa sarebbe successo al loro posto"
          />
        </dl>
      )}
    </Card>
  );
}

export default async function Performance() {
  const { d, errore } = await carica();
  if (!d) return <BackendGiu errore={errore} />;
  const valuta = d.account?.currency ?? "EUR";
  const c = d.comparison;
  const iniziale = Object.values(d.books)[0]?.performance.initial_balance;
  return (
    <main>
      <BarraStato d={d} />
      <TitoloPagina titolo="Performance" descrizione="Due conti paper con lo stesso capitale e le stesse regole di rischio: uno guidato dall'AI, uno da una regola fissa. Il confronto dice se l'AI aggiunge valore." />
      {c && (
        <div className="mb-4 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
          <div className="text-xs font-medium text-zinc-500">AI contro QUANT</div>
          <p className="mt-1 text-base font-medium">{VERDETTI[c.verdict] ?? c.verdict}</p>
          <p className="mt-1 text-sm text-zinc-500">
            Differenza di expectancy: <b className={tonoValore(c.expectancy_r_difference)}>{erre(c.expectancy_r_difference)}</b> per trade ·
            trade chiusi AI {c.ai.trades ?? 0}, QUANT {c.quant.trades ?? 0}
          </p>
        </div>
      )}
      <Card titolo="Andamento">
        <EquityChart serie={Object.fromEntries(Object.entries(d.books).map(([n, b]) => [n, b.performance.equity_curve]))} iniziale={iniziale} altezza={260} />
      </Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {(["AI", "QUANT"] as const).map((n) => d.books[n] && <Conto key={n} nome={n} b={d.books[n]} valuta={valuta} />)}
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {(["AI", "QUANT"] as const).map((n) => d.books[n] && <Misure key={n} nome={n} b={d.books[n]} />)}
      </div>
      <p className="mt-4 text-xs text-zinc-400">
        R = risultato in multipli del rischio iniziale (1R = la perdita se scatta lo stop). Tutti i risultati sono netti di spread, slittamento simulato, commissioni e swap.
      </p>
    </main>
  );
}
