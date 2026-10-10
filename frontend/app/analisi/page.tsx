/** Analisi: il quadro globale del motore e la lettura dell'AI, con i limiti dei dati. */

import { BackendGiu, BarraStato } from "@/components/BarraStato";
import { Badge, Card, NOMI_REGIME, TitoloPagina, tonoQualita, tonoRegime, Vuoto } from "@/components/ui";
import { carica } from "@/lib/carica";
import { fa, num, quando } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Analisi() {
  const { d, errore } = await carica();
  if (!d) return <BackendGiu errore={errore} />;
  const g = d.global;
  const a = d.ai_analysis;
  return (
    <main>
      <BarraStato d={d} />
      <TitoloPagina titolo="Analisi del mercato" descrizione="Il regime lo calcola il motore quantitativo; l'AI lo commenta, costruisce scenari e dichiara cosa non sa." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card titolo="Quadro globale" sottotitolo={g ? `motore · ${fa(g.as_of)}` : undefined}>
          {!g ? <Vuoto titolo="In attesa della prima scansione" /> : (
            <div className="space-y-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tono={tonoRegime(g.regime)}>{NOMI_REGIME[g.regime] ?? g.regime}</Badge>
                <Badge tono={tonoQualita(g.data_quality)}>dati {g.data_quality}</Badge>
                <Badge>{g.market_phase}</Badge>
              </div>
              <ul className="list-disc space-y-1 pl-5 text-zinc-700">{g.regime_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
              <div>
                <div className="mb-1 text-xs font-medium text-zinc-500">Forza delle valute</div>
                <div className="space-y-1">
                  {g.currency_strength.map((c) => (
                    <div key={c.currency} className="flex items-center gap-2">
                      <span className="w-10 font-medium">{c.currency}</span>
                      <div className="relative h-2 flex-1 rounded-full bg-zinc-100">
                        <span className="absolute left-1/2 top-0 h-2 w-px bg-zinc-300" />
                        <div
                          className={`absolute top-0 h-2 rounded-full ${c.score >= 0 ? "bg-emerald-500" : "bg-rose-500"}`}
                          style={c.score >= 0 ? { left: "50%", width: `${Math.min(50, c.score * 20)}%` } : { right: "50%", width: `${Math.min(50, -c.score * 20)}%` }}
                        />
                      </div>
                      <span className="w-12 text-right text-xs text-zinc-500">{num(c.score)}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {g.source_quality.map((q) => (
                  <Badge key={q.domain} tono={q.configured ? tonoQualita(q.quality) : "grigio"}>{q.domain}: {q.configured ? q.quality : "non configurata"}</Badge>
                ))}
              </div>
              {g.next_events.length > 0 && (
                <div>
                  <div className="mb-1 text-xs font-medium text-zinc-500">Eventi economici</div>
                  <ul className="space-y-0.5 text-xs">{g.next_events.map((e, i) => <li key={i}><b>{e.currency}</b> {e.title} · {quando(e.scheduled_at)} · {e.phase}</li>)}</ul>
                </div>
              )}
            </div>
          )}
        </Card>
        <Card titolo="Lettura dell'AI" sottotitolo={a ? `${a.model} · ${fa(a.at)}` : undefined}>
          {!a ? <Vuoto titolo="Nessuna analisi ancora">Si rinnova ogni ora.</Vuoto> : a.status !== "AVAILABLE" ? (
            <p className="text-sm text-amber-700">Analisi non disponibile: {a.failure_reasons.join("; ")}</p>
          ) : (
            <div className="space-y-4 text-sm">
              <p className="leading-relaxed text-zinc-800">{a.summary}</p>
              {a.themes.length > 0 && (
                <div className="space-y-2">
                  {a.themes.map((t) => <div key={t.title}><b>{t.title}.</b> <span className="text-zinc-600">{t.description}</span></div>)}
                </div>
              )}
              <div>
                <div className="mb-1 text-xs font-medium text-zinc-500">Scenari</div>
                {a.scenarios.map((s) => (
                  <div key={s.name} className="mb-2">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-16 rounded-full bg-zinc-100"><div className="h-1.5 rounded-full bg-violet-500" style={{ width: `${s.likelihood * 100}%` }} /></div>
                      <b>{s.name}</b> <span className="text-xs text-zinc-500">{num(s.likelihood * 100, 0)}% · {s.horizon_hours} h</span>
                    </div>
                    <p className="text-xs text-zinc-600">{s.narrative}</p>
                  </div>
                ))}
              </div>
              <p className="text-xs text-zinc-400">Le percentuali sono stime qualitative del modello, non probabilità calibrate.</p>
            </div>
          )}
        </Card>
      </div>
      {(g?.known_limitations.length || a?.uncertainties.length) ? (
        <div className="mt-4">
          <Card titolo="Cosa il sistema non vede" sottotitolo="I limiti dei dati disponibili, dichiarati invece che nascosti">
            <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-600">
              {g?.known_limitations.map((l, i) => <li key={`l${i}`}>{l}</li>)}
              {a?.uncertainties.map((u, i) => <li key={`u${i}`}>{u.description}{u.missing ? ` — manca: ${u.missing}` : ""}</li>)}
            </ul>
          </Card>
        </div>
      ) : null}
    </main>
  );
}
