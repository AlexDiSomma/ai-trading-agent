/** Panoramica: in un colpo d'occhio, cosa sta facendo il sistema e come sta andando. */

import Link from "next/link";

import { BackendGiu, BarraStato } from "@/components/BarraStato";
import { EquityChart } from "@/components/EquityChart";
import { ElencoPosizioni } from "@/components/Posizioni";
import { Badge, Card, NOMI_REGIME, Stat, TitoloPagina, tonoQualita, tonoRegime, Vuoto } from "@/components/ui";
import { carica } from "@/lib/carica";
import { erre, fa, nomeStrumento, num, quando, soldi, tonoValore } from "@/lib/format";
import type { Dashboard } from "@/types/dashboard";

export const dynamic = "force-dynamic";

const ESITI: Record<string, { testo: string; tono: string }> = {
  WAIT: { testo: "attende", tono: "text-zinc-500" },
  REJECTED: { testo: "scartato", tono: "text-amber-700" },
  MARKET: { testo: "posizione aperta", tono: "text-emerald-700" },
  STOP: { testo: "ordine stop piazzato", tono: "text-emerald-700" },
  LIMIT: { testo: "ordine limit piazzato", tono: "text-emerald-700" },
  PAUSED: { testo: "in pausa", tono: "text-amber-700" },
  SKIPPED: { testo: "saltato", tono: "text-zinc-500" },
};

