/**
 * Proxy delle azioni della dashboard verso il backend.
 *
 * Il browser non vede mai il token: chiama questa route, che aggiunge
 * l'autorizzazione lato server. Solo i percorsi elencati sono inoltrabili.
 */

import { NextResponse } from "next/server";

import { stessaOrigine } from "@/lib/sessione";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

const CONSENTITI = new Set([
  "controls/pause",
  "controls/symbols",
  "controls/strategies",
  "controls/kill-switch",
  "controls/trailing",
  "controls/invert",
  "controls/quant-target",
  "controls/risk",
  "paper/close",
  "paper/close-all",
  "autopilot/scan",
  "live/drills/kill-switch",
]);

export async function POST(request: Request) {
  // Le azioni partono solo dalle pagine della dashboard (difesa contro richieste
  // da altri siti aperti nello stesso browser).
  if (!stessaOrigine(request)) {
    return NextResponse.json({ detail: "Richiesta non valida." }, { status: 403 });
  }
  let corpo: { path?: string; body?: unknown };
  try {
    corpo = await request.json();
  } catch {
    return NextResponse.json({ detail: "Richiesta non valida." }, { status: 400 });
  }
  if (!corpo.path || !CONSENTITI.has(corpo.path)) {
    return NextResponse.json({ detail: "Azione non consentita." }, { status: 403 });
  }
  const headers: Record<string, string> = { "Content-Type": "application/json", Accept: "application/json" };
  if (process.env.API_TOKEN) headers.Authorization = `Bearer ${process.env.API_TOKEN}`;
  try {
    const r = await fetch(`${API_URL}/api/${corpo.path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(corpo.body ?? {}),
      cache: "no-store",
    });
    const testo = await r.text();
    let dati: unknown = testo;
    try {
      dati = JSON.parse(testo);
    } catch {
      /* testo semplice */
    }
    return NextResponse.json(dati, { status: r.status });
  } catch {
    return NextResponse.json({ detail: "Backend non raggiungibile." }, { status: 502 });
  }
}
