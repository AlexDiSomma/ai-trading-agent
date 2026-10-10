/**
 * Notizie: solo quelle che possono muovere i mercati, da fonti filtrate.
 *
 * Ogni notizia dice da dove viene (ufficiale, testata, sito specializzato), se è
 * confermata da più editori, se è una voce non verificata, che impatto può avere e su
 * cosa. In cima, i freni: notizie che stanno impedendo nuove aperture.
 */

import Link from "next/link";

import { BackendGiu, BarraStato } from "@/components/BarraStato";
import { Badge, Card, TitoloPagina, Vuoto, type Tono } from "@/components/ui";
import { getNews } from "@/lib/api";
import { carica } from "@/lib/carica";
import { fa, NOMI_ASSET, nomeStrumento, quando } from "@/lib/format";
import type { Notizia, RispostaNotizie } from "@/types/dashboard";

export const dynamic = "force-dynamic";

const LIVELLI: Record<string, { testo: string; tono: Tono }> = {
  UFFICIALE: { testo: "Fonte ufficiale", tono: "verde" },
  TESTATA: { testo: "Testata", tono: "blu" },
  SPECIALIZZATA: { testo: "Sito specializzato", tono: "grigio" },
};
const IMPATTI: Record<string, { testo: string; tono: Tono }> = {
  HIGH: { testo: "Impatto alto", tono: "rosso" },
  MEDIUM: { testo: "Impatto medio", tono: "giallo" },
  LOW: { testo: "Impatto basso", tono: "grigio" },
};

type Filtri = { ore: string; impatto: string; asset: string; confermate: string };