function Attivita({ d }: { d: Dashboard }) {
  const righe = d.decisions.filter((x) => x.symbol !== "—").slice(0, 10);
  if (!righe.length) return <Vuoto titolo="Nessuna decisione ancora">La prima scansione arriva entro pochi minuti dall&apos;avvio.</Vuoto>;
  return (
    <ol className="space-y-3">
      {righe.map((x, i) => {
        const e = ESITI[x.outcome] ?? { testo: x.outcome.toLowerCase(), tono: "text-zinc-600" };
        return (
          <li key={i} className="flex gap-3">
            <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-zinc-300" />
            <div className="min-w-0">
              <p className="text-sm">
                <b>{nomeStrumento(x.symbol)}</b> <span className="text-zinc-400">·</span> {x.book}{" "}
                <span className={`font-medium ${e.tono}`}>{e.testo}</span>
                {x.direction && <span className="text-zinc-500"> {x.direction === "LONG" ? "in acquisto" : "in vendita"}</span>}
                {x.rr ? <span className="text-zinc-500"> · RR {num(x.rr)}</span> : null}
              </p>
              {x.reasons && x.reasons.length > 0 && <p className="line-clamp-2 text-xs text-zinc-500">{x.reasons[0]}</p>}
              <p className="text-[11px] text-zinc-400">{fa(x.at)}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export default async function Panoramica() {
  const { d, errore } = await carica();
  if (!d) return <BackendGiu errore={errore} />;
  const libri = d.books;
  const valuta = d.account?.currency ?? "EUR";
  const posizioni = Object.values(libri).flatMap((b) => b.open_positions);
  const oggi = new Date().toDateString();
  const chiusiOggi = Object.values(libri).flatMap((b) => b.closed_trades).filter((t) => new Date(t.closed_at).toDateString() === oggi);
  const pnlOggi = chiusiOggi.reduce((s, t) => s + t.net_pnl, 0) + posizioni.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
  const maxPos = Number(d.settings.risk.max_open_positions ?? 3);
  const g = d.global;
  const iniziale = Object.values(libri)[0]?.performance.initial_balance;

  return (
    <main>
      <BarraStato d={d} />
      <TitoloPagina titolo="Panoramica" descrizione="Il sistema analizza il mercato, decide e gestisce le posizioni da solo, su due conti paper separati: AI e QUANT (regola fissa)." />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {(["AI", "QUANT"] as const).map((nome) =>
          libri[nome] ? (
            <Stat
              key={nome}
              etichetta={`Conto ${nome}`}
              valore={soldi(libri[nome].equity_now, valuta)}
              tono={tonoValore(libri[nome].equity_now - libri[nome].performance.initial_balance)}
              nota={`${libri[nome].performance.trades} trade chiusi · ${erre(libri[nome].performance.total_r)}`}
            />
          ) : null,
        )}
        <Stat etichetta="Risultato di oggi" valore={soldi(pnlOggi, valuta, true)} tono={tonoValore(pnlOggi)} nota={`${chiusiOggi.length} chiusi oggi, più il non realizzato`} />
        <Stat etichetta="Posizioni aperte" valore={`${posizioni.length}`} nota={`massimo ${maxPos} per conto`} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card titolo="Il mercato adesso" sottotitolo={g ? `aggiornato ${fa(g.as_of)}` : undefined} azioni={<Link href="/analisi" className="text-xs font-medium text-zinc-600 hover:text-zinc-900">Dettagli →</Link>}>
          {!g ? (
            <Vuoto titolo="In attesa della prima scansione" />
          ) : (
            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Regime</span>
                <Badge tono={tonoRegime(g.regime)}>{NOMI_REGIME[g.regime] ?? g.regime}</Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Volatilità</span>
                <span>{g.volatility.replace("/", "·")}{g.vix ? ` · VIX ${num(g.vix, 1)}` : ""}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-zinc-500">Qualità dei dati</span>
                <Badge tono={tonoQualita(g.data_quality)}>{g.data_quality}</Badge>
              </div>
              <div>
                <span className="text-zinc-500">Valute più forti / deboli</span>
                <p className="mt-1">
                  <span className="text-emerald-700">{g.currency_strength.slice(0, 2).map((c) => c.currency).join(", ")}</span>
                  <span className="text-zinc-400"> / </span>
                  <span className="text-rose-700">{g.currency_strength.slice(-2).map((c) => c.currency).join(", ")}</span>
                </p>
              </div>
              {g.next_events.length > 0 && (
                <div>
                  <span className="text-zinc-500">Prossimi eventi importanti</span>
                  <ul className="mt-1 space-y-0.5 text-xs">
                    {g.next_events.slice(0, 3).map((e, i) => (
                      <li key={i}><b>{e.currency}</b> {e.title} · {quando(e.scheduled_at)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Card>
        <Card titolo="Cosa sta facendo" sottotitolo="Le ultime decisioni, con il motivo" className="lg:col-span-2">
          <Attivita d={d} />
        </Card>
      </div>

      {d.news_summary && (
        <div className="mt-4">
          <Card
            titolo="Notizie che contano"
            sottotitolo={`${d.news_summary.high_24h} ad alto impatto nelle ultime 24 ore`}
            azioni={<Link href="/notizie" className="text-xs font-medium text-zinc-600 hover:text-zinc-900">Tutte →</Link>}
          >
            {Object.keys(d.news_summary.brakes).length > 0 && (
              <p className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-900">
                <b>Freno attivo</b> su {Object.keys(d.news_summary.brakes).map(nomeStrumento).join(", ")}: nessuna nuova apertura per ora.
              </p>
            )}
            {d.news_summary.latest.length === 0 ? (
              <Vuoto titolo="Nessuna notizia rilevante nelle ultime ore" />
            ) : (
              <ul className="divide-y divide-zinc-100">
                {d.news_summary.latest.map((n) => (
                  <li key={n.id} className="flex items-start gap-3 py-2">
                    <Badge tono={n.impact === "HIGH" ? "rosso" : n.impact === "MEDIUM" ? "giallo" : "grigio"}>
                      {n.impact === "HIGH" ? "Alto" : n.impact === "MEDIUM" ? "Medio" : "Basso"}
                    </Badge>
                    <div className="min-w-0">
                      <p className="text-sm">{n.summary_it ?? n.title}</p>
                      <p className="text-[11px] text-zinc-400">{n.source} · {fa(n.published_at)}{n.confirmed ? " · confermata" : ""}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}

      <div className="mt-4">
        <Card titolo="Posizioni aperte" azioni={<Link href="/posizioni" className="text-xs font-medium text-zinc-600 hover:text-zinc-900">Tutte e storico →</Link>}>
          <ElencoPosizioni posizioni={posizioni} valuta={valuta} />
        </Card>
      </div>

      <div className="mt-4">
        <Card titolo="Andamento dei conti paper" azioni={<Link href="/performance" className="text-xs font-medium text-zinc-600 hover:text-zinc-900">Statistiche →</Link>}>
          <EquityChart serie={Object.fromEntries(Object.entries(libri).map(([n, b]) => [n, b.performance.equity_curve]))} iniziale={iniziale} />
        </Card>
      </div>
    </main>
  );
}
