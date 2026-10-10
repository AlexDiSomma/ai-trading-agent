/** Formattazione condivisa: stessi numeri, stesso aspetto, in tutta la dashboard. */

export const num = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : v.toLocaleString("it-IT", { minimumFractionDigits: d, maximumFractionDigits: d });

export const soldi = (v: number | null | undefined, valuta = "EUR", segno = false) => {
  if (v === null || v === undefined) return "—";
  const s = v.toLocaleString("it-IT", { style: "currency", currency: valuta, maximumFractionDigits: 2 });
  return segno && v > 0 ? `+${s}` : s;
};

export const erre = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${num(v, 2)} R`;

export const prezzo = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : Number(v.toPrecision(7)).toString();

export const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `${num(v, d)}%`);

export const quando = (s: string | null | undefined) =>
  s
    ? new Date(s).toLocaleString("it-IT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
    : "—";

export function fa(s: string | null | undefined): string {
  if (!s) return "mai";
  const sec = Math.round((Date.now() - Date.parse(s)) / 1000);
  if (sec < 0) return `tra ${Math.round(-sec / 60)} min`;
  if (sec < 60) return `${sec} s fa`;
  if (sec < 3600) return `${Math.round(sec / 60)} min fa`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h fa`;
  return `${Math.round(sec / 86400)} g fa`;
}

export const tonoValore = (v: number | null | undefined) =>
  v === null || v === undefined || v === 0 ? "text-zinc-600" : v > 0 ? "text-emerald-600" : "text-rose-600";

/** Nomi leggibili dei limiti di rischio. */
export const ETICHETTE_RISCHIO: Record<string, { label: string; hint: string; unit?: string }> = {
  max_risk_per_trade_pct: { label: "Rischio per operazione", hint: "Perdita massima se scatta lo stop, sull'equity", unit: "%" },
  max_open_risk_pct: { label: "Rischio aperto totale", hint: "Somma delle perdite allo stop delle posizioni aperte", unit: "%" },
  max_open_positions: { label: "Posizioni contemporanee", hint: "Per ciascun conto (QUANT e AI)" },
  daily_max_loss_pct: { label: "Stop loss giornaliero", hint: "Raggiunto: nessuna nuova apertura fino a domani", unit: "%" },
  weekly_max_loss_pct: { label: "Stop loss settimanale", hint: "Raggiunto: nessuna nuova apertura fino a lunedì", unit: "%" },
  max_drawdown_pct: { label: "Drawdown massimo", hint: "Calo dal picco dell'equity che ferma le aperture", unit: "%" },
  daily_profit_target_pct: { label: "Take profit giornaliero", hint: "Raggiunto: stop alle aperture fino a domani (vuoto = nessuno)", unit: "%" },
  minimum_rr: { label: "Rendimento/rischio minimo", hint: "Target diviso stop: sotto, l'operazione è scartata" },
  min_stop_distance_atr: { label: "Stop minimo (ATR)", hint: "Distanza minima dello stop, in volatilità H1" },
  max_stop_distance_atr: { label: "Stop massimo (ATR)", hint: "Distanza massima dello stop, in volatilità H1" },
  max_spread_atr_ratio: { label: "Spread massimo (ATR)", hint: "Oltre, lo strumento è troppo caro per entrare" },
  max_position_size_lots: { label: "Lotti massimi", hint: "Tetto alla dimensione di una posizione" },
  allowed_sessions_raw: { label: "Orari consentiti (ora broker)", hint: "Es. 00:00-23:59 oppure 08:00-12:00,14:00-17:30" },
  allowed_weekdays_raw: { label: "Giorni consentiti", hint: "0 = lunedì … 4 = venerdì" },
};

/** Nomi leggibili degli strumenti: "XAUUSD" è l'oro, ma non tutti lo sanno a colpo d'occhio. */
export const NOMI_STRUMENTI: Record<string, string> = {
  XAUUSD: "Oro", XAGUSD: "Argento", US100: "Nasdaq 100", US500: "S&P 500", US30: "Dow Jones",
  GER40: "DAX 40", UK100: "FTSE 100", JP225: "Nikkei 225", OIL: "Petrolio",
  EURUSD: "Euro / Dollaro", GBPUSD: "Sterlina / Dollaro", USDJPY: "Dollaro / Yen",
  EURJPY: "Euro / Yen", GBPJPY: "Sterlina / Yen", AUDUSD: "Dollaro australiano / USD",
  USDCHF: "Dollaro / Franco", USDCAD: "Dollaro / Dollaro canadese",
  GOLD: "Oro", SILVER: "Argento", US100Cash: "Nasdaq 100", US500Cash: "S&P 500", US30Cash: "Dow Jones",
};

export const nomeStrumento = (codice: string) => NOMI_STRUMENTI[codice] ?? codice;

export const NOMI_ASSET: Record<string, string> = {
  USD: "Dollaro", EUR: "Euro", GBP: "Sterlina", JPY: "Yen", CHF: "Franco", AUD: "Dollaro AU",
  NZD: "Dollaro NZ", CAD: "Dollaro CA", XAU: "Oro", XAG: "Argento", OIL: "Petrolio", EQUITY: "Azionario",
};