function Chip({ attivo, href, children }: { attivo: boolean; href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      scroll={false}
      className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition ${
        attivo ? "bg-zinc-900 text-white ring-zinc-900" : "bg-white text-zinc-600 ring-zinc-300 hover:bg-zinc-50"
      }`}
    >
      {children}
    </Link>
  );
}

function url(f: Filtri, cambio: Partial<Filtri>) {
  const p = new URLSearchParams({ ...f, ...cambio });
  for (const [k, v] of [...p.entries()]) if (!v) p.delete(k);
  return `/notizie?${p.toString()}`;
}

function Scheda({ n }: { n: Notizia }) {
  const livello = LIVELLI[n.level] ?? LIVELLI.SPECIALIZZATA;
  const impatto = IMPATTI[n.impact] ?? IMPATTI.LOW;
  return (
    <article className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Badge tono={impatto.tono}>{impatto.testo}</Badge>
        <Badge tono={livello.tono}>{livello.testo}</Badge>
        {n.confirmed ? (
          <Badge tono="verde">{n.level === "UFFICIALE" ? "Fonte primaria" : `Confermata da ${n.publishers.length} editori`}</Badge>
        ) : (
          <Badge>Fonte singola</Badge>
        )}
        {n.unverified && <Badge tono="giallo">Voce non verificata</Badge>}
        <span className="ml-auto text-zinc-400" title={quando(n.published_at)}>
          {n.source} · {fa(n.published_at)}
        </span>
      </div>
      {n.summary_it ? (
        <>
          <p className="mt-2 text-sm font-medium text-zinc-900">{n.summary_it}</p>
          <a href={n.url} target="_blank" rel="noopener noreferrer" className="mt-1 block text-xs text-zinc-500 hover:text-zinc-800 hover:underline">
            {n.title} ↗
          </a>
        </>
      ) : (
        <a href={n.url} target="_blank" rel="noopener noreferrer" className="mt-2 block text-sm font-medium text-zinc-900 hover:underline">
          {n.title} ↗
        </a>
      )}
      {n.why && <p className="mt-2 text-xs text-zinc-500">{n.why}</p>}
      {n.assets.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {n.assets.map((a) => (
            <span key={a} className="rounded-md bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-600">{NOMI_ASSET[a] ?? a}</span>
          ))}
        </div>
      )}
    </article>
  );
}

export default async function Notizie({ searchParams }: { searchParams: Promise<Partial<Filtri>> }) {
  const { d, errore } = await carica();
  if (!d) return <BackendGiu errore={errore} />;
  const p = await searchParams;
  const f: Filtri = { ore: p.ore ?? "24", impatto: p.impatto ?? "MEDIUM", asset: p.asset ?? "", confermate: p.confermate ?? "" };
  let r: RispostaNotizie | null = null;
  let problema: string | null = null;
  try {
    r = await getNews({ ore: f.ore, impatto: f.impatto, ...(f.asset ? { asset: f.asset } : {}), confermate: f.confermate ? "true" : "false" });
  } catch (e) {
    problema = e instanceof Error ? e.message : "Notizie non disponibili.";
  }
  const freni = Object.entries(r?.brakes ?? {});
  const fonti = Object.entries(r?.sources ?? {});
  const fontiOk = fonti.filter(([, s]) => s.ok).length;

  return (
    <main>
      <BarraStato d={d} />
      <TitoloPagina
        titolo="Notizie"
        descrizione="Solo notizie che possono muovere forex, oro e indici, da banche centrali, testate riconosciute e siti forex. L'AI le riassume e ne stima l'impatto; il sistema le usa come freno, mai come motivo per entrare."
      />
      {problema && <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">{problema}</div>}
      {freni.length > 0 && (
        <div className="mb-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
          <b>Freni attivi:</b> nessuna nuova apertura per {r?.brake_minutes} minuti su
          <ul className="mt-1 space-y-0.5">
            {freni.map(([s, motivi]) => <li key={s}><b>{nomeStrumento(s)}</b> — {motivi[0]}</li>)}
          </ul>
        </div>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className="rounded-xl border border-zinc-200 bg-white p-3 shadow-sm"><div className="text-xs text-zinc-500">Rilevanti tenute</div><div className="text-xl font-semibold">{r?.stats.total_kept ?? "—"}</div></div>
        <div className="rounded-xl border border-zinc-200 bg-white p-3 shadow-sm"><div className="text-xs text-zinc-500">Alto impatto (24 h)</div><div className="text-xl font-semibold">{r?.stats.high_24h ?? "—"}</div></div>
        <div className="rounded-xl border border-zinc-200 bg-white p-3 shadow-sm"><div className="text-xs text-zinc-500">Scartate come irrilevanti</div><div className="text-xl font-semibold">{r?.stats.discarded_irrelevant ?? "—"}</div></div>
        <div className="rounded-xl border border-zinc-200 bg-white p-3 shadow-sm"><div className="text-xs text-zinc-500">Fonti raggiungibili</div><div className="text-xl font-semibold">{fonti.length ? `${fontiOk}/${fonti.length}` : "—"}</div><div className="text-[11px] text-zinc-400">aggiornate {fa(r?.stats.last_collection)}</div></div>
      </div>

      <div className="mb-4 space-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs font-medium text-zinc-500">Impatto</span>
          <Chip attivo={f.impatto === "LOW"} href={url(f, { impatto: "LOW" })}>Tutte</Chip>
          <Chip attivo={f.impatto === "MEDIUM"} href={url(f, { impatto: "MEDIUM" })}>Medio e alto</Chip>
          <Chip attivo={f.impatto === "HIGH"} href={url(f, { impatto: "HIGH" })}>Solo alto</Chip>
          <span className="mx-2 h-4 w-px bg-zinc-200" />
          <Chip attivo={!!f.confermate} href={url(f, { confermate: f.confermate ? "" : "1" })}>Solo confermate</Chip>
          <span className="mx-2 h-4 w-px bg-zinc-200" />
          {["24", "48", "72"].map((o) => <Chip key={o} attivo={f.ore === o} href={url(f, { ore: o })}>{o} h</Chip>)}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs font-medium text-zinc-500">Riguarda</span>
          <Chip attivo={!f.asset} href={url(f, { asset: "" })}>Tutto</Chip>
          {["USD", "EUR", "GBP", "JPY", "XAU", "XAG", "EQUITY", "OIL"].map((a) => (
            <Chip key={a} attivo={f.asset === a} href={url(f, { asset: a })}>{NOMI_ASSET[a] ?? a}</Chip>
          ))}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-2">
          {!r || r.items.length === 0 ? (
            <Vuoto titolo="Nessuna notizia con questi filtri">
              Le fonti si leggono ogni 10 minuti. Le notizie senza un tema di mercato (cronaca, consigli, sport) vengono scartate.
            </Vuoto>
          ) : (
            r.items.map((n) => <Scheda key={n.id} n={n} />)
          )}
        </div>
        <Card titolo="Fonti" sottotitolo="Gratuite, lette ogni 10 minuti">
          <ul className="space-y-2 text-sm">
            {fonti.map(([nome, s]) => (
              <li key={nome} className="flex items-center justify-between gap-2" title={s.error}>
                <span className="flex items-center gap-2">
                  <span className={`h-2 w-2 rounded-full ${s.ok ? "bg-emerald-500" : "bg-rose-500"}`} />
                  {nome}
                </span>
                <span className="text-xs text-zinc-400">{LIVELLI[s.level]?.testo}</span>
              </li>
            ))}
            {fonti.length === 0 && <li className="text-zinc-500">Prima lettura in corso…</li>}
          </ul>
          <p className="mt-4 text-xs text-zinc-500">
            Una notizia è <b>confermata</b> se viene da una fonte ufficiale o se la riportano almeno due editori indipendenti. Solo le notizie ad alto impatto, confermate e non classificate come voci, fermano le nuove aperture.
          </p>
        </Card>
      </div>
    </main>
  );
}
