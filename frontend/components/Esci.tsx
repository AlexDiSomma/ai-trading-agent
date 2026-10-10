"use client";

/** Chiude la sessione e torna alla pagina di accesso. */

import { useRouter } from "next/navigation";
import { useState } from "react";

export function Esci({ className = "" }: { className?: string }) {
  const router = useRouter();
  const [attesa, setAttesa] = useState(false);
  const esci = async () => {
    setAttesa(true);
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => null);
    router.replace("/login");
    router.refresh();
  };
  return (
    <button onClick={esci} disabled={attesa} className={`text-xs font-medium text-zinc-500 hover:text-zinc-900 disabled:opacity-50 ${className}`}>
      {attesa ? "Uscita…" : "Esci"}
    </button>
  );
}
