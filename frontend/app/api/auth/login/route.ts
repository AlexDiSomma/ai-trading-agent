/**
 * Login: confronto della password con la sua impronta (scrypt), in tempo costante.
 *
 * Protezioni: solo richieste dalla dashboard stessa; dopo 5 tentativi falliti da uno
 * stesso indirizzo, blocco di 15 minuti; risposta volutamente uguale per utente o
 * password sbagliati; piccolo ritardo su ogni errore.
 */

import { NextResponse } from "next/server";

import { confronta } from "@/scripts/password.mjs";
import { configurata, COOKIE, DURATA_ORE, DURATA_RICORDAMI_GIORNI, firma, stessaOrigine } from "@/lib/sessione";

export const runtime = "nodejs";

const MAX_TENTATIVI = 5;
const BLOCCO_MS = 15 * 60 * 1000;
const tentativi = new Map<string, { n: number; finoA: number }>();
// L'indirizzo viene da un'intestazione falsificabile: un limite globale impedisce di
// aggirare il blocco cambiandola a ogni tentativo.
const MAX_GLOBALI = 20;
let globali: { n: number; da: number; finoA: number } = { n: 0, da: Date.now(), finoA: 0 };

function indirizzo(request: Request): string {
  return request.headers.get("x-forwarded-for")?.split(",")[0].trim() || request.headers.get("x-real-ip") || "locale";
}

const attesa = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function POST(request: Request) {
  if (!stessaOrigine(request)) {
    return NextResponse.json({ detail: "Richiesta non valida." }, { status: 403 });
  }
  if (!configurata()) {
    return NextResponse.json(
      { detail: "Credenziali non ancora impostate. Nella cartella frontend esegui: npm run imposta-password" },
      { status: 503 },
    );
  }
  if (globali.finoA > Date.now()) {
    return NextResponse.json({ detail: "Troppi tentativi falliti: accesso sospeso per 15 minuti." }, { status: 429 });
  }
  const ip = indirizzo(request);
  const stato = tentativi.get(ip);
  if (stato && stato.finoA > Date.now()) {
    const minuti = Math.ceil((stato.finoA - Date.now()) / 60000);
    return NextResponse.json({ detail: `Troppi tentativi. Riprova fra ${minuti} minuti.` }, { status: 429 });
  }

  let corpo: { utente?: string; password?: string; ricordami?: boolean };
  try {
    corpo = await request.json();
  } catch {
    return NextResponse.json({ detail: "Richiesta non valida." }, { status: 400 });
  }
  const utenteOk = corpo.utente === process.env.DASHBOARD_USER;
  // Il confronto della password avviene sempre, anche con utente sbagliato: stessi tempi.
  const passwordOk = confronta(String(corpo.password ?? ""), process.env.DASHBOARD_PASSWORD_HASH);
  if (!utenteOk || !passwordOk) {
    const n = (stato?.finoA && stato.finoA <= Date.now() ? 0 : stato?.n ?? 0) + 1;
    tentativi.set(ip, { n, finoA: n >= MAX_TENTATIVI ? Date.now() + BLOCCO_MS : 0 });
    if (Date.now() - globali.da > BLOCCO_MS) globali = { n: 0, da: Date.now(), finoA: 0 };
    globali.n += 1;
    if (globali.n >= MAX_GLOBALI) globali.finoA = Date.now() + BLOCCO_MS;
    await attesa(600);
    const restanti = MAX_TENTATIVI - n;
    return NextResponse.json(
      { detail: restanti > 0 ? `Nome utente o password non corretti. Tentativi rimasti: ${restanti}.` : "Troppi tentativi: accesso bloccato per 15 minuti." },
      { status: 401 },
    );
  }
  tentativi.delete(ip);
  const durata = corpo.ricordami ? DURATA_RICORDAMI_GIORNI * 86400 : DURATA_ORE * 3600;
  const risposta = NextResponse.json({ ok: true });
  risposta.cookies.set(COOKIE, await firma(process.env.DASHBOARD_USER!, durata), {
    httpOnly: true,
    sameSite: "strict",
    secure: new URL(request.url).protocol === "https:",
    path: "/",
    maxAge: durata,
  });
  return risposta;
}
