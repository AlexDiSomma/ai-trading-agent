/** Posizioni: aperte (con chiusura manuale) e storico dei trade chiusi. */

import { BackendGiu, BarraStato } from "@/components/BarraStato";
import { Azione } from "@/components/interattivi";
import { ElencoPosizioni } from "@/components/Posizioni";
import { StoricoTrade } from "@/components/StoricoTrade";
import { Card, TitoloPagina } from "@/components/ui";
import { carica } from "@/lib/carica";
import { quando } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Posizioni() {
  const { d, errore } = await carica();
  if (!d) return <BackendGiu errore={errore} />;
  const valuta = d.account?.currency ?? "EUR";
  const libri = Object.values(d.books);
  const aperte = libri.flatMap((b) => b.open_positions);
  const trades = libri.flatMap((b) => b.closed_trades).sort((a, b) => b.closed_at.localeCompare(a.closed_at));
  const rifiuti = Object.entries(d.books)
    .flatMap(([nome, b]) => b.rejections.map((r) => ({ ...r, book: nome })))
    .sort((a, b) => b.at.localeCompare(a.at))
    .slice(0, 12);

  return (
    <main>
      <BarraStato d={d} />
      <TitoloPagina
        titolo="Posizioni"
        descrizione="Stop e target sono gestiti in automatico. Puoi chiudere tu una posizione in qualsiasi momento: avviene al prezzo di mercato corrente."
        destra={
          aperte.length > 0 && (
            <Azione
              path="paper/close-all"
              body={{ book: null, confirm: true }}
              variante="pericolo"
              conferma={{
                titolo: `Chiudere tutte le ${aperte.length} posizioni?`,
                testo: "Tutte le posizioni e gli ordini paper in attesa vengono chiusi subito al prezzo corrente, anche se in perdita. Il sistema potrà riaprirne di nuove: per fermarlo usa la pausa o il kill switch.",
                bottone: "Chiudi tutto",
                pericolosa: true,
              }}
              okMessaggio="Posizioni chiuse."
            >
              Chiudi tutto
            </Azione>
          )
        }
      />
      <Card titolo={`Aperte e in attesa (${aperte.length})`}>
        <ElencoPosizioni posizioni={aperte} valuta={valuta} />
      </Card>
      <div className="mt-4">
        <Card titolo={`Storico (${trades.length})`} sottotitolo="Risultati netti: spread, slittamento, commissioni e swap inclusi">
          <StoricoTrade trades={trades} valuta={valuta} />
        </Card>
      </div>
      <div className="mt-4">
        <Card titolo="Operazioni scartate dal controllo di rischio" sottotitolo="Piani validi che non hanno superato i tuoi limiti: è il sistema che ti protegge">
          {rifiuti.length === 0 ? (
            <p className="text-sm text-zinc-500">Nessuno scarto recente.</p>
          ) : (
            <ul className="divide-y divide-zinc-100 text-sm">
              {rifiuti.map((r, i) => (
                <li key={i} className="py-2">
                  <span className="font-medium">{r.symbol}</span> <span className="text-xs text-zinc-500">{r.book} · {quando(r.at)}</span>
                  <p className="text-xs text-zinc-600">{r.reasons.join(" · ")}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </main>
  );
}
