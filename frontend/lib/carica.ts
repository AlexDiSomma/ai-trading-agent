/** Lettura dei dati della dashboard con un errore leggibile se il backend è giù. */

import { API_URL, ApiError, getDashboard } from "@/lib/api";
import type { Dashboard } from "@/types/dashboard";

export async function carica(): Promise<{ d: Dashboard | null; errore: string }> {
  try {
    return { d: await getDashboard(), errore: "" };
  } catch (e) {
    return { d: null, errore: e instanceof ApiError ? e.message : `Nessuna risposta da ${API_URL}.` };
  }
}
