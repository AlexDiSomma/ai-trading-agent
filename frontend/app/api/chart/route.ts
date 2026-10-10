/** Proxy dei dati del grafico: il token resta sul server. */

import { NextResponse } from "next/server";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const TIMEFRAME = new Set(["M1", "M5", "M15", "M30", "H1", "H4", "D1"]);

export async function GET(request: Request) {
  const url = new URL(request.url);
  const simbolo = url.searchParams.get("symbol") ?? "";
  const tf = url.searchParams.get("tf") ?? "M5";
  const count = Math.min(Math.max(Number(url.searchParams.get("count") ?? 300), 2), 1000);
  if (!/^[A-Za-z0-9._-]{2,32}$/.test(simbolo) || !TIMEFRAME.has(tf)) {
    return NextResponse.json({ detail: "Parametri non validi." }, { status: 400 });
  }
  const headers: Record<string, string> = { Accept: "application/json" };
  if (process.env.API_TOKEN) headers.Authorization = `Bearer ${process.env.API_TOKEN}`;
  try {
    const r = await fetch(`${API_URL}/api/chart/${encodeURIComponent(simbolo)}?timeframe=${tf}&count=${count}`, {
      headers,
      cache: "no-store",
    });
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json({ detail: "Backend non raggiungibile." }, { status: 502 });
  }
}
