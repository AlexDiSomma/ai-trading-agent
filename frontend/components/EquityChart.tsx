/**
 * Curva dell'equity in SVG, senza librerie. A gradini: l'equity realizzata cambia
 * solo quando un trade si chiude, e una linea obliqua fra due chiusure
 * suggerirebbe valori mai esistiti.
 */

import { Vuoto } from "@/components/ui";

const COLORI: Record<string, string> = { QUANT: "#0284c7", AI: "#7c3aed" };

export function EquityChart({
  serie,
  iniziale,
  altezza = 220,
}: {
  serie: Record<string, { t: string; equity: number }[]>;
  iniziale?: number;
  altezza?: number;
}) {
  const conPunti = Object.entries(serie).filter(([, p]) => p.length > 0);
  const tutte = conPunti.flatMap(([, p]) => p);
  if (tutte.length < 3) {
    return (
      <Vuoto titolo="La curva comparirà con i primi trade chiusi">
        Ogni chiusura aggiunge un gradino. Il punto di partenza è il saldo iniziale del conto paper.
      </Vuoto>
    );
  }
  const L = 800, H = altezza, PX = 48, PY = 16;
  const adesso = Date.now();
  const tempi = tutte.map((p) => Date.parse(p.t));
  const t0 = Math.min(...tempi);
  const t1 = Math.max(adesso, ...tempi);
  const valori = [...tutte.map((p) => p.equity), ...(iniziale ? [iniziale] : [])];
  const v0 = Math.min(...valori), v1 = Math.max(...valori);
  const m = (v1 - v0) * 0.15 || Math.max(1, v0 * 0.002);
  const lo = v0 - m, hi = v1 + m;
  const x = (t: number) => PX + ((t - t0) / (t1 - t0 || 1)) * (L - PX - 8);
  const y = (v: number) => PY + (1 - (v - lo) / (hi - lo)) * (H - 2 * PY);

  const gradini = (punti: { t: string; equity: number }[]) => {
    const o = [...punti].sort((a, b) => Date.parse(a.t) - Date.parse(b.t));
    const d: string[] = [`M ${x(Date.parse(o[0].t))} ${y(o[0].equity)}`];
    for (let i = 1; i < o.length; i++) {
      d.push(`H ${x(Date.parse(o[i].t))}`, `V ${y(o[i].equity)}`);
    }
    d.push(`H ${x(t1)}`);
    return d.join(" ");
  };
  const tacche = [lo + m, (lo + hi) / 2, hi - m];

  return (
    <div>
      <svg viewBox={`0 0 ${L} ${H}`} className="h-auto w-full" role="img" aria-label="Curva dell'equity dei conti paper">
        <defs>
          {conPunti.map(([nome]) => (
            <linearGradient key={nome} id={`g-${nome}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={COLORI[nome] ?? "#52525b"} stopOpacity="0.18" />
              <stop offset="100%" stopColor={COLORI[nome] ?? "#52525b"} stopOpacity="0" />
            </linearGradient>
          ))}
        </defs>
        {tacche.map((v) => (
          <g key={v}>
            <line x1={PX} x2={L - 8} y1={y(v)} y2={y(v)} className="stroke-zinc-100" />
            <text x={PX - 6} y={y(v) + 4} fontSize="11" className="fill-zinc-400" textAnchor="end">
              {Math.round(v).toLocaleString("it-IT")}
            </text>
          </g>
        ))}
        {iniziale && (
          <line x1={PX} x2={L - 8} y1={y(iniziale)} y2={y(iniziale)} className="stroke-zinc-300" strokeDasharray="4 4" />
        )}
        {conPunti.map(([nome, punti]) => {
          const d = gradini(punti);
          return (
            <g key={nome}>
              <path d={`${d} V ${H - PY} H ${x(Date.parse(punti[0].t))} Z`} fill={`url(#g-${nome})`} />
              <path d={d} fill="none" stroke={COLORI[nome] ?? "#52525b"} strokeWidth="2.25" strokeLinejoin="round" />
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex flex-wrap gap-4 text-xs text-zinc-600">
        {conPunti.map(([nome, punti]) => (
          <span key={nome} className="inline-flex items-center gap-1.5">
            <span className="h-2 w-4 rounded-full" style={{ background: COLORI[nome] ?? "#52525b" }} />
            {nome} · {punti[punti.length - 1].equity.toLocaleString("it-IT", { maximumFractionDigits: 2 })}
          </span>
        ))}
        {iniziale && <span className="inline-flex items-center gap-1.5 text-zinc-400">– – capitale iniziale</span>}
      </div>
    </div>
  );
}
