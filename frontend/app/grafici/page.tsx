/** Grafici in tempo reale degli strumenti autorizzati, con posizioni e trade del sistema. */

import { BackendGiu, BarraStato } from "@/components/BarraStato";
import { GraficoLive } from "@/components/GraficoLive";
import { TitoloPagina } from "@/components/ui";
import { carica } from "@/lib/carica";

export const dynamic = "force-dynamic";

export default async function Grafici({ searchParams }: { searchParams: Promise<{ s?: string; tf?: string }> }) {
  const { d, errore } = await carica();
  if (!d) return <BackendGiu errore={errore} />;
  const parametri = await searchParams;
  const canonici = Object.fromEntries(d.opportunities.map((o) => [o.symbol, o.canonical]));
  const elenco = Object.keys(d.controls?.symbols ?? canonici).map((s) => ({ symbol: s, canonical: canonici[s] ?? s }));
  // Di default lo strumento con una posizione aperta, altrimenti il primo in classifica.
  const conPosizione = Object.values(d.books).flatMap((b) => b.open_positions)[0]?.plan.broker_symbol;
  const iniziale = parametri.s && elenco.some((x) => x.symbol === parametri.s) ? parametri.s : conPosizione ?? d.opportunities[0]?.symbol ?? elenco[0]?.symbol;
  const tf = ["M1", "M5", "M15", "H1", "H4", "D1"].includes(parametri.tf ?? "") ? parametri.tf! : "M5";

  return (
    <main>
      <BarraStato d={d} />
      <TitoloPagina titolo="Grafici" descrizione="Candele in tempo reale da XM. Le linee sono le posizioni paper aperte (ingresso, stop, target), i marcatori i trade chiusi." />
      {iniziale ? (
        <GraficoLive simboli={elenco} simboloIniziale={iniziale} tfIniziale={tf} />
      ) : (
        <p className="text-sm text-zinc-500">Nessuno strumento disponibile.</p>
      )}
    </main>
  );
}
