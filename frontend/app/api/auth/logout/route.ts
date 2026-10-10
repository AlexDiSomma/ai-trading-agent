/** Uscita: il cookie di sessione viene cancellato. */

import { NextResponse } from "next/server";

import { COOKIE, stessaOrigine } from "@/lib/sessione";

export async function POST(request: Request) {
  if (!stessaOrigine(request)) {
    return NextResponse.json({ detail: "Richiesta non valida." }, { status: 403 });
  }
  const risposta = NextResponse.json({ ok: true });
  risposta.cookies.set(COOKIE, "", { httpOnly: true, sameSite: "strict", path: "/", maxAge: 0 });
  return risposta;
}
