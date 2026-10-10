"use client";

/** Modulo di accesso: errori chiari, stato di attesa, ritorno alla pagina richiesta. */

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

export function ModuloLogin({ destinazione, configurata }: { destinazione: string; configurata: boolean }) {
  const router = useRouter();
  const [utente, setUtente] = useState("");
  const [password, setPassword] = useState("");
  const [ricordami, setRicordami] = useState(false);
  const [mostra, setMostra] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  const [attesa, setAttesa] = useState(false);

  const invia = async (e: FormEvent) => {
    e.preventDefault();
    setAttesa(true);
    setErrore(null);
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ utente, password, ricordami }),
      });
      if (r.ok) {
        router.replace(destinazione.startsWith("/") && !destinazione.startsWith("//") ? destinazione : "/");
        router.refresh();
        return;
      }
      const d = (await r.json().catch(() => ({}))) as { detail?: string };
      setErrore(d.detail ?? "Accesso non riuscito.");
      setPassword("");
    } catch {
      setErrore("La dashboard non risponde.");
    } finally {
      setAttesa(false);
    }
  };

  if (!configurata) {
    return (
      <div className="space-y-3 text-sm text-zinc-600">
        <p>L&apos;accesso non è ancora configurato, quindi la dashboard resta chiusa.</p>
        <p>Nella cartella <code className="rounded bg-zinc-100 px-1">frontend</code> del progetto esegui:</p>
        <pre className="rounded-lg bg-zinc-900 px-3 py-2 text-xs text-zinc-100">npm run imposta-password</pre>
        <p>Scegli nome utente e password, poi riavvia con <code className="rounded bg-zinc-100 px-1">avvia.bat</code>.</p>
      </div>
    );
  }

  const campo = "mt-1 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200";
  return (
    <form onSubmit={invia} className="space-y-4">
      <label className="block">
        <span className="text-sm font-medium text-zinc-800">Nome utente</span>
        <input value={utente} onChange={(e) => setUtente(e.target.value)} autoComplete="username" autoFocus required className={campo} />
      </label>
      <label className="block">
        <span className="text-sm font-medium text-zinc-800">Password</span>
        <div className="relative">
          <input
            type={mostra ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            className={`${campo} pr-16`}
          />
          <button type="button" onClick={() => setMostra((m) => !m)} className="absolute right-2 top-1/2 mt-0.5 -translate-y-1/2 rounded px-2 py-1 text-xs text-zinc-500 hover:text-zinc-900">
            {mostra ? "Nascondi" : "Mostra"}
          </button>
        </div>
      </label>
      <label className="flex items-center gap-2 text-sm text-zinc-600">
        <input type="checkbox" checked={ricordami} onChange={(e) => setRicordami(e.target.checked)} className="h-4 w-4 rounded border-zinc-300" />
        Ricordami su questo dispositivo per 30 giorni
      </label>
      {errore && <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">{errore}</p>}
      <button type="submit" disabled={attesa} className="w-full rounded-lg bg-zinc-900 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-zinc-800 disabled:opacity-50">
        {attesa ? "Verifica…" : "Accedi"}
      </button>
    </form>
  );
}
