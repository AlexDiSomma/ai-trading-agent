"use client";

/**
 * Componenti interattivi della dashboard: notifiche, conferme, azioni, interruttori.
 *
 * Ogni azione passa da `/api/action` (il token resta sul server), mostra uno stato
 * di attesa, un esito leggibile e poi aggiorna i dati della pagina.
 */

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useTransition,
  type ReactNode,
} from "react";

// ---------------------------------------------------------------- notifiche
type Notifica = { id: number; testo: string; tipo: "ok" | "errore" };
const ContestoNotifiche = createContext<(testo: string, tipo?: "ok" | "errore") => void>(() => {});

export function Notifiche({ children }: { children: ReactNode }) {
  const [lista, setLista] = useState<Notifica[]>([]);
  const mostra = useCallback((testo: string, tipo: "ok" | "errore" = "ok") => {
    const id = Date.now() + Math.random();
    setLista((l) => [...l, { id, testo, tipo }]);
    setTimeout(() => setLista((l) => l.filter((n) => n.id !== id)), tipo === "errore" ? 7000 : 3500);
  }, []);
  return (
    <ContestoNotifiche.Provider value={mostra}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-20 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2 md:bottom-4">
        {lista.map((n) => (
          <div
            key={n.id}
            className={`pointer-events-auto rounded-lg px-4 py-3 text-sm shadow-lg ring-1 ${
              n.tipo === "ok" ? "bg-zinc-900 text-white ring-zinc-900" : "bg-rose-50 text-rose-900 ring-rose-200"
            }`}
          >
            {n.testo}
          </div>
        ))}
      </div>
    </ContestoNotifiche.Provider>
  );
}

export const useNotifica = () => useContext(ContestoNotifiche);

// ---------------------------------------------------------------- chiamata alle azioni
export async function eseguiAzione(path: string, body: unknown): Promise<{ ok: boolean; dati: unknown; errore?: string }> {
  try {
    const r = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, body }),
    });
    const dati = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = (dati as { detail?: unknown }).detail;
      const errore = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg).join("; ") : `Errore ${r.status}`;
      return { ok: false, dati, errore };
    }
    return { ok: true, dati };
  } catch {
    return { ok: false, dati: null, errore: "Backend non raggiungibile." };
  }
}

// ---------------------------------------------------------------- conferma
export function Conferma({
  aperta, titolo, testo, conferma = "Conferma", pericolosa = false, onConferma, onAnnulla, children,
}: {
  aperta: boolean; titolo: string; testo?: ReactNode; conferma?: string; pericolosa?: boolean;
  onConferma: () => void; onAnnulla: () => void; children?: ReactNode;
}) {
  const bottone = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!aperta) return;
    bottone.current?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onAnnulla();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [aperta, onAnnulla]);
  if (!aperta) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-4 sm:items-center" onClick={onAnnulla}>
      <div role="dialog" aria-modal="true" aria-label={titolo} className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-base font-semibold text-zinc-900">{titolo}</h3>
        {testo && <div className="mt-2 text-sm text-zinc-600">{testo}</div>}
        {children}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onAnnulla} className="rounded-lg px-3 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-100">Annulla</button>
          <button
            ref={bottone}
            onClick={onConferma}
            className={`rounded-lg px-3 py-2 text-sm font-semibold text-white ${pericolosa ? "bg-rose-600 hover:bg-rose-700" : "bg-zinc-900 hover:bg-zinc-800"}`}
          >
            {conferma}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- pulsante d'azione
const STILI = {
  primario: "bg-zinc-900 text-white hover:bg-zinc-800",
  secondario: "bg-white text-zinc-800 ring-1 ring-inset ring-zinc-300 hover:bg-zinc-50",
  pericolo: "bg-rose-600 text-white hover:bg-rose-700",
  tenue: "text-zinc-600 hover:bg-zinc-100",
};

export function Azione({
  path, body, children, variante = "secondario", conferma, okMessaggio, campoRisultato, piccolo = false, className = "",
}: {
  path: string; body?: unknown; children: ReactNode; variante?: keyof typeof STILI;
  conferma?: { titolo: string; testo?: ReactNode; bottone?: string; pericolosa?: boolean };
  okMessaggio?: string;
  /** Aggiunge all'esito il campo della risposta: `result` (testo) o `passed` (prova riuscita). */
  campoRisultato?: "result" | "passed";
  piccolo?: boolean; className?: string;
}) {
  const router = useRouter();
  const notifica = useNotifica();
  const [aperta, setAperta] = useState(false);
  const [inCorso, avvia] = useTransition();

  const esegui = () => {
    setAperta(false);
    avvia(async () => {
      const r = await eseguiAzione(path, body);
      if (r.ok) {
        const dati = (r.dati ?? {}) as { result?: string; passed?: boolean };
        if (campoRisultato === "passed" && dati.passed === false) {
          notifica("Prova non superata: controlla i dettagli nella pagina.", "errore");
        } else {
          const extra = campoRisultato === "result" && dati.result ? `: ${dati.result}` : "";
          notifica(`${okMessaggio ?? "Fatto"}${extra}`);
        }
        router.refresh();
      } else {
        notifica(r.errore ?? "Operazione non riuscita.", "errore");
      }
    });
  };

  return (
    <>
      <button
        disabled={inCorso}
        onClick={() => (conferma ? setAperta(true) : esegui())}
        className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition disabled:opacity-50 ${
          piccolo ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm"
        } ${STILI[variante]} ${className}`}
      >
        {inCorso && <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}
        {children}
      </button>
      {conferma && (
        <Conferma
          aperta={aperta}
          titolo={conferma.titolo}
          testo={conferma.testo}
          conferma={conferma.bottone}
          pericolosa={conferma.pericolosa}
          onConferma={esegui}
          onAnnulla={() => setAperta(false)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------- interruttore
export function Interruttore({
  attivo, path, chiave, id, etichetta, nome,
}: { attivo: boolean; path: string; chiave: string; id: string; etichetta: string; nome: string }) {
  const router = useRouter();
  const notifica = useNotifica();
  const [valore, setValore] = useState(attivo);
  const [inCorso, avvia] = useTransition();
  useEffect(() => setValore(attivo), [attivo]);

  const cambia = () => {
    const nuovo = !valore;
    setValore(nuovo); // ottimistico
    avvia(async () => {
      const r = await eseguiAzione(path, { [chiave]: id, enabled: nuovo });
      if (r.ok) {
        notifica(`${nome} ${nuovo ? "attivato" : "disattivato"}`);
        router.refresh();
      } else {
        setValore(!nuovo);
        notifica(r.errore ?? "Operazione non riuscita.", "errore");
      }
    });
  };

  return (
    <button
      role="switch"
      aria-checked={valore}
      aria-label={etichetta}
      disabled={inCorso}
      onClick={cambia}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition disabled:opacity-60 ${valore ? "bg-emerald-500" : "bg-zinc-300"}`}
    >
      <span className={`inline-block h-5 w-5 transform rounded-full bg-[#fff] shadow transition ${valore ? "translate-x-5" : "translate-x-0.5"}`} />
    </button>
  );
}
