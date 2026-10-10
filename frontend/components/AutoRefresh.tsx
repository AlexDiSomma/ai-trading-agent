"use client";

/** Ricarica i dati della pagina (componenti server) a intervalli regolari. */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export function AutoRefresh({ secondi = 15 }: { secondi?: number }) {
  const router = useRouter();
  const [ultimo, setUltimo] = useState<string>("");

  useEffect(() => {
    setUltimo(new Date().toLocaleTimeString("it-IT"));
    const id = setInterval(() => {
      router.refresh();
      setUltimo(new Date().toLocaleTimeString("it-IT"));
    }, secondi * 1000);
    return () => clearInterval(id);
  }, [router, secondi]);

  return (
    <span className="text-xs text-zinc-500">
      aggiornamento ogni {secondi} s · ultimo {ultimo}
    </span>
  );
}
