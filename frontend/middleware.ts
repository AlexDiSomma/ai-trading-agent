/**
 * Ogni pagina e ogni API della dashboard richiede una sessione valida.
 *
 * Senza credenziali configurate (`npm run imposta-password`) la dashboard non si apre
 * a nessuno: fail closed. Restano libere solo la pagina di login, le sue API e i file
 * statici necessari a disegnarla.
 */

import { NextResponse, type NextRequest } from "next/server";

import { COOKIE, verifica } from "@/lib/sessione";

const LIBERI = ["/login", "/api/auth/login", "/api/auth/logout", "/icon.svg"];

export async function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (LIBERI.some((p) => pathname === p)) return NextResponse.next();
  const utente = await verifica(request.cookies.get(COOKIE)?.value);
  if (utente) return NextResponse.next();
  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ detail: "Sessione scaduta: accedi di nuovo." }, { status: 401 });
  }
  const login = new URL("/login", request.url);
  if (pathname !== "/") login.searchParams.set("da", pathname + search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
