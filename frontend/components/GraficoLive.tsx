"use client";

/**
 * Grafico a candele in tempo reale (Lightweight Charts, Apache 2.0).
 *
 * Primo caricamento: 300 candele. Poi, a intervalli che dipendono dal timeframe,
 * solo le ultime 3, aggiornate sul posto: la candela in formazione si muove davanti
 * agli occhi. Le posizioni paper aperte sono linee (ingresso, stop, target); i trade
 * chiusi sono marcatori. Orari mostrati nel fuso del browser.
 */

import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { nomeStrumento } from "@/lib/format";

type Barra = { time: number; open: number; high: number; low: number; close: number };
type Posizione = { book: string; status: string; direction: string; entry: number; stop: number; target: number; pnl: number | null };
type TradeChiuso = { book: string; direction: string; opened: number; closed: number; entry: number; exit: number; reason: string; r: number; pnl: number };
type Risposta = {
  symbol: string; timeframe: string; digits: number; bars: Barra[]; last_bar_forming: boolean;
  utc_times: boolean; quote: { bid: number; ask: number } | null; positions: Posizione[]; trades: TradeChiuso[];
  detail?: string;
};

const TIMEFRAME: { id: string; label: string; ogni: number }[] = [
  { id: "M1", label: "1m", ogni: 2000 },
  { id: "M5", label: "5m", ogni: 3000 },
  { id: "M15", label: "15m", ogni: 5000 },
  { id: "H1", label: "1h", ogni: 10000 },
  { id: "H4", label: "4h", ogni: 15000 },
  { id: "D1", label: "1D", ogni: 30000 },
];

function colore(variabile: string, alfa = 1): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(variabile).trim();
  return v ? `rgb(${v} / ${alfa})` : "#888";
}

function stile() {
  return {
    layout: { background: { color: "transparent" }, textColor: colore("--zinc-500") },
    grid: { vertLines: { color: colore("--zinc-200", 0.5) }, horzLines: { color: colore("--zinc-200", 0.5) } },
    rightPriceScale: { borderColor: colore("--zinc-200") },
    timeScale: { borderColor: colore("--zinc-200") },
    crosshair: { vertLine: { labelBackgroundColor: colore("--zinc-700") }, horzLine: { labelBackgroundColor: colore("--zinc-700") } },
  };
}

const oraLocale = (t: Time) =>
  new Date((t as number) * 1000).toLocaleString("it-IT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/** Allinea un istante all'inizio della candela che lo contiene (i marcatori stanno sulle candele). */
function allinea(t: number, barre: Barra[]): number | null {
  let lo = 0, hi = barre.length - 1, ris: number | null = null;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (barre[m].time <= t) { ris = barre[m].time; lo = m + 1; } else hi = m - 1;
  }
  return ris;
}

