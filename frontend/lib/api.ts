/**
 * Accesso al backend.
 *
 * Due scelte di sicurezza:
 *
 * 1. **Il token sta sul server, non nel browser.** Viene letto da `API_TOKEN`
 *    (senza prefisso `NEXT_PUBLIC_`), quindi queste funzioni girano solo nei
 *    componenti server. Un token in un bundle JavaScript è un token pubblico.
 * 2. **Nessuna cache.** Lo stato del sistema e i prezzi non si memorizzano:
 *    mostrare un dato vecchio come se fosse attuale è il difetto che questa
 *    dashboard esiste per evitare.
 */

import type { Dashboard, RispostaNotizie } from "@/types/dashboard";
import type { SystemHealth } from "@/types/health";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function fetchApi<T>(path: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = process.env.API_TOKEN;
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const risposta = await fetch(`${API_URL}${path}`, {
    headers,
    cache: "no-store",
  });

  // 503 non è un errore da nascondere: è lo stato del sistema, e il corpo
  // contiene la spiegazione. Va mostrato, non trasformato in "errore generico".
  if (!risposta.ok && risposta.status !== 503) {
    const testo = await risposta.text().catch(() => "");
    throw new ApiError(
      `Il backend ha risposto ${risposta.status}. ${testo}`.trim(),
      risposta.status,
    );
  }

  return (await risposta.json()) as T;
}

/** Stato del sistema. Risponde anche quando i componenti sono fuori uso. */
export async function getSystemHealth(): Promise<SystemHealth> {
  return fetchApi<SystemHealth>("/api/health");
}

/** Tutto ciò che la dashboard mostra, in una lettura. */
export async function getDashboard(): Promise<Dashboard> {
  return fetchApi<Dashboard>("/api/dashboard");
}

/** Notizie filtrate (parametri: ore, impatto, asset, confermate). */
export async function getNews(parametri: Record<string, string>): Promise<RispostaNotizie> {
  return fetchApi<RispostaNotizie>(`/api/news?${new URLSearchParams(parametri).toString()}`);
}

export { API_URL };
