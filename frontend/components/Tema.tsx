"use client";

/**
 * Scelta del tema: chiaro, scuro o come il sistema. Preferenza del singolo browser
 * (localStorage): non riguarda il sistema di trading né gli altri dispositivi.
 */

import { useEffect, useState } from "react";

type Scelta = "chiaro" | "scuro" | "sistema";
const CHIAVE = "tema";

function leggi(): Scelta {
  try {
    const v = localStorage.getItem(CHIAVE);
    return v === "chiaro" || v === "scuro" ? v : "sistema";
  } catch {
    return "sistema";
  }
}

export function applicaTema(scelta: Scelta) {
  const scuro = scelta === "scuro" || (scelta === "sistema" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", scuro);
  window.dispatchEvent(new Event("tema-cambiato"));
}

/** Script eseguito prima del disegno della pagina: evita il lampo di tema sbagliato. */
export const SCRIPT_TEMA = `(function(){try{var v=localStorage.getItem('${CHIAVE}');var s=v==='scuro'||((v===null||v==='sistema')&&window.matchMedia('(prefers-color-scheme: dark)').matches);if(s)document.documentElement.classList.add('dark');}catch(e){}})();`;

export function SceltaTema({ compatta = false }: { compatta?: boolean }) {
  const [scelta, setScelta] = useState<Scelta>("sistema");
  useEffect(() => {
    setScelta(leggi());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const segui = () => leggi() === "sistema" && applicaTema("sistema");
    mq.addEventListener("change", segui);
    return () => mq.removeEventListener("change", segui);
  }, []);
  const scegli = (s: Scelta) => {
    setScelta(s);
    try {
      localStorage.setItem(CHIAVE, s);
    } catch {
      /* preferenza non salvabile: vale solo per questa sessione */
    }
    applicaTema(s);
  };
  const voci: [Scelta, string, string][] = [
    ["chiaro", "☀", "Chiaro"],
    ["scuro", "☾", "Scuro"],
    ["sistema", "◐", "Sistema"],
  ];
  return (
    <div role="radiogroup" aria-label="Tema" className="inline-flex rounded-lg bg-zinc-100 p-0.5">
      {voci.map(([v, icona, nome]) => (
        <button
          key={v}
          role="radio"
          aria-checked={scelta === v}
          title={nome}
          onClick={() => scegli(v)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
            scelta === v ? "bg-white text-zinc-900 shadow-sm" : "text-zinc-500 hover:text-zinc-800"
          }`}
        >
          <span aria-hidden>{icona}</span>
          {!compatta && <span className="ml-1">{nome}</span>}
        </button>
      ))}
    </div>
  );
}