export function GraficoLive({ simboli, simboloIniziale, tfIniziale }: { simboli: { symbol: string; canonical: string }[]; simboloIniziale: string; tfIniziale: string }) {
  const router = useRouter();
  const percorso = usePathname();
  const contenitore = useRef<HTMLDivElement>(null);
  const grafico = useRef<IChartApi | null>(null);
  const serie = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const marcatori = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const linee = useRef<IPriceLine[]>([]);
  const barre = useRef<Barra[]>([]);
  const firmaSovrapposizioni = useRef("");

  const [simbolo, setSimbolo] = useState(simboloIniziale);
  const [tf, setTf] = useState(tfIniziale);
  const [dati, setDati] = useState<Risposta | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [aggiornato, setAggiornato] = useState<number>(0);

  // --- creazione del grafico, una volta ---
  useEffect(() => {
    if (!contenitore.current) return;
    const c = createChart(contenitore.current, {
      autoSize: true,
      ...stile(),
      localization: { locale: "it-IT", timeFormatter: oraLocale },
      timeScale: {
        ...stile().timeScale,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (t: Time) => new Date((t as number) * 1000).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }),
      },
    });
    const s = c.addSeries(CandlestickSeries, {
      upColor: colore("--emerald-500"), downColor: colore("--rose-500"),
      borderUpColor: colore("--emerald-500"), borderDownColor: colore("--rose-500"),
      wickUpColor: colore("--emerald-500"), wickDownColor: colore("--rose-500"),
    });
    grafico.current = c;
    serie.current = s;
    marcatori.current = createSeriesMarkers(s, []);
    const tema = () => c.applyOptions(stile());
    window.addEventListener("tema-cambiato", tema);
    return () => {
      window.removeEventListener("tema-cambiato", tema);
      c.remove();
      grafico.current = null;
      serie.current = null;
    };
  }, []);

  const disegnaSovrapposizioni = useCallback((r: Risposta) => {
    const s = serie.current;
    if (!s) return;
    const firma = JSON.stringify([r.positions, r.trades.map((t) => t.closed)]);
    if (firma === firmaSovrapposizioni.current) return;
    firmaSovrapposizioni.current = firma;
    linee.current.forEach((l) => s.removePriceLine(l));
    linee.current = [];
    for (const p of r.positions) {
      const etichetta = `${p.book} ${p.direction === "LONG" ? "acq." : "vend."}`;
      linee.current.push(
        s.createPriceLine({ price: p.entry, color: colore("--zinc-500"), lineWidth: 1, lineStyle: 2, title: `${etichetta} ingresso` }),
        s.createPriceLine({ price: p.stop, color: colore("--rose-500"), lineWidth: 1, lineStyle: 0, title: `${p.book} SL` }),
        s.createPriceLine({ price: p.target, color: colore("--emerald-500"), lineWidth: 1, lineStyle: 0, title: `${p.book} TP` }),
      );
    }
    const m: SeriesMarker<Time>[] = [];
    for (const t of r.trades) {
      const ingresso = allinea(t.opened, barre.current);
      const uscita = allinea(t.closed, barre.current);
      const lungo = t.direction === "LONG";
      if (ingresso !== null) {
        m.push({ time: ingresso as UTCTimestamp, position: lungo ? "belowBar" : "aboveBar", shape: lungo ? "arrowUp" : "arrowDown", color: colore(t.book === "AI" ? "--violet-500" : "--sky-500"), text: t.book });
      }
      if (uscita !== null) {
        m.push({ time: uscita as UTCTimestamp, position: lungo ? "aboveBar" : "belowBar", shape: "circle", color: colore(t.r >= 0 ? "--emerald-500" : "--rose-500"), text: `${t.r >= 0 ? "+" : ""}${t.r.toFixed(1)}R` });
      }
    }
    m.sort((a, b) => (a.time as number) - (b.time as number));
    marcatori.current?.setMarkers(m);
  }, []);

  // --- caricamento completo al cambio di strumento o timeframe, poi aggiornamenti ---
  useEffect(() => {
    let attivo = true;
    let timer: ReturnType<typeof setTimeout>;
    const intervallo = TIMEFRAME.find((x) => x.id === tf)?.ogni ?? 5000;
    firmaSovrapposizioni.current = "";
    barre.current = [];

    const carica = async (completo: boolean) => {
      try {
        const r = await fetch(`/api/chart?symbol=${encodeURIComponent(simbolo)}&tf=${tf}&count=${completo ? 300 : 3}`, { cache: "no-store" });
        const d = (await r.json()) as Risposta;
        if (!attivo) return;
        if (!r.ok) throw new Error(d.detail ?? `Errore ${r.status}`);
        const s = serie.current;
        if (s) {
          if (completo || barre.current.length === 0) {
            s.applyOptions({ priceFormat: { type: "price", precision: d.digits, minMove: 1 / 10 ** d.digits } });
            barre.current = d.bars;
            s.setData(d.bars as CandlestickData<Time>[]);
            grafico.current?.timeScale().scrollToRealTime();
          } else {
            for (const b of d.bars) {
              const ultima = barre.current[barre.current.length - 1];
              if (!ultima || b.time >= ultima.time) {
                s.update(b as CandlestickData<Time>);
                if (ultima && b.time === ultima.time) barre.current[barre.current.length - 1] = b;
                else barre.current.push(b);
              }
            }
          }
          disegnaSovrapposizioni(d);
        }
        setDati(d);
        setErrore(null);
        setAggiornato(Date.now());
      } catch (e) {
        if (attivo) setErrore(e instanceof Error ? e.message : "Dati non disponibili.");
      } finally {
        if (attivo) timer = setTimeout(() => carica(false), intervallo);
      }
    };
    carica(true);
    return () => {
      attivo = false;
      clearTimeout(timer);
    };
  }, [simbolo, tf, disegnaSovrapposizioni]);

  const cambia = (s: string, t: string) => {
    setSimbolo(s);
    setTf(t);
    router.replace(`${percorso}?s=${encodeURIComponent(s)}&tf=${t}`, { scroll: false });
  };

  // --- intestazione: prezzo, variazione, stato del flusso ---
  const ultime = dati?.bars ?? [];
  const ultima = barre.current[barre.current.length - 1] ?? ultime[ultime.length - 1];
  const precedente = barre.current[barre.current.length - 2];
  const variazione = ultima && precedente ? ((ultima.close - precedente.close) / precedente.close) * 100 : null;
  const inRitardo = aggiornato > 0 && Date.now() - aggiornato > 3 * (TIMEFRAME.find((x) => x.id === tf)?.ogni ?? 5000);
  const cifre = dati?.digits ?? 5;

  return (
    <div className="rounded-xl border border-zinc-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-zinc-100 px-4 py-3">
        <select
          value={simbolo}
          onChange={(e) => cambia(e.target.value, tf)}
          aria-label="Strumento"
          className="rounded-lg border border-zinc-300 bg-white px-2.5 py-1.5 text-sm font-semibold"
        >
          {simboli.map((s) => (
            <option key={s.symbol} value={s.symbol}>{nomeStrumento(s.canonical)} · {s.canonical}</option>
          ))}
        </select>
        <div role="radiogroup" aria-label="Timeframe" className="inline-flex rounded-lg bg-zinc-100 p-0.5">
          {TIMEFRAME.map((x) => (
            <button
              key={x.id}
              role="radio"
              aria-checked={tf === x.id}
              onClick={() => cambia(simbolo, x.id)}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${tf === x.id ? "bg-white text-zinc-900 shadow-sm" : "text-zinc-500 hover:text-zinc-800"}`}
            >
              {x.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-4 text-sm">
          {ultima && (
            <span className="font-semibold tabular-nums">
              {ultima.close.toFixed(cifre)}
              {variazione !== null && (
                <span className={`ml-2 text-xs font-medium ${variazione >= 0 ? "text-emerald-600" : "text-rose-600"}`}>
                  {variazione >= 0 ? "+" : ""}{variazione.toFixed(2)}%
                </span>
              )}
            </span>
          )}
          {dati?.quote && (
            <span className="hidden text-xs text-zinc-500 sm:inline tabular-nums">
              bid {dati.quote.bid.toFixed(cifre)} · ask {dati.quote.ask.toFixed(cifre)}
            </span>
          )}
          <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500" title={errore ?? undefined}>
            <span className={`h-2 w-2 rounded-full ${errore ? "bg-rose-500" : inRitardo ? "bg-amber-500" : "animate-pulse bg-emerald-500"}`} />
            {errore ? "interrotto" : inRitardo ? "in ritardo" : "live"}
          </span>
        </div>
      </div>
      {errore && <div className="border-b border-rose-100 bg-rose-50 px-4 py-2 text-xs text-rose-800">{errore} · nuovo tentativo in corso</div>}
      <div ref={contenitore} className="h-[60vh] min-h-[320px] w-full md:h-[520px]" />
      <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-zinc-100 px-4 py-2.5 text-xs text-zinc-500">
        {dati && dati.positions.length > 0 ? (
          dati.positions.map((p, i) => (
            <span key={i}>
              <b className="text-zinc-700">{p.book}</b> {p.direction === "LONG" ? "acquisto" : "vendita"} {p.status === "PENDING" ? "(in attesa)" : ""} · ingresso {p.entry.toFixed(cifre)} · SL {p.stop.toFixed(cifre)} · TP {p.target.toFixed(cifre)}
              {p.pnl !== null && <b className={p.pnl >= 0 ? " text-emerald-600" : " text-rose-600"}> {p.pnl >= 0 ? "+" : ""}{p.pnl.toFixed(2)} €</b>}
            </span>
          ))
        ) : (
          <span>Nessuna posizione aperta su questo strumento.</span>
        )}
        <span className="ml-auto">
          ▲▼ ingressi · ● uscite (in R) · orari locali{dati && !dati.utc_times ? " (ora broker: scarto non ancora misurato)" : ""}
        </span>
      </div>
    </div>
  );
}
